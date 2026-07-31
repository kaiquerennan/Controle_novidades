from __future__ import annotations

import re
from typing import Literal


PathKind = Literal['system', 'url', 'windows', 'unknown']
PATH_LABEL = 'Acesse através do caminho:'
# As ocorrências da EMC escrevem a navegação com "|"; o padrão editorial mantém
# esse separador no texto corrido.
PATH_SEPARATOR = ' | '
_SEPARATORS = ('|', '>')
_URL = re.compile(r'^(?:https?://|www\.|[a-z][a-z0-9+.-]*://)', re.IGNORECASE)
_WINDOWS = re.compile(r'^(?:[a-z]:[\\/]|\\\\|%[^%]+%[\\/])', re.IGNORECASE)
_NAVIGATION = re.compile(
    r'^\s*(?:(?:acesse\s+atrav[eé]s\s+do\s+caminho|caminho(?:\s+do\s+sistema)?)'
    r'\s*:?\s*|acesse\s*:\s*)(?P<value>.+?)\s*$',
    re.IGNORECASE,
)
# As ocorrências escrevem o rótulo sozinho e o caminho na linha seguinte.
_NAVIGATION_LABEL = re.compile(
    r'^\s*(?:acesse\s+atrav[eé]s\s+do\s+caminho|caminho(?:\s+do\s+sistema)?|acesse)\s*:\s*$',
    re.IGNORECASE,
)


def classify_path(value: str) -> PathKind:
    candidate = value.strip()
    if _URL.match(candidate):
        return 'url'
    if _WINDOWS.match(candidate):
        return 'windows'
    for separator in _SEPARATORS:
        if separator in candidate and len(_segments(candidate, separator)) >= 2:
            return 'system'
    if ' / ' in candidate and len(_segments(candidate, '/')) >= 2:
        return 'system'
    return 'unknown'


def _segments(value: str, separator: str) -> list[str]:
    return [segment.strip() for segment in value.split(separator) if segment.strip()]


def canonical_system_path(value: str) -> str | None:
    candidate = value.strip()
    match = _NAVIGATION.match(candidate)
    if match:
        candidate = match.group('value').strip()
    if classify_path(candidate) != 'system':
        return None
    separator = next(
        (option for option in _SEPARATORS if option in candidate), '/'
    )
    return PATH_SEPARATOR.join(_segments(candidate, separator))


def extract_system_paths(text: str) -> list[str]:
    paths: list[str] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        candidate = ''
        if _NAVIGATION_LABEL.match(line) and index + 1 < len(lines):
            candidate = lines[index + 1].strip()
            index += 1
        else:
            match = _NAVIGATION.match(line)
            if match:
                candidate = match.group('value').strip()
        canonical = canonical_system_path(candidate) if candidate else None
        if canonical and canonical not in paths:
            paths.append(canonical)
        index += 1
    return paths

def mentions_system_path(text: str, path: str) -> bool:
    """Reconhece o caminho citado no meio de uma frase, não só em linha própria.

    O padrão editorial escreve "No caminho A | B | C, foi implementada...", então
    a checagem de fidelidade precisa aceitar essa forma além do bloco rotulado.
    """
    segments = _segments(path, PATH_SEPARATOR.strip())
    if len(segments) < 2:
        return False
    normalizado = _normalize(text)
    return any(
        _normalize(separator.join(segments)) in normalizado
        for separator in (PATH_SEPARATOR, ' > ', ' / ', '|', '>', '/')
    )


def _normalize(value: str) -> str:
    return re.sub(r'\s+', ' ', value).strip().casefold()


def missing_system_paths(source_text: str, final_text: str) -> list[str]:
    final_paths = set(extract_system_paths(final_text))
    return [
        path
        for path in extract_system_paths(source_text)
        if path not in final_paths and not mentions_system_path(final_text, path)
    ]


def format_system_navigation(text: str) -> str:
    """Normaliza somente navegações explicitamente identificadas como caminho do sistema."""
    formatted: list[str] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip().casefold() == PATH_LABEL.casefold() and index + 1 < len(lines):
            canonical = canonical_system_path(lines[index + 1])
            if canonical:
                formatted.extend((PATH_LABEL, canonical))
                index += 2
                continue
        match = _NAVIGATION.match(line)
        if match:
            canonical = canonical_system_path(match.group('value'))
            if canonical:
                formatted.extend((PATH_LABEL, canonical))
                index += 1
                continue
        formatted.append(line)
        index += 1
    return '\n'.join(formatted)