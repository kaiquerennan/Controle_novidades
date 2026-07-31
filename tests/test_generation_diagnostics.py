from __future__ import annotations

import logging
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from core.config import TEXT_MODELS
from pydantic import ValidationError

from models.novelty_plan import NoveltyPlan, PlannedBlock
from services.image_service import DATA_ROOT, SessionWorkspace
from services.novelty_generation_service import generate_automatic_novelty
from services.providers.base_provider import AIProvider
from services.providers.exceptions import ProviderError, map_provider_exception
from services.providers.openai_provider import OpenAIProvider


class FakeHttpError(Exception):
    def __init__(self, message: str, status_code: int, body: dict | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}


class FailingImageProvider(AIProvider):
    name = 'openai'

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def treat_image(self, **kwargs):
        raise ProviderError(
            'Falha simulada.',
            code='openai_quota_exceeded',
            technical_detail=f'Authorization: Bearer {self.api_key}',
            provider='openai',
            status_http=429,
            exception_type='RateLimitError',
        )

    def suggest_orientation(self, **kwargs):
        return ''

    def generate_novelty_plan(self, **kwargs):
        raise AssertionError('A análise não deve ser chamada após falha total.')


class GenerationDiagnosticsTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='generation_diagnostics_', dir=DATA_ROOT))
        self.workspace = SessionWorkspace(
            session_id='diagnostic-session',
            original_dir=self.root / 'originals',
            processed_dir=self.root / 'processed',
            export_dir=self.root / 'exports',
            temp_dir=self.root / 'temp',
        )
        for folder in (
            self.workspace.original_dir,
            self.workspace.processed_dir,
            self.workspace.export_dir,
            self.workspace.temp_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    async def run_sync(self, function, **kwargs):
        return function(**kwargs)

    def image(self, position: int = 1) -> dict:
        image_id = f'imagem_{position:03d}'
        original = self.workspace.original_dir / f'{image_id}.png'
        Image.new('RGB', (16, 12), (20, 40, 60)).save(original, 'PNG')
        return {
            'id': image_id,
            'original': original,
            'treated': self.workspace.processed_dir / image_id / 'imagem_tratada.png',
            'status': 'Pronta',
            'error': '',
        }

    async def test_image_error_keeps_stage_cause_and_sanitizes_log(self) -> None:
        api_key = 'sk-super-secret-diagnostic-key'
        provider = FailingImageProvider(api_key)
        with self.assertLogs('EMC Novidade Visual IA', level=logging.INFO) as captured:
            with self.assertRaises(ProviderError) as raised:
                await generate_automatic_novelty(
                    provider,
                    api_key=api_key,
                    text_model=TEXT_MODELS['openai'],
                    image_model='gpt-image-1.5',
                    raw_text='texto',
                    images=[self.image()],
                    workspace=self.workspace,
                    anonymization_prompt='anonimizar',
                    novelty_prompt='organizar',
                    run_sync=self.run_sync,
                )
        error = raised.exception
        self.assertEqual(error.stage, 'anonimizacao')
        self.assertEqual(error.image_id, 'imagem_001')
        self.assertEqual(error.status_http, 429)
        self.assertIsInstance(error.__cause__, ProviderError)
        output = '\n'.join(captured.output)
        self.assertNotIn(api_key, output)
        self.assertIn('[REDACTED]', output)


class ErrorMappingTestCase(unittest.TestCase):
    @patch('services.providers.openai_provider.OpenAI')
    def test_openai_text_only_structured_analysis(self, openai_class) -> None:
        plan = NoveltyPlan(blocos=[
            PlannedBlock(tipo='titulo', conteudo='Título'),
            PlannedBlock(tipo='texto', conteudo='Texto organizado'),
        ])
        client = openai_class.return_value
        client.responses.parse.return_value = SimpleNamespace(output_parsed=plan)
        result = OpenAIProvider().generate_novelty_plan(
            api_key='test-key', model=TEXT_MODELS['openai'], raw_text='texto bruto',
            images=[], prompt='organizar',
        )
        self.assertEqual(result, plan)
        content = client.responses.parse.call_args.kwargs['input'][0]['content']
        self.assertFalse(any(item.get('type') == 'input_image' for item in content))

    def test_missing_openai_key_is_rejected_before_api_call(self) -> None:
        with self.assertRaises(ProviderError) as raised:
            OpenAIProvider().generate_novelty_plan(
                api_key='   ', model='gpt-image-1.5', raw_text='texto', images=[], prompt='p'
            )
        self.assertEqual(raised.exception.code, 'missing_api_key')

    def test_zero_quota_is_identified(self) -> None:
        error = FakeHttpError(
            'quota',
            429,
            {'error': {
                'code': 429,
                'status': 'RESOURCE_EXHAUSTED',
                'message': 'Quota exceeded, limit: 0, model: gemini-3.1-flash-image',
            }},
        )
        mapped = map_provider_exception('gemini', error, 'secret-key')
        self.assertEqual(mapped.status_http, 429)
        self.assertIn('gemini-3.1-flash-image', mapped.user_message)
        self.assertIn('Criar outra chave no mesmo projeto não resolve', mapped.user_message)

    def test_incompatible_model_is_specific(self) -> None:
        mapped = map_provider_exception(
            'openai',
            FakeHttpError('The model does not support this operation', 400),
            'secret-key',
        )
        self.assertEqual(mapped.code, 'openai_model_incompatible')
        self.assertIn('não é compatível', mapped.user_message)

    def test_invalid_schema_is_specific(self) -> None:
        try:
            NoveltyPlan.model_validate({
                'blocos': [{'tipo': 'imagem', 'imagem_ids': []}],
                'imagens': [],
            })
        except ValidationError as exc:
            mapped = map_provider_exception('openai', exc, 'secret-key')
        else:
            self.fail('O payload inválido deveria falhar.')
        self.assertEqual(mapped.code, 'openai_invalid_schema_response')
        self.assertIn('formato esperado', mapped.user_message)

    def test_non_image_fields_from_real_response_are_normalized(self) -> None:
        block = PlannedBlock.model_validate({
            'tipo': 'texto',
            'conteudo': 'Conclusão',
            'imagem_ids': ['imagem_001'],
            'legenda': 'indevida',
            'modo_exibicao': 'lado_a_lado',
        })
        self.assertEqual(block.imagem_ids, [])
        self.assertEqual(block.legenda, '')
        self.assertEqual(block.modo_exibicao, 'normal')

    def test_technical_details_have_requested_fields(self) -> None:
        error = ProviderError(
            'Mensagem', provider='openai', model='gpt-4o-mini', stage='schema',
            status_http=200, exception_type='ValidationError', image_id='imagem_001',
        )
        self.assertEqual(
            set(error.sanitized_details()),
            {
                'Provedor', 'Modelo', 'Etapa', 'Status HTTP',
                'Tipo da exceção', 'Mensagem técnica', 'Imagem',
            },
        )


if __name__ == '__main__':
    unittest.main()
