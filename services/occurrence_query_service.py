from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import re
import unicodedata
from typing import Any

from services.occurrence_database_service import OccurrenceDatabase


# A view ja entrega somente as ocorrencias de mudanca, com todo o contexto
OCCURRENCE_VIEW = 'VW_OCORRENCIAS_MUDANCA'
DEFAULT_MONTHS = 3
DEFAULT_LIMIT = 500

_BASE_QUERY = (
    'SELECT NUMERO_OCORRENCIA, CODIGO_CLIENTE, DATA_ABERTURA, ULTIMA_ALTERACAO, JSON_COMPLETO'
    f' FROM {OCCURRENCE_VIEW}'
    ' WHERE ULTIMA_ALTERACAO >= DATEADD(MONTH, ?, GETDATE())'
)

_SEARCH_BY_TEXT = ' AND JSON_COMPLETO LIKE ? ESCAPE ?'
_SEARCH_BY_NUMBER_OR_TEXT = ' AND (NUMERO_OCORRENCIA = ? OR JSON_COMPLETO LIKE ? ESCAPE ?)'
_ORDER = ' ORDER BY ULTIMA_ALTERACAO DESC'

LIKE_ESCAPE = '\\'

# A DESCRICAO vem com rotulos em caixa alta: CAMINHO, SOLICITACAO, JUSTIFICATIVA,
# ATENDIMENTO, OBSERVACAO. Nem toda ocorrencia traz todos eles.
_SECTION_PATTERN = re.compile(
    r'^[ \t]*([A-ZÀ-Ú][A-ZÀ-Ú0-9 ./_-]{2,40}?)[ \t]*:[ \t]*',
    re.MULTILINE,
)


# Secoes da DESCRICAO que descrevem a mudanca em si.
CHANGE_SECTIONS = ('CAMINHO', 'SOLICITACAO', 'JUSTIFICATIVA', 'ATENDIMENTO', 'PROBLEMA', 'MOTIVO')

# Linhas de controle do chamado: servem para a equipe, nunca para o cliente
# final. Sao descartadas antes de o texto chegar na IA.
ADMINISTRATIVE_PATTERNS = tuple(
    re.compile(padrao, re.IGNORECASE)
    for padrao in (
        r'risco comercial',
        r'altera[c]ao de status',
        r'teste[s]? realizado',
        r'realizar teste',
        r'favor verificar',
        r'versao testada',
        r'em homologacao',
        r'disponivel para teste',
        r'disponibilizado para teste',
        r'^::.*::$',
        r'novo arquivo disponibilizado',
        r'arquivo compactado',
        r'novo instalador',
        r'instalador do',
        r'\.(zip|exe|rar|7z|msi)\b',
        # Numeros de chamado citados no corpo da observacao.
        r'\b(oc|ic|os)\s*\d{4,}',
        # Frases de acompanhamento da equipe.
        r'^analisando',
        r'nosso maior problema',
        r'gentileza analisar',
        r'\b(cache[s]?|buffer[s]?|lock[s]?|thread[s]?|heap|swap)\b',
        # A URL pode vir precedida do marcador de lista da propria observacao.
        r'^https?://\S+$',
        # Ambientes de homologacao e arquivos de teste compartilhados. Links
        # publicos da EMC continuam passando: eles servem ao cliente final.
        r'https?://\S*(hml|homolog)',
        r'https?://\S*(drive|docs)\.google\.',
        r'https?://\S*(dropbox|wetransfer|1drv\.ms|onedrive)',
    )
)

_LEADING_BULLET = re.compile(r'^[-•*–—\s]+')


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize('NFKD', value)
    return ''.join(char for char in normalized if not unicodedata.combining(char))


def section_key(label: str) -> str:
    return _strip_accents(str(label or '')).upper().strip()


def is_administrative_line(line: str) -> bool:
    """Linha de controle do chamado, sem valor para o cliente final."""
    alvo = _strip_accents(str(line or '')).strip()
    if not alvo:
        return True
    return any(padrao.search(alvo) for padrao in ADMINISTRATIVE_PATTERNS)


