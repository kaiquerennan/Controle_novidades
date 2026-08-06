from __future__ import annotations

from datetime import datetime
from typing import Any

from services.novelty_history_service import ORIGIN_DATABASE, LaunchedNovelty
from services.occurrence_database_service import OccurrenceDatabase
from services.occurrence_query_service import (
    LIKE_ESCAPE,
    escape_like,
    parse_filter_date,
)
from services.text_sanitizer import html_to_text


NOVELTY_TABLE = 'ITENS_NOVIDADES_EMC'
DEFAULT_LIMIT = 500

_BASE_QUERY = (
    'SELECT ine.ITN_DATA_ALTERACAO, ine.OCO_NUMERO, ine.ITN_TITULO, ine.ITN_DESCRICAO'
    f' FROM {NOVELTY_TABLE} ine'
)

# Clausulas sem o "AND": elas sao unidas no final, para que qualquer combinacao
# de filtros gere uma query valida.
_FROM_DATE = 'ine.ITN_DATA_ALTERACAO >= ?'
# O ultimo dia entra inteiro: comparar com a data crua cortaria tudo o que foi
# alterado depois da meia-noite dele.
_TO_DATE = 'ine.ITN_DATA_ALTERACAO < DATEADD(DAY, 1, ?)'
_SEARCH_BY_TEXT = '(ine.ITN_TITULO LIKE ? ESCAPE ? OR ine.ITN_DESCRICAO LIKE ? ESCAPE ?)'
_SEARCH_BY_NUMBER = (
    '(ine.OCO_NUMERO = ? OR ine.ITN_TITULO LIKE ? ESCAPE ?'
    ' OR ine.ITN_DESCRICAO LIKE ? ESCAPE ?)'
)
_ORDER = ' ORDER BY ine.ITN_DATA_ALTERACAO DESC'


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    data = parse_filter_date(value)
    return datetime(data.year, data.month, data.day) if data else None


class LaunchedNoveltyQueryService:
    """Novidades ja publicadas no cadastro da EMC.

    Sao somente leitura: o aplicativo apenas as exibe ao lado das que ele proprio
    exportou, sem gravar nada nesta tabela.
    """

    def __init__(self, database: OccurrenceDatabase | None = None) -> None:
        self.database = database or OccurrenceDatabase()

    def list_novelties(
        self,
        *,
        search: str = '',
        start_date: Any = None,
        end_date: Any = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[LaunchedNovelty]:
        term = str(search or '').strip()
        # A ordem das clausulas define a ordem dos parametros.
        clauses: list[str] = []
        params: list[Any] = []

        inicio = parse_filter_date(start_date)
        fim = parse_filter_date(end_date)
        if inicio is not None:
            clauses.append(_FROM_DATE)
            params.append(inicio)
        if fim is not None:
            clauses.append(_TO_DATE)
            params.append(fim)
        if term:
            pattern = f'%{escape_like(term)}%'
            if term.isdigit():
                clauses.append(_SEARCH_BY_NUMBER)
                params.extend(
                    [int(term), pattern, LIKE_ESCAPE, pattern, LIKE_ESCAPE]
                )
            else:
                clauses.append(_SEARCH_BY_TEXT)
                params.extend([pattern, LIKE_ESCAPE, pattern, LIKE_ESCAPE])

        query = _BASE_QUERY
        if clauses:
            query += ' WHERE ' + ' AND '.join(clauses)
        rows = self.database.fetch_all(query + _ORDER, params, limit=limit)
        return [self.build_novelty(row) for row in rows]

    @staticmethod
    def build_novelty(row: dict[str, Any]) -> LaunchedNovelty:
        try:
            numero = int(row.get('OCO_NUMERO') or 0)
        except (TypeError, ValueError):
            numero = 0
        return LaunchedNovelty(
            # O titulo tambem passa pelo conversor: ele costuma vir com entidades
            # (&amp;, &nbsp;) mesmo quando nao tem tag alguma.
            title=html_to_text(row.get('ITN_TITULO')),
            created_at=_as_datetime(row.get('ITN_DATA_ALTERACAO')),
            text=html_to_text(row.get('ITN_DESCRICAO')),
            occurrence_number=numero,
            origin=ORIGIN_DATABASE,
        )


__all__ = [
    'DEFAULT_LIMIT',
    'NOVELTY_TABLE',
    'LaunchedNoveltyQueryService',
]
