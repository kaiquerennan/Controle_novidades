"""Notas técnicas: lidas de um arquivo Markdown que a equipe edita à mão.

O arquivo é ``conteudo/notas_tecnicas.md``. Cada nota começa numa linha
``## Título``. Logo abaixo do título, até a primeira linha em branco, vêm campos
opcionais no formato ``Campo: valor``; depois da linha em branco, o texto livre
da nota, em Markdown. Exemplo:

    ## NT 2025.002 - Reforma Tributária (IBS/CBS)
    Data: 01/10/2025
    Link: https://www.nfe.fazenda.gov.br/...
    Sistema: NF-e / NFC-e
    Impacto: Alto

    Texto da nota. Aceita **negrito**, listas com "-" e links.

``Data`` ordena a lista (mais recente primeiro) e ``Link`` vira o botão de abrir
a nota; qualquer outro campo aparece como etiqueta. O arquivo é relido a cada
abertura da tela, então uma edição aparece sem reiniciar o aplicativo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
import re

from services.image_service import PROJECT_ROOT, safe_path


NOTES_PATH = PROJECT_ROOT / 'conteudo' / 'notas_tecnicas.md'

_FIELD_PATTERN = re.compile(r'^([A-Za-zÀ-ÿ][\wÀ-ÿ /()-]{0,30}):\s*(.*)$')
_DATE_FORMATS = ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y')


@dataclass(slots=True)
class TechnicalNote:
    title: str
    body: str = ''
    published: date | None = None
    link: str = ''
    tags: list[tuple[str, str]] = field(default_factory=list)

    @property
    def date_label(self) -> str:
        return self.published.strftime('%d/%m/%Y') if self.published else ''

    def matches(self, termo: str) -> bool:
        termo = termo.strip().casefold()
        if not termo:
            return True
        texto = ' '.join(
            [self.title, self.body] + [f'{chave} {valor}' for chave, valor in self.tags]
        )
        return termo in texto.casefold()


def _parse_date(value: str) -> date | None:
    for formato in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), formato).date()
        except ValueError:
            continue
    return None


def _parse_block(title: str, lines: list[str]) -> TechnicalNote:
    nota = TechnicalNote(title=title.strip())
    corpo_inicio = 0
    # Campos só valem colados ao título: depois da primeira linha em branco,
    # "Atenção: ..." é texto da nota, não um campo.
    for indice, linha in enumerate(lines):
        if not linha.strip():
            corpo_inicio = indice + 1
            break
        campo = _FIELD_PATTERN.match(linha.strip())
        if campo is None:
            corpo_inicio = indice
            break
        chave, valor = campo.group(1).strip(), campo.group(2).strip()
        if not valor:
            continue
        chave_normal = chave.casefold()
        if chave_normal == 'data':
            nota.published = _parse_date(valor)
            if nota.published is None:
                nota.tags.append((chave, valor))
        elif chave_normal == 'link':
            nota.link = valor
        else:
            nota.tags.append((chave, valor))
    else:
        corpo_inicio = len(lines)
    nota.body = '\n'.join(lines[corpo_inicio:]).strip()
    return nota


def parse_notes(text: str) -> list[TechnicalNote]:
    """Separa o arquivo em notas; o que vem antes do primeiro ``##`` é ignorado."""
    notas: list[TechnicalNote] = []
    titulo: str | None = None
    linhas: list[str] = []
    for linha in text.splitlines():
        if linha.startswith('## '):
            if titulo is not None:
                notas.append(_parse_block(titulo, linhas))
            titulo, linhas = linha[3:], []
        elif titulo is not None:
            linhas.append(linha)
    if titulo is not None:
        notas.append(_parse_block(titulo, linhas))
    notas = [nota for nota in notas if nota.title]
    # Mais recente primeiro; sem data, ficam no fim na ordem do arquivo.
    com_data = sorted(
        (nota for nota in notas if nota.published), key=lambda nota: nota.published,
        reverse=True,
    )
    return com_data + [nota for nota in notas if not nota.published]


def load_notes(path: Path | None = None) -> list[TechnicalNote]:
    alvo = safe_path(path or NOTES_PATH)
    if not alvo.is_file():
        return []
    # utf-8-sig aceita o arquivo salvo pelo Bloco de Notas com BOM.
    return parse_notes(alvo.read_text(encoding='utf-8-sig'))


__all__ = ['NOTES_PATH', 'TechnicalNote', 'load_notes', 'parse_notes']
