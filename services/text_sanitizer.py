from __future__ import annotations

from html import unescape
import re


_COMMENT = re.compile(r'(?s)<!--.*?-->')
_NOT_CONTENT = re.compile(r'(?is)<(script|style)\b.*?</\1\s*>')
_LIST_ITEM = re.compile(r'(?i)<li[^>]*>')
_LINE_BREAK = re.compile(r'(?i)<br\s*/?>')
# "li" fica de fora: a abertura dele ja inseriu a quebra e o marcador, e tratar o
# fechamento tambem separaria cada item da lista por uma linha em branco.
_BLOCK_TAGS = 'p|div|h[1-6]|tr|ul|ol|table|section|article|blockquote|pre'
_BLOCK_END = re.compile(rf'(?i)</(?:{_BLOCK_TAGS})\s*>')
_BLOCK_START = re.compile(rf'(?i)<(?:{_BLOCK_TAGS})[^>]*>')
# Só o que tem cara de marcação sai; um "a < b" solto no texto fica preservado.
_ANY_TAG = re.compile(r'</?[a-zA-Z][^>]*>')

_LINK = re.compile(r'!?\[([^\]]+)\]\(([^)]+)\)')
_BOLD = re.compile(r'(?:\*\*|__)(.+?)(?:\*\*|__)')
_ITALIC = re.compile(r'(?<!\w)_([^_]+)_(?!\w)')


def html_to_text(value: str) -> str:
    """Converte o HTML do cadastro em texto puro, preservando as quebras.

    O conteudo publicado vem com marcacao do editor do portal. Exibir isso cru
    na tela mostraria as tags ao usuario, entao aqui os blocos viram quebras de
    linha, as listas viram marcadores e o resto da marcacao e descartado.
    """
    texto = str(value or '')
    if not texto.strip():
        return ''
    texto = _NOT_CONTENT.sub(' ', texto)
    texto = _COMMENT.sub(' ', texto)
    texto = _LIST_ITEM.sub('\n- ', texto)
    texto = _LINE_BREAK.sub('\n', texto)
    texto = _BLOCK_END.sub('\n', texto)
    texto = _BLOCK_START.sub('\n', texto)
    texto = _ANY_TAG.sub('', texto)
    # As entidades so podem ser resolvidas depois de tirar as tags: um &lt;b&gt;
    # no texto viraria uma tag de verdade se a ordem fosse invertida.
    texto = unescape(texto).replace('\xa0', ' ')
    linhas = [re.sub(r'[ \t]+', ' ', linha).strip() for linha in texto.split('\n')]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(linhas)).strip()


def markdown_highlights(value: str) -> list[str]:
    return [match.group(1).strip() for match in _BOLD.finditer(str(value or '')) if match.group(1).strip()]


def strip_markdown(value: str) -> str:
    """Remove apenas a sintaxe visual, preservando conteúdo e marcadores de lista."""
    text = str(value or '').replace('\r\n', '\n')
    text = _LINK.sub(
        lambda match: f'{match.group(1)} ({match.group(2)})' if match.group(2) else match.group(1),
        text,
    )
    cleaned: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if re.fullmatch(r'\s*```[^`]*\s*', line):
            in_fence = not in_fence
            continue
        line = re.sub(r'^\s{0,3}#{1,6}\s+', '', line)
        line = re.sub(r'^\s*>\s?', '', line)
        cleaned.append(line)
    text = '\n'.join(cleaned)
    text = _BOLD.sub(lambda match: match.group(1), text)
    text = _ITALIC.sub(lambda match: match.group(1), text)
    text = re.sub(r'`([^`]+)`', lambda match: match.group(1), text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()