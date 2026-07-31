from __future__ import annotations

from io import BytesIO
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from urllib import error

from models.composition import CompositionDocument, ContentBlock
from services.eddie_message_service import EddieMessageService
from services.image_service import DATA_ROOT
from services.providers.exceptions import sanitize_technical_detail
from services.whatsapp_config_service import WhatsAppConfig, WhatsAppConfigService
from services.whatsapp_service import (
    WhatsAppApiError,
    WhatsAppService,
    normalize_whatsapp_phone,
)


class FakeProtector:
    def protect(self, value: str) -> str:
        return f'protected:{value[::-1]}'

    def unprotect(self, value: str) -> str:
        return value.removeprefix('protected:')[::-1]


class FakeResponse:
    status = 200

    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode('utf-8')


class WhatsAppIntegrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='whatsapp_', dir=DATA_ROOT))
        self.config_path = self.root / 'whatsapp_config.local.json'
        self.config = WhatsAppConfig(
            provider='generic_json',
            base_url='https://api.example.com',
            send_endpoint='/messages/send',
            auth_scheme='bearer',
            sender_id='sender-1',
            instance_name='emc',
            timeout_seconds=15,
            encrypted_token='protected:terces',
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_missing_config_requires_configuration_screen_contract(self) -> None:
        source = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8-sig')
        self.assertIn("'Enviar pelo WhatsApp'", source)
        self.assertIn('open_whatsapp_config_dialog(status.message)', source)
        self.assertIn('open_whatsapp_send_dialog()', source)

    def test_configured_status_skips_configuration_when_valid(self) -> None:
        service = WhatsAppConfigService(self.config_path, protector=FakeProtector())
        saved = service.save(self.config, token='secret', mark_valid=True)
        status = service.status(saved)
        self.assertTrue(status.configured)
        self.assertTrue(status.valid)
        self.assertEqual(status.missing_fields, [])

    def test_token_is_masked_and_not_saved_plain(self) -> None:
        service = WhatsAppConfigService(self.config_path, protector=FakeProtector())
        saved = service.save(self.config, token='super-secret-token', mark_valid=True)
        raw = self.config_path.read_text(encoding='utf-8')
        self.assertNotIn('super-secret-token', raw)
        self.assertIn('encrypted_token', raw)
        self.assertEqual(service.decrypt_token(saved), 'super-secret-token')
        source = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8-sig')
        self.assertIn('TOKEN_PLACEHOLDER', source)

    def test_configuration_is_not_valid_until_connection_test_marks_it(self) -> None:
        service = WhatsAppConfigService(self.config_path, protector=FakeProtector())
        saved = service.save(self.config, token='secret')
        self.assertFalse(service.status(saved).valid)
        saved = service.save(self.config, token='secret', mark_valid=True)
        self.assertTrue(service.status(saved).valid)

    def test_test_connection_uses_configured_endpoint_and_timeout(self) -> None:
        calls = []

        def opener(req, timeout=20):
            calls.append((req, timeout))
            return FakeResponse({'ok': True})

        result = WhatsAppService(opener=opener).test_connection(self.config, 'secret')
        self.assertTrue(result.success)
        self.assertEqual(calls[0][0].full_url, 'https://api.example.com/messages/send')
        self.assertEqual(calls[0][0].headers['Authorization'], 'Bearer secret')
        self.assertEqual(calls[0][1], 15)

    def test_token_does_not_appear_in_sanitized_logs(self) -> None:
        detail = sanitize_technical_detail(
            'Authorization: Bearer abc123 access_token=abc123 token: abc123', 'abc123'
        )
        self.assertNotIn('abc123', detail)
        self.assertIn('[REDACTED]', detail)

    def test_recipient_is_normalized(self) -> None:
        self.assertEqual(normalize_whatsapp_phone('+55 (31) 99999-9999'), '5531999999999')
        self.assertEqual(normalize_whatsapp_phone('(31) 3333-4444'), '553133334444')

    def test_invalid_recipient_blocks_send(self) -> None:
        with self.assertRaises(WhatsAppApiError):
            normalize_whatsapp_phone('123')

    def test_eddie_message_uses_title_and_link_only_and_rejects_local_link(self) -> None:
        document = CompositionDocument(blocks=[
            ContentBlock(tipo='titulo', conteudo='Nova rotina fiscal'),
            ContentBlock(tipo='texto', papel='introducao', conteudo='Texto completo que nao deve ir no WhatsApp.'),
        ])
        message = EddieMessageService().build(
            document, public_link='https://novidades.emc.com.br/item/1'
        )
        self.assertEqual(message.title, 'Nova rotina fiscal')
        self.assertIn('Nova rotina fiscal', message.preview)
        self.assertIn('https://novidades.emc.com.br/item/1', message.preview)
        self.assertNotIn('Texto completo', message.preview)
        self.assertIn('Eu sou o Eddie', message.preview)
        with self.assertRaises(ValueError):
            EddieMessageService().build(document, public_link='http://localhost:8510')

    def test_generic_payload_uses_message_and_future_metadata(self) -> None:
        payload = WhatsAppService.build_message_payload(
            self.config,
            recipient='5531999999999',
            message='Mensagem editada',
            novelty_title='Titulo',
            novelty_link='https://novidades.emc.com.br/item/1',
        )
        self.assertEqual(payload['to'], '5531999999999')
        self.assertEqual(payload['message'], 'Mensagem editada')
        self.assertEqual(payload['sender_id'], 'sender-1')
        self.assertEqual(payload['instance_name'], 'emc')
        self.assertEqual(payload['novelty']['title'], 'Titulo')
        self.assertEqual(payload['novelty']['link'], 'https://novidades.emc.com.br/item/1')

    def test_timeout_and_auth_and_http_errors_are_friendly(self) -> None:
        def timeout_opener(*args, **kwargs):
            raise TimeoutError('timed out')

        service = WhatsAppService(opener=timeout_opener)
        with self.assertRaises(WhatsAppApiError) as timeout_ctx:
            service.test_connection(self.config, 'secret')
        self.assertIn('Tempo esgotado', timeout_ctx.exception.user_message)

        def http_opener(status: int, payload: dict):
            def open_request(req, timeout=20):
                raise error.HTTPError(
                    req.full_url, status, 'error', {}, BytesIO(json.dumps(payload).encode('utf-8'))
                )
            return open_request

        auth_service = WhatsAppService(opener=http_opener(401, {'error': {'code': 190, 'message': 'bad token'}}))
        with self.assertRaises(WhatsAppApiError) as auth_ctx:
            auth_service.test_connection(self.config, 'secret')
        self.assertIn('Token', auth_ctx.exception.user_message)

        http_service = WhatsAppService(opener=http_opener(500, {'error': {'message': 'server error'}}))
        with self.assertRaises(WhatsAppApiError) as http_ctx:
            http_service.send_message(
                self.config, 'secret', recipient='5531999999999', message='M', novelty_title='T', novelty_link='https://x.com/a'
            )
        self.assertIn('temporariamente indisponivel', http_ctx.exception.user_message)

    def test_success_returns_message_id_and_confirmation_guard_exists(self) -> None:
        calls = []

        def opener(req, timeout=20):
            calls.append(req)
            return FakeResponse({'message_id': 'wamid.test'})

        result = WhatsAppService(opener=opener).send_message(
            self.config,
            'secret',
            recipient='(31) 99999-9999',
            message='Mensagem editada',
            novelty_title='Titulo',
            novelty_link='https://novidades.emc.com.br/item/1',
        )
        self.assertEqual(result.message_id, 'wamid.test')
        self.assertEqual(len(calls), 1)
        request_payload = json.loads(calls[0].data.decode('utf-8'))
        self.assertEqual(request_payload['message'], 'Mensagem editada')
        source = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8-sig')
        self.assertIn("state['whatsapp_sending'] = True", source)
        self.assertIn("if state.get('whatsapp_sending')", source)
        self.assertIn('Confirmar envio pelo WhatsApp', source)
        self.assertIn('Editar mensagem', source)

    def test_nicegui_disconnected_client_guard_contract_exists(self) -> None:
        source = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8-sig')
        self.assertIn('safe_visual_update', source)
        self.assertIn('except RuntimeError', source)


if __name__ == '__main__':
    unittest.main()
