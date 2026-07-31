from __future__ import annotations

import re
from typing import Literal


PathKind = Literal['system', 'url', 'windows', 'unknown']
PATH_LABEL = 'Acesse através do caminho:'
_URL = re.compile(r'^(?:https?://|www\.|[a-z][a-z0-9+.-]*://)', re.IGNORECASE)
_WINDOWS = re.compile(r'^(?:[a-z]:[\\/]|\\\\|%[^%]+%[\\/])', re.IGNORECASE)
_NAVIGATION = re.compile(
    r'^\s*(?:(?:acesse\s+atrav[eé]s\s+do\s+caminho|caminho(?:\s+do\s+sistema)?)'
    r'\s*:?\s*|acesse\s*:\s*)(?P<value>.+?)\s*$',
    re.IGNORECASE,
)


def classify_path(value: str) -> PathKind:
    candidate = value.strip()
    if _URL.match(candidate):
        return 'url'
    if _WINDOWS.match(candidate):
        return 'windows'
    if '>' in candidate and len(_segments(candidate, '>')) >= 2:
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
    separator = '>' if '>' in candidate else '/'
    return ' / '.join(_segments(candidate, separator))


def extract_system_paths(text: str) -> list[str]:
    paths: list[str] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        candidate = ''
        if line.casefold() == PATH_LABEL.casefold() and index + 1 < len(lines):
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

def missing_system_paths(source_text: str, final_text: str) -> list[str]:
    final_paths = set(extract_system_paths(final_text))
    return [path for path in extract_system_paths(source_text) if path not in final_paths]


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