from __future__ import annotations

import re

from models.composition import CompositionDocument, ContentBlock


LIST_MARKER = '- '
TEXT_TYPES = frozenset({'titulo', 'subtitulo', 'texto', 'lista'})

# No editor, o negrito é escrito com asteriscos duplos. Na matéria ele vive no
# campo destaques de cada bloco, que guarda os trechos a destacar.
MARK = '**'
_MARK_PATTERN = re.compile(r'\*\*(.+?)\*\*', re.DOTALL)


def add_marks(text: str, destaques: list[str]) -> str:
    """Escreve os destaques do bloco como **negrito** no texto."""
    alvos = [str(item) for item in dict.fromkeys(destaques) if str(item).strip()]
    if not alvos:
        return text
    # Do maior para o menor e em uma única passagem: assim um destaque contido
    # em outro não é marcado de novo dentro do que já foi marcado.
    padrao = re.compile(
        '|'.join(re.escape(alvo) for alvo in sorted(alvos, key=len, reverse=True))
    )
    return padrao.sub(lambda encontrado: f'{MARK}{encontrado.group(0)}{MARK}', text)


def strip_marks(text: str) -> tuple[str, list[str]]:
    """Separa o texto limpo dos trechos que estavam em **negrito**."""
    destaques: list[str] = []

    def _coletar(encontrado: re.Match[str]) -> str:
        trecho = encontrado.group(1).strip()
        if trecho:
            destaques.append(trecho)
        return encontrado.group(1)

    return _MARK_PATTERN.sub(_coletar, text), list(dict.fromkeys(destaques))


def composition_to_plain_text(document: CompositionDocument) -> str:
    """Converte a matéria em texto corrido, como ela seria lida.

    Blocos de imagem e divisores não aparecem: eles são posicionados na
    pré-visualização, não no texto.
    """
    paragrafos: list[str] = []
    for block in document.blocks:
        if block.tipo in {'titulo', 'subtitulo', 'texto'}:
            conteudo = block.conteudo.strip()
            if conteudo:
                paragrafos.append(add_marks(conteudo, list(block.destaques)))
        elif block.tipo == 'lista':
            linhas = []
            for item in block.itens:
                texto = str(item.get('texto') or '').strip()
                if not texto:
                    continue
                marcado = add_marks(texto, list(item.get('destaques') or []))
                linhas.append(f'{LIST_MARKER}{marcado}')
            if linhas:
                paragrafos.append('\n'.join(linhas))
    return '\n\n'.join(paragrafos)


# O asterisco simples marca item de lista; o duplo marca negrito. Sem o
# negative lookahead, um parágrafo iniciado por **negrito** viraria lista.
_LIST_PREFIX = re.compile(r'^([-•–—]|\*(?!\*))\s*')


def _is_list_line(line: str) -> bool:
    return bool(_LIST_PREFIX.match(line))


def _strip_list_marker(line: str) -> str:
    return _LIST_PREFIX.sub('', line, count=1).strip()


def _blocks_from_plain_text(text: str) -> list[ContentBlock]:
    normalizado = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    paragrafos = [
        bloco.strip() for bloco in normalizado.split('\n\n') if bloco.strip()
    ]
    blocos: list[ContentBlock] = []
    for indice, paragrafo in enumerate(paragrafos):
        linhas = [linha.strip() for linha in paragrafo.splitlines() if linha.strip()]
        if not linhas:
            continue
        if indice == 0:
            conteudo, destaques = strip_marks(' '.join(linhas))
            blocos.append(
                ContentBlock(tipo='titulo', conteudo=conteudo, destaques=destaques)
            )
            continue
        if all(_is_list_line(linha) for linha in linhas):
            itens = []
            for linha in linhas:
                texto, destaques = strip_marks(_strip_list_marker(linha))
                if texto:
                    itens.append({'texto': texto, 'destaques': destaques})
            blocos.append(ContentBlock(tipo='lista', itens=itens))
            continue
        conteudo, destaques = strip_marks('\n'.join(linhas))
        blocos.append(
            ContentBlock(tipo='texto', conteudo=conteudo, destaques=destaques)
        )
    primeiro_texto = next((b for b in blocos if b.tipo == 'texto'), None)
    if primeiro_texto is not None:
        primeiro_texto.papel = 'introducao'
    return blocos


def apply_plain_text(document: CompositionDocument, text: str) -> None:
    """Reescreve a matéria a partir do texto corrido, preservando as imagens.

    Cada bloco de imagem é reinserido depois da mesma quantidade de parágrafos
    que o precedia antes da edição. Assim as imagens acompanham o texto sem
    precisar de marcadores no meio da escrita.
    """
    ancoras: list[tuple[int, ContentBlock]] = []
    paragrafos_antes = 0
    for block in document.blocks:
        if block.tipo in TEXT_TYPES:
            paragrafos_antes += 1
        else:
            ancoras.append((paragrafos_antes, block))

    novos = _blocks_from_plain_text(text)
    # Insere em ordem crescente somando os já inseridos, senão cada inserção
    # desloca as âncoras seguintes.
    for inseridos, (posicao, bloco) in enumerate(sorted(ancoras, key=lambda par: par[0])):
        novos.insert(min(posicao + inseridos, len(novos)), bloco)

    document.blocks = novos
    document.source = 'manual'


__all__ = [
    'LIST_MARKER',
    'MARK',
    'add_marks',
    'apply_plain_text',
    'composition_to_plain_text',
    'strip_marks',
]
