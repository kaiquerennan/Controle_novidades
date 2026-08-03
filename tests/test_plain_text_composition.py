"""Round-trip entre a matéria e o texto corrido do editor.

Executar com:
    .venv\\Scripts\\python.exe -m pytest tests/test_plain_text_composition.py
"""

from __future__ import annotations

from models.composition import CompositionDocument, ContentBlock
from services.plain_text_composition import (
    add_marks,
    apply_plain_text,
    composition_to_plain_text,
    strip_marks,
)


def _documento() -> CompositionDocument:
    documento = CompositionDocument()
    documento.blocks = [
        ContentBlock(tipo='titulo', conteudo='Implementada opção de cores'),
        ContentBlock(
            tipo='texto',
            conteudo='Foi criado o parâmetro Cor de fundo na tela de departamentos.',
            destaques=['Cor de fundo'],
            papel='introducao',
        ),
        ContentBlock(tipo='imagem', imagem_id='imagem_1'),
        ContentBlock(
            tipo='lista',
            itens=[
                {'texto': 'Cor de fundo do menu', 'destaques': ['Cor de fundo']},
                {'texto': 'Cor de texto do menu', 'destaques': ['Cor de texto']},
            ],
        ),
        ContentBlock(tipo='texto', conteudo='As cores não se aplicam aos smartphones.'),
        ContentBlock(tipo='imagem', imagem_id='imagem_2'),
    ]
    return documento


def test_destaques_viram_asteriscos() -> None:
    texto = composition_to_plain_text(_documento())
    assert '**Cor de fundo**' in texto
    assert '**Cor de texto**' in texto
    assert '- **Cor de fundo** do menu' in texto


def test_asteriscos_viram_destaques() -> None:
    documento = _documento()
    apply_plain_text(documento, composition_to_plain_text(documento))
    texto_bloco = next(b for b in documento.blocks if b.tipo == 'texto')
    assert texto_bloco.destaques == ['Cor de fundo']
    assert '**' not in texto_bloco.conteudo, 'o conteúdo guardado fica sem marcação'
    lista = next(b for b in documento.blocks if b.tipo == 'lista')
    assert lista.itens[0]['destaques'] == ['Cor de fundo']
    assert lista.itens[0]['texto'] == 'Cor de fundo do menu'


def test_round_trip_estavel() -> None:
    documento = _documento()
    texto = composition_to_plain_text(documento)
    apply_plain_text(documento, texto)
    assert composition_to_plain_text(documento) == texto


def test_imagens_sao_preservadas_na_posicao() -> None:
    documento = _documento()
    antes = [b.tipo for b in documento.blocks]
    apply_plain_text(documento, composition_to_plain_text(documento))
    assert [b.tipo for b in documento.blocks] == antes
    assert [b.imagem_id for b in documento.blocks if b.tipo == 'imagem'] == [
        'imagem_1', 'imagem_2',
    ]


def test_paragrafo_iniciado_por_negrito_nao_vira_lista() -> None:
    documento = CompositionDocument()
    documento.blocks = [ContentBlock(tipo='titulo', conteudo='Título')]
    apply_plain_text(documento, 'Título\n\n**Cadastro Padrão** passa a ser opcional.')
    bloco = next(b for b in documento.blocks if b.tipo == 'texto')
    assert bloco.tipo == 'texto'
    assert bloco.destaques == ['Cadastro Padrão']
    assert bloco.conteudo == 'Cadastro Padrão passa a ser opcional.'


def test_destaque_contido_em_outro_nao_duplica_marcacao() -> None:
    marcado = add_marks(
        'A opção Cor de fundo e a opção Cor ficam disponíveis.',
        ['Cor', 'Cor de fundo'],
    )
    assert '***' not in marcado
    assert '**Cor de fundo**' in marcado


def test_texto_sem_marcacao_nao_gera_destaque() -> None:
    limpo, destaques = strip_marks('Frase simples sem nenhum negrito.')
    assert destaques == []
    assert limpo == 'Frase simples sem nenhum negrito.'
