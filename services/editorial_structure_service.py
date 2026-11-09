from __future__ import annotations

import json
import re

from models.editorial_source import EditorialUnit
from services.system_path_formatter import PATH_LABEL, canonical_system_path


_EXPLICIT_PATH = re.compile(
    r'^\s*(?:acesse\s+atrav[eé]s\s+do\s+caminho|caminho(?:\s+do\s+sistema)?|acesse)\s*:',
    re.IGNORECASE,
)
_LABELS = {
    'campos': 'campos', 'campo': 'campos', 'botões': 'botoes', 'botoes': 'botoes',
    'botão': 'botoes', 'botao': 'botoes', 'parâmetros': 'parametros',
    'parametros': 'parametros', 'parâmetro': 'parametros', 'parametro': 'parametros',
    'opções': 'opcoes', 'opcoes': 'opcoes', 'opção': 'opcoes', 'opcao': 'opcoes',
    'exemplos': 'exemplos', 'exemplo': 'exemplos', 'observações': 'observacoes',
    'observacoes': 'observacoes', 'observação': 'observacoes', 'observacao': 'observacoes',
}
_BULLET = re.compile(r'^\s*(?:[-+*•]|\d+[.)])\s+(?P<value>.+)$')
_BOLD = re.compile(r'(?:\*\*|__)(.+?)(?:\*\*|__)')


def semantic_text(value: str) -> tuple[str, list[str]]:
    highlights = [match.group(1).strip() for match in _BOLD.finditer(value) if match.group(1).strip()]
    text = _BOLD.sub(lambda match: match.group(1), value).strip()
    return text, highlights


def _values(value: str) -> list[str]:
    return [item.strip() for item in re.split(r'\s*[;,]\s*', value) if item.strip()]


def extract_editorial_units(raw_text: str) -> list[EditorialUnit]:
    lines = raw_text.replace('\r\n', '\n').splitlines()
    units: list[EditorialUnit] = [EditorialUnit(id='rotina_001')]
    current = units[0]
    pending_list: list[dict[str, object]] = []
    consumed_lines: set[int] = set()

    def flush_list() -> None:
        nonlocal pending_list
        if pending_list:
            current.listas.append(pending_list)
            pending_list = []

    for index, raw_line in enumerate(lines):
        if index in consumed_lines:
            continue
        line = raw_line.strip()
        if not line:
            flush_list()
            continue
        if _EXPLICIT_PATH.match(line):
            path_candidate = line
            if (
                line.casefold() == PATH_LABEL.casefold()
                and index + 1 < len(lines)
            ):
                path_candidate = lines[index + 1].strip()
                consumed_lines.add(index + 1)
            path = canonical_system_path(path_candidate)
            if path:
                if current.caminho:
                    flush_list()
                    current = EditorialUnit(id=f'rotina_{len(units) + 1:03d}')
                    units.append(current)
                current.caminho = path
                previous = lines[index - 1].strip() if index else ''
                if previous and len(previous) <= 120 and not _EXPLICIT_PATH.match(previous):
                    previous_text, previous_highlights = semantic_text(previous)
                    current.nome = previous_text
                    current.destaques.extend(previous_highlights)
                    if current.descricao and current.descricao[-1] == previous_text:
                        current.descricao.pop()
                continue
        labelled = re.match(r'^([^:]{1,30}):\s*(.+)$', line)
        if labelled:
            key = _LABELS.get(labelled.group(1).strip().casefold())
            if key:
                flush_list()
                values = _values(labelled.group(2))
                getattr(current, key).extend(values)
                for value in values:
                    _, highlights = semantic_text(value)
                    current.destaques.extend(highlights)
                continue
        bullet = _BULLET.match(line)
        if bullet:
            text, highlights = semantic_text(bullet.group('value'))
            pending_list.append({'texto': text, 'destaques': highlights})
            current.destaques.extend(highlights)
            continue
        flush_list()
        text, highlights = semantic_text(line)
        current.descricao.append(text)
        current.destaques.extend(highlights)
        if not current.nome and len(text) <= 100 and not text.endswith(('.', ';', ':')):
            current.nome = text
    flush_list()
    return [unit for unit in units if any(
        (unit.nome, unit.caminho, unit.descricao, unit.campos, unit.botoes,
         unit.parametros, unit.opcoes, unit.exemplos, unit.observacoes, unit.listas)
    )]


def structured_source_prompt(raw_text: str, units: list[EditorialUnit]) -> str:
    inventory = json.dumps([unit.to_dict() for unit in units], ensure_ascii=False, indent=2)
    # O provedor ja rotula este conteudo como TEXTO BRUTO DA NOVIDADE, e o
    # prompt descreve os blocos por esse nome: um segundo titulo para o mesmo
    # texto so competia com ele.
    return (
        f'{raw_text}\n\n'
        'INVENTÁRIO ESTRUTURADO DETERMINÍSTICO. Preserve cada unidade e mantenha seu id:\n'
        f'{inventory}'
    )