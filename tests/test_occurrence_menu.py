"""Render do menu de ocorrencias, sem banco e sem navegador.

Executar com:
    .venv\\Scripts\\python.exe -m pytest tests/test_occurrence_menu.py
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil
import tempfile
from typing import Iterator

from nicegui import ui
from nicegui.testing import User
import pytest

from components.occurrence_menu import render_occurrence_menu
from services.image_service import DATA_ROOT
from services.occurrence_dismissal_service import OccurrenceDismissalService
from services.occurrence_query_service import (
    OccurrenceQueryService,
    OccurrenceSummary,
    parse_description_sections,
)


# O plugin completo exige selenium; o user_plugin roda sem navegador.
pytest_plugins = ['nicegui.testing.user_plugin']


def _ocorrencia(numero: int, solicitacao: str, justificativa: str) -> OccurrenceSummary:
    descricao = f'SOLICITAÇÃO:\n{solicitacao}\n\nJUSTIFICATIVA:\n{justificativa}'
    return OccurrenceQueryService.build_summary(
        {
            'NUMERO_OCORRENCIA': numero,
            'CODIGO_CLIENTE': 1573,
            'DATA_ABERTURA': datetime(2026, 7, 27, 15, 23),
            'ULTIMA_ALTERACAO': datetime(2026, 7, 31, 10, 45),
            'JSON_COMPLETO': json.dumps(
                {
                    'NUMERO_OCORRENCIA': numero,
                    'NOME_CLIENTE': 'Cliente Teste',
                    'SISTEMA': 'EMC GESTÃO EMPRESARIAL',
                    'MODULO': 'EMC FORÇA DE VENDA',
                    'DESCRICAO': descricao,
                    'COMPLEMENTO': '',
                    'OBSERVACOES_RELEVANTES': [],
                    'RELACIONAMENTO': None,
                },
                ensure_ascii=False,
            ),
        }
    )


OCORRENCIAS = [
    _ocorrencia(
        97422,
        'Exibir o preco de atacado na consulta de produtos.',
        'Hoje o vendedor precisa consultar o valor de atacado por fora.',
    ),
    _ocorrencia(
        97421,
        'Mostrar a imagem dos produtos durante a consulta.',
        'A ausencia da imagem dificulta a identificacao visual dos itens.',
    ),
]


class FakeQueryService:
    """Substitui o SQL Server: aplica o mesmo filtro que a consulta real faz."""

    def __init__(self) -> None:
        self.searches: list[str] = []

    def list_changes(self, *, search: str = '', months: int = 3, limit: int = 500):
        self.searches.append(search)
        if not search:
            return list(OCORRENCIAS)
        termo = search.lower()
        return [
            item
            for item in OCORRENCIAS
            if termo in str(item.number) or termo in item.raw_json.lower()
        ]


@pytest.fixture
def dismissals() -> Iterator[OccurrenceDismissalService]:
    # safe_path so aceita caminhos dentro da raiz do projeto.
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='menu_', dir=DATA_ROOT))
    try:
        yield OccurrenceDismissalService(path=root / 'dismissed.json')
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def queries() -> FakeQueryService:
    return FakeQueryService()


@pytest.fixture
def launched() -> list[OccurrenceSummary]:
    return []


@pytest.fixture
def page(
    queries: FakeQueryService,
    dismissals: OccurrenceDismissalService,
    launched: list[OccurrenceSummary],
) -> None:
    @ui.page('/')
    def index() -> None:
        render_occurrence_menu(
            launched.append,
            query_service=queries,
            dismissal_service=dismissals,
        )


async def test_lista_mostra_solicitacao_e_justificativa(user: User, page: None) -> None:
    await user.open('/')
    await user.should_see('#97422')
    await user.should_see('#97421')
    await user.should_see('SOLICITAÇÃO')
    await user.should_see('JUSTIFICATIVA')
    await user.should_see('Exibir o preco de atacado na consulta de produtos.')
    await user.should_see('A ausencia da imagem dificulta a identificacao visual dos itens.')
    await user.should_see('2 ocorrência(s) de mudança nos últimos 3 meses')


async def test_busca_filtra_por_numero(user: User, page: None) -> None:
    await user.open('/')
    await user.should_see('#97421')
    user.find(marker='busca-ocorrencia').type('97422')
    await user.should_see(marker='lancar-97422')
    await user.should_not_see(marker='lancar-97421')


async def test_busca_filtra_por_texto_do_lancamento(user: User, page: None) -> None:
    await user.open('/')
    user.find(marker='busca-ocorrencia').type('imagem dos produtos')
    await user.should_see(marker='lancar-97421')
    await user.should_not_see(marker='lancar-97422')


async def test_lancar_novidade_entrega_a_ocorrencia(
    user: User, page: None, launched: list[OccurrenceSummary]
) -> None:
    await user.open('/')
    await user.should_see('#97422')
    user.find(marker='lancar-97422').click()
    await user.should_see('#97422')
    assert [item.number for item in launched] == [97422]


async def test_excluir_pede_confirmacao_e_some_da_lista(
    user: User, page: None, dismissals: OccurrenceDismissalService
) -> None:
    await user.open('/')
    await user.should_see('#97422')

    user.find(marker='excluir-97422').click()
    await user.should_see('Não lançar novidade?')
    await user.should_see('ocorrência número 97422 não deve virar novidade')

    # Enquanto nao confirma, nada e excluido.
    assert dismissals.numbers() == set()

    user.find(marker='confirmar-exclusao').click()
    # Pelo marcador, e nao pelo texto: a notificacao de sucesso tambem cita o numero.
    await user.should_not_see(marker='excluir-97422')
    await user.should_see(marker='excluir-97421')
    assert dismissals.numbers() == {97422}


async def test_excluida_reaparece_em_ver_excluidas(
    user: User, page: None, dismissals: OccurrenceDismissalService
) -> None:
    dismissals.dismiss(97422, 'nao vira novidade')
    await user.open('/')
    await user.should_not_see(marker='excluir-97422')
    user.find(kind=ui.switch).click()
    await user.should_see('#97422')
    await user.should_see('Restaurar')


def test_descricao_e_separada_pelos_rotulos() -> None:
    descricao = (
        '\r\nCAMINHO: \r\nInstalador\r\n\r\nSOLICITAÇÃO: \r\nGerar instalador da última '
        'versão.\r\n\r\nJUSTIFICATIVA: \r\nLiberação compatível com a última versão'
    )
    secoes = parse_description_sections(descricao)
    assert secoes['CAMINHO'] == 'Instalador'
    assert secoes['SOLICITACAO'] == 'Gerar instalador da última versão.'
    assert secoes['JUSTIFICATIVA'] == 'Liberação compatível com a última versão'


def test_descricao_sem_rotulos_nao_inventa_secoes() -> None:
    assert parse_description_sections('Texto corrido sem rotulo nenhum.') == {}


def test_ocorrencia_sem_solicitacao_mantem_descricao() -> None:
    resumo = OccurrenceQueryService.build_summary(
        {
            'NUMERO_OCORRENCIA': 1,
            'JSON_COMPLETO': json.dumps({'DESCRICAO': 'Apenas um texto solto.'}),
        }
    )
    assert resumo.solicitation == ''
    assert resumo.justification == ''
    assert resumo.description == 'Apenas um texto solto.'


def test_texto_para_geracao_reune_o_contexto() -> None:
    texto = OCORRENCIAS[0].as_raw_text()
    assert 'OCORRÊNCIA: 97422' in texto
    assert 'EMC FORÇA DE VENDA' in texto
    assert 'Exibir o preco de atacado' in texto