def relevant_lines(text: str, seen: set[str] | None = None) -> list[str]:
    """Remove linhas administrativas e repetidas, preservando a ordem."""
    vistas = seen if seen is not None else set()
    mantidas: list[str] = []
    for linha in str(text or '').splitlines():
        # O marcador de lista da origem sai antes da checagem: senao "- URL"
        # escaparia do padrao ancorado no inicio da linha.
        limpa = _LEADING_BULLET.sub('', linha).strip()
        if is_administrative_line(limpa):
            continue
        chave = _strip_accents(limpa).casefold()
        if chave in vistas:
            continue
        vistas.add(chave)
        mantidas.append(limpa)
    return mantidas


def parse_description_sections(description: str) -> dict[str, str]:
    """Separa a DESCRICAO nos rotulos em caixa alta que a equipe usa."""
    texto = str(description or '').replace('\r\n', '\n').replace('\r', '\n')
    matches = list(_SECTION_PATTERN.finditer(texto))
    if not matches:
        return {}
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(texto)
        chave = section_key(match.group(1))
        conteudo = texto[start:end].strip()
        if chave and conteudo:
            sections.setdefault(chave, conteudo)
    return sections


@dataclass(slots=True)
class OccurrenceObservation:
    date: str = ''
    text: str = ''
    user: str = ''


@dataclass(slots=True)
class OccurrenceSummary:
    number: int
    client_code: int = 0
    client_name: str = ''
    opened_at: datetime | None = None
    changed_at: datetime | None = None
    system: str = ''
    module: str = ''
    description: str = ''
    complement: str = ''
    observations: list[OccurrenceObservation] = field(default_factory=list)
    sections: dict[str, str] = field(default_factory=dict)
    raw_json: str = ''

    @property
    def solicitation(self) -> str:
        return self.sections.get('SOLICITACAO', '')

    @property
    def justification(self) -> str:
        return self.sections.get('JUSTIFICATIVA', '')

    @property
    def path(self) -> str:
        return self.sections.get('CAMINHO', '')

    @property
    def changed_at_label(self) -> str:
        return self.changed_at.strftime('%d/%m/%Y %H:%M') if self.changed_at else ''

    @property
    def opened_at_label(self) -> str:
        return self.opened_at.strftime('%d/%m/%Y') if self.opened_at else ''

    def as_raw_text(self) -> str:
        """Contexto da ocorrencia para alimentar a geração.

        A mudanca em si vem das secoes da DESCRICAO. O historico do chamado
        entra depois, marcado como registro interno, porque mistura discussao
        da equipe, testes e controle de status com informacao funcional. As
        linhas puramente administrativas sao descartadas aqui, antes de chegar
        na IA.
        """
        partes: list[str] = [f'OCORRÊNCIA: {self.number}']
        if self.system:
            partes.append(f'SISTEMA: {self.system}')
        if self.module:
            partes.append(f'MÓDULO: {self.module}')

        vistas: set[str] = set()
        mudanca: list[str] = []
        for chave in CHANGE_SECTIONS:
            conteudo = self.sections.get(chave, '')
            linhas = relevant_lines(conteudo, vistas)
            if linhas:
                mudanca.append(f'{chave}:\n' + '\n'.join(linhas))
        if not mudanca:
            linhas = relevant_lines(self.description, vistas)
            if linhas:
                mudanca.append('\n'.join(linhas))
        if mudanca:
            partes.append('\nA MUDANÇA:\n\n' + '\n\n'.join(mudanca))

        complemento = relevant_lines(self.complement, vistas)
        if complemento:
            partes.append('\nCOMPLEMENTO:\n' + '\n'.join(complemento))

        historico: list[str] = []
        for item in self.observations:
            historico.extend(relevant_lines(item.text, vistas))
        if historico:
            partes.append(
                '\nREGISTRO INTERNO DO CHAMADO (contexto para entender a mudança; '
                'não publique este conteúdo, use apenas o que for comportamento '
                'efetivo da funcionalidade):\n'
                + '\n'.join(f'- {linha}' for linha in historico)
            )
        return '\n'.join(partes).strip()


