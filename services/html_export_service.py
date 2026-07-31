from __future__ import annotations

from html import escape
from pathlib import PurePosixPath
import re
from typing import Any

from core.config import HTML_IMAGE_BASE_PATH


_ALLOWED_TAGS = {'p', 'font', 'strong', 'br', 'ul', 'li', 'img'}
_UNSAFE_SOURCE = re.compile(r'^(?:[a-z]:|\\|file:|data:|https?://|//)', re.IGNORECASE)


def validate_image_base_path(value: str) -> str:
    base = str(value or '').strip().replace('\\', '/')
    if not base.startswith('/') or _UNSAFE_SOURCE.match(base) or '..' in PurePosixPath(base).parts:
        raise ValueError('O caminho-base de imagens deve ser absoluto no aplicativo e não pode ser externo.')
    return base.rstrip('/') + '/'


def safe_image_src(filename: str, base_path: str = HTML_IMAGE_BASE_PATH) -> str:
    raw = str(filename or '').replace('\\', '/')
    if _UNSAFE_SOURCE.match(raw) or '..' in PurePosixPath(raw).parts:
        raise ValueError('Referência de imagem insegura para o HTML.')
    name = PurePosixPath(raw).name
    if not name or name in {'.', '..'}:
        raise ValueError('Nome de imagem inválido para o HTML.')
    safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', name)
    return validate_image_base_path(base_path) + safe_name


def render_inline(text: str, highlights: list[str] | None = None) -> str:
    value = str(text or '')
    terms = sorted(
        {str(item) for item in highlights or [] if str(item)}, key=len, reverse=True,
    )
    if not terms:
        return escape(value, quote=True)
    pattern = re.compile('|'.join(re.escape(term) for term in terms))
    parts: list[str] = []
    cursor = 0
    for match in pattern.finditer(value):
        parts.append(escape(value[cursor:match.start()], quote=True))
        parts.append(f'<strong>{escape(match.group(0), quote=True)}</strong>')
        cursor = match.end()
    parts.append(escape(value[cursor:], quote=True))
    return ''.join(parts)


def _paragraph(content: str, *, strong: bool = False, size: int = 2, align: str = 'justify') -> str:
    rendered = escape(str(content or ''), quote=True)
    if strong:
        rendered = f'<strong>{rendered}</strong>'
    return (
        f'<p class="isSelectedEnd" style="text-align: {align};">\n'
        f'    <font size="{size}" face="Verdana">{rendered}</font>\n'
        '</p>'
    )


def _text_paragraph(content: str, highlights: list[str]) -> str:
    return (
        '<p class="isSelectedEnd" style="text-align: justify;">\n'
        f'    <font size="2" face="Verdana">{render_inline(content, highlights)}</font>\n'
        '</p>'
    )


def _image_html(image: dict[str, Any], base_path: str) -> str:
    filename = str(image.get('arquivo_final') or image.get('arquivo') or '')
    src = safe_image_src(filename, base_path)
    alt = escape(PurePosixPath(filename.replace('\\', '/')).name, quote=True)
    width = max(1, int(image.get('largura') or 1))
    height = max(1, int(image.get('altura') or 1))
    return (
        '<p class="isSelectedEnd" style="text-align: center;">\n'
        '    <font size="2" face="Verdana">\n'
        f'        <img src="{escape(src, quote=True)}" alt="{alt}" '
        f'width="{width}" height="{height}" border="0">\n'
        '        <br>\n'
        '    </font>\n'
        '</p>'
    )


def build_novelty_html(
    metadata: dict[str, Any], base_path: str = HTML_IMAGE_BASE_PATH,
) -> str:
    base_path = validate_image_base_path(base_path)
    images = {str(item.get('id')): item for item in metadata.get('imagens') or []}
    fragments: list[str] = []
    cover = dict(metadata.get('capa') or {})
    if cover.get('status') == 'gerada' and cover.get('arquivo'):
        fragments.append(_image_html({
            'arquivo_final': cover['arquivo'],
            'largura': cover.get('largura'), 'altura': cover.get('altura'),
        }, base_path))
    for block in sorted(metadata.get('blocos') or [], key=lambda value: int(value.get('ordem') or 0)):
        block_type = str(block.get('tipo') or '')
        if block_type == 'titulo':
            fragments.append(_paragraph(block.get('conteudo', ''), strong=True, size=4))
        elif block_type == 'subtitulo':
            fragments.append(_paragraph(block.get('conteudo', ''), strong=True, size=3))
        elif block_type == 'texto':
            content = str(block.get('conteudo') or '')
            if block.get('papel') == 'caminho':
                lines = content.splitlines()
                label = escape(lines[0] if lines else 'Acesse através do caminho:', quote=True)
                path = escape(' / '.join(lines[1:]), quote=True)
                fragments.append(
                    '<p class="isSelectedEnd" style="text-align: justify;">\n'
                    '    <font size="2" face="Verdana">\n'
                    f'        <strong>{label}</strong><br>\n'
                    f'        <strong>{path}</strong>\n'
                    '    </font>\n'
                    '</p>'
                )
            else:
                for paragraph in re.split(r'\n\s*\n', content):
                    if paragraph.strip():
                        fragments.append(_text_paragraph(
                            paragraph.strip(), list(block.get('destaques') or []),
                        ))
        elif block_type == 'lista':
            items = list(block.get('itens') or [])
            rendered_items = [
                '    <li style="text-align: justify;">\n'
                '        <font size="2" face="Verdana">'
                f'{render_inline(item.get("texto", ""), item.get("destaques") or [])}'
                '</font>\n'
                '    </li>'
                for item in items
            ]
            fragments.append(
                '<ul data-spread="false">\n' + '\n'.join(rendered_items) + '\n</ul>'
            )
        elif block_type == 'imagem':
            for image_id in (block.get('imagem_id'), block.get('imagem_secundaria_id')):
                if image_id and str(image_id) in images:
                    fragments.append(_image_html(images[str(image_id)], base_path))
            if block.get('legenda'):
                fragments.append(_paragraph(block['legenda'], size=2, align='center'))
    html = '\n\n'.join(fragments).strip() + '\n'
    lowered = html.lower()
    for forbidden in ('<script', '<iframe', 'javascript:', ' onload=', ' onclick=', 'data:image', 'file:'):
        if forbidden in lowered:
            raise ValueError('Conteúdo inseguro detectado no HTML final.')
    return html


def allowed_html_tags() -> set[str]:
    return set(_ALLOWED_TAGS)