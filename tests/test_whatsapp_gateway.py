"""Cliente do gateway de WhatsApp, sem rede e sem Node.

Executar com:
    .venv\\Scripts\\python.exe -m pytest tests/test_whatsapp_gateway.py
"""

from __future__ import annotations

import pytest

from services.whatsapp_gateway_service import (
    WhatsAppGatewayError,
    WhatsAppGatewayService,
    mask_phone,
    normalize_phone,
)


@pytest.fixture
def service() -> WhatsAppGatewayService:
    return WhatsAppGatewayService()


def _com_resposta(service: WhatsAppGatewayService, resposta: dict) -> list[tuple]:
    chamadas: list[tuple] = []

    def _fake(method: str, path: str, payload: dict | None = None):
        chamadas.append((method, path, payload))
        return resposta

    service._request = _fake  # type: ignore[assignment]
    return chamadas


@pytest.mark.parametrize(
    'entrada,esperado',
    [
        ('31999998888', '5531999998888'),
        ('5531999998888', '5531999998888'),
        ('+55 31 99999-8888', '5531999998888'),
        ('(31) 99999-8888', '5531999998888'),
    ],
)
def test_numero_normalizado(entrada: str, esperado: str) -> None:
    assert normalize_phone(entrada) == esperado


@pytest.mark.parametrize('invalido', ['', '   ', 'abc', '123', '99999'])
def test_numero_invalido_e_recusado(invalido: str) -> None:
    with pytest.raises(WhatsAppGatewayError):
        normalize_phone(invalido)


def test_numero_mascarado_no_log() -> None:
    mascarado = mask_phone('5531999998888')
    assert mascarado.startswith('5531')
    assert mascarado.endswith('88')
    assert '99999' not in mascarado


def test_status_com_servico_parado(service: WhatsAppGatewayService) -> None:
    service.is_listening = lambda: False  # type: ignore[assignment]
    situacao = service.status()
    assert situacao.running is False
    assert situacao.connected is False
    assert situacao.label == 'Serviço do WhatsApp parado'


def test_status_conectado(service: WhatsAppGatewayService) -> None:
    service.is_listening = lambda: True  # type: ignore[assignment]
    _com_resposta(service, {
        'conexao': 'conectado', 'conectado': True,
        'telefone': '5531999998888', 'tem_qr': False, 'ultimo_erro': '',
    })
    situacao = service.status()
    assert situacao.connected is True
    assert situacao.phone == '5531999998888'
    assert situacao.label == 'Conectado'


def test_status_aguardando_qr(service: WhatsAppGatewayService) -> None:
    service.is_listening = lambda: True  # type: ignore[assignment]
    _com_resposta(service, {'conexao': 'aguardando_qr', 'tem_qr': True})
    situacao = service.status()
    assert situacao.has_qr is True
    assert situacao.label == 'Aguardando leitura do QR'


def test_envio_normaliza_o_numero(service: WhatsAppGatewayService) -> None:
    chamadas = _com_resposta(service, {'id': 'ABC123'})
    identificador = service.send(recipient='(31) 99999-8888', message='Olá')
    assert identificador == 'ABC123'
    metodo, caminho, payload = chamadas[0]
    assert (metodo, caminho) == ('POST', '/send')
    assert payload == {'to': '5531999998888', 'message': 'Olá'}


def test_envio_recusa_mensagem_vazia(service: WhatsAppGatewayService) -> None:
    _com_resposta(service, {'id': 'x'})
    with pytest.raises(WhatsAppGatewayError) as erro:
        service.send(recipient='31999998888', message='   ')
    assert 'vazia' in erro.value.user_message


def test_logout_chama_o_gateway(service: WhatsAppGatewayService) -> None:
    chamadas = _com_resposta(service, {'ok': True})
    service.logout()
    assert chamadas[0][:2] == ('POST', '/logout')


def test_qr_ausente_devolve_vazio(service: WhatsAppGatewayService) -> None:
    def _falha(method: str, path: str, payload: dict | None = None):
        raise WhatsAppGatewayError('Nenhum QR disponivel no momento.')

    service._request = _falha  # type: ignore[assignment]
    assert service.qr_code() == ''
