from __future__ import annotations

import re


_LINK = re.compile(r'!?\[([^\]]+)\]\(([^)]+)\)')
_BOLD = re.compile(r'(?:\*\*|__)(.+?)(?:\*\*|__)')
_ITALIC = re.compile(r'(?<!\w)_([^_]+)_(?!\w)')


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