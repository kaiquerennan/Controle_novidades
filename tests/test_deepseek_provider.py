"""Provider DeepSeek sem rede: contrato, parsing e limites conhecidos.

Executar com:
    .venv\\Scripts\\python.exe -m pytest tests/test_deepseek_provider.py
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.config import API_KEY_ENVIRONMENTS, DEEPSEEK_BASE_URL, PROVIDER_LABELS
from services.providers.deepseek_provider import DeepSeekProvider
from services.providers.exceptions import ProviderError
from services.providers.provider_factory import ProviderFactory


PLANO_VALIDO = {
    'blocos': [
        {'tipo': 'titulo', 'conteudo': 'Implementada integração com a API Placas'},
        {
            'tipo': 'texto',
            'conteudo': 'No caminho Cadastro | Veículo, foi implementada a integração.',
            'papel': 'introducao',
        },
        {'tipo': 'lista', 'itens': [{'texto': 'Cor de fundo'}, {'texto': 'Cor de texto'}]},
    ],
    'imagens': [],
}

REVISAO_VALIDA = {'aprovada': True, 'avisos': [], 'correcoes_necessarias': []}


class FakeCompletions:
    def __init__(self, conteudo: str) -> None:
        self.conteudo = conteudo
        self.chamadas: list[dict] = []

    def create(self, **kwargs):
        self.chamadas.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.conteudo))],
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
        )


class FakeClient:
    def __init__(self, conteudo: str) -> None:
        self.completions = FakeCompletions(conteudo)
        self.chat = SimpleNamespace(completions=self.completions)


@pytest.fixture
def provider() -> DeepSeekProvider:
    return DeepSeekProvider()


def _com_cliente(provider: DeepSeekProvider, conteudo: str) -> FakeClient:
    cliente = FakeClient(conteudo)
    provider._client = lambda api_key, timeout: cliente  # type: ignore[assignment]
    return cliente


def test_provedor_registrado_na_fabrica() -> None:
    assert isinstance(ProviderFactory.create('deepseek'), DeepSeekProvider)
    assert isinstance(ProviderFactory.create('DeepSeek'), DeepSeekProvider)


def test_configuracao_declarada() -> None:
    assert PROVIDER_LABELS['deepseek'] == 'DeepSeek'
    assert API_KEY_ENVIRONMENTS['deepseek'] == 'DEEPSEEK_API_KEY'
    assert DEEPSEEK_BASE_URL == 'https://api.deepseek.com'


def test_plano_valido_e_convertido(provider: DeepSeekProvider) -> None:
    cliente = _com_cliente(provider, json.dumps(PLANO_VALIDO, ensure_ascii=False))
    plano = provider.generate_novelty_plan(
        api_key='sk-teste', model='deepseek-chat', raw_text='texto', images=[], prompt='P',
    )
    assert [b.tipo for b in plano.blocos] == ['titulo', 'texto', 'lista']
    assert plano.blocos[0].conteudo.startswith('Implementada')

    enviado = cliente.completions.chamadas[0]
    assert enviado['model'] == 'deepseek-chat'
    assert enviado['response_format'] == {'type': 'json_object'}
    # Sem structured outputs, o esquema precisa ir dentro do prompt.
    assert 'JSON Schema' in enviado['messages'][0]['content']
    assert 'blocos' in enviado['messages'][0]['content']


def test_imagens_nao_impedem_a_geracao(provider: DeepSeekProvider) -> None:
    _com_cliente(provider, json.dumps(PLANO_VALIDO, ensure_ascii=False))
    plano = provider.generate_novelty_plan(
        api_key='sk-teste', model='deepseek-chat', raw_text='texto',
        images=[('imagem_1', Path('a.png'))], prompt='P',
    )
    assert plano.blocos, 'a geração continua mesmo sem visão no modelo'


def test_revisao_valida(provider: DeepSeekProvider) -> None:
    _com_cliente(provider, json.dumps(REVISAO_VALIDA, ensure_ascii=False))
    revisao = provider.review_novelty(
        api_key='sk-teste', model='deepseek-chat', raw_text='texto',
        document={}, image_ids=[], prompt='P',
    )
    assert revisao.aprovada is True


def test_resposta_vazia_vira_erro_tratado(provider: DeepSeekProvider) -> None:
    _com_cliente(provider, '   ')
    with pytest.raises(ProviderError) as erro:
        provider.generate_novelty_plan(
            api_key='sk-teste', model='deepseek-chat', raw_text='t', images=[], prompt='P',
        )
    assert erro.value.code == 'deepseek_empty_response'


def test_chave_ausente_e_recusada(provider: DeepSeekProvider) -> None:
    with pytest.raises(ProviderError) as erro:
        provider.generate_novelty_plan(
            api_key='  ', model='deepseek-chat', raw_text='t', images=[], prompt='P',
        )
    assert erro.value.code == 'missing_api_key'


@pytest.mark.parametrize(
    'operacao',
    [
        lambda p: p.generate_cover(
            api_key='sk', model='m', prompt='p', destination=Path('c.png')
        ),
        lambda p: p.treat_image(
            api_key='sk', model='m', prompt='p', source=Path('a.png'),
            destination=Path('b.png'),
        ),
        lambda p: p.suggest_orientation(api_key='sk', model='m', source=Path('a.png')),
    ],
)
def test_operacoes_de_imagem_falham_com_mensagem_clara(provider, operacao) -> None:
    with pytest.raises(ProviderError) as erro:
        operacao(provider)
    assert 'imagens' in str(erro.value).lower()