def escape_like(term: str) -> str:
    """Neutraliza os curingas do LIKE para que a busca seja literal."""
    escaped = term.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
    for wildcard in ('%', '_', '['):
        escaped = escaped.replace(wildcard, LIKE_ESCAPE + wildcard)
    return escaped


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


class OccurrenceQueryService:
    """Consulta as ocorrencias de mudanca na view VW_OCORRENCIAS_MUDANCA."""

    def __init__(self, database: OccurrenceDatabase | None = None) -> None:
        self.database = database or OccurrenceDatabase()

    def list_changes(
        self,
        *,
        search: str = '',
        months: int = DEFAULT_MONTHS,
        limit: int = DEFAULT_LIMIT,
    ) -> list[OccurrenceSummary]:
        term = str(search or '').strip()
        window = max(1, min(120, int(months)))
        params: list[Any] = [-window]
        query = _BASE_QUERY
        if term:
            pattern = f'%{escape_like(term)}%'
            if term.isdigit():
                query += _SEARCH_BY_NUMBER_OR_TEXT
                params.extend([int(term), pattern, LIKE_ESCAPE])
            else:
                query += _SEARCH_BY_TEXT
                params.extend([pattern, LIKE_ESCAPE])
        rows = self.database.fetch_all(query + _ORDER, params, limit=limit)
        return [self.build_summary(row) for row in rows]

    def get(self, number: int) -> OccurrenceSummary | None:
        row = self.database.fetch_one(
            'SELECT NUMERO_OCORRENCIA, CODIGO_CLIENTE, DATA_ABERTURA, ULTIMA_ALTERACAO,'
            f' JSON_COMPLETO FROM {OCCURRENCE_VIEW} WHERE NUMERO_OCORRENCIA = ?',
            (int(number),),
        )
        return self.build_summary(row) if row else None

    @staticmethod
    def build_summary(row: dict[str, Any]) -> OccurrenceSummary:
        raw_json = str(row.get('JSON_COMPLETO') or '')
        try:
            payload = json.loads(raw_json) if raw_json else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        description = str(payload.get('DESCRICAO') or '')
        observations = []
        for item in payload.get('OBSERVACOES_RELEVANTES') or []:
            if not isinstance(item, dict):
                continue
            observations.append(
                OccurrenceObservation(
                    date=str(item.get('DATA_OBSERVACAO') or ''),
                    text=str(item.get('TEXTO_OBSERVACAO') or ''),
                    user=str(item.get('USUARIO_OBSERVACAO') or ''),
                )
            )
        return OccurrenceSummary(
            number=int(row.get('NUMERO_OCORRENCIA') or payload.get('NUMERO_OCORRENCIA') or 0),
            client_code=int(row.get('CODIGO_CLIENTE') or 0),
            client_name=str(payload.get('NOME_CLIENTE') or ''),
            opened_at=_parse_datetime(row.get('DATA_ABERTURA') or payload.get('DATA_ABERTURA')),
            changed_at=_parse_datetime(
                row.get('ULTIMA_ALTERACAO') or payload.get('ULTIMA_ALTERACAO')
            ),
            system=str(payload.get('SISTEMA') or ''),
            module=str(payload.get('MODULO') or ''),
            description=description,
            complement=str(payload.get('COMPLEMENTO') or ''),
            observations=observations,
            sections=parse_description_sections(description),
            raw_json=raw_json,
        )


__all__ = [
    'ADMINISTRATIVE_PATTERNS',
    'CHANGE_SECTIONS',
    'DEFAULT_LIMIT',
    'DEFAULT_MONTHS',
    'is_administrative_line',
    'relevant_lines',
    'OCCURRENCE_VIEW',
    'OccurrenceObservation',
    'OccurrenceQueryService',
    'OccurrenceSummary',
    'escape_like',
    'parse_description_sections',
    'section_key',
]
