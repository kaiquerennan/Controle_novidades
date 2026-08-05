from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import re
import time
from typing import Any, Iterator, Sequence

from core.config.settings import OCCURRENCE_DB_QUERY_LIMIT
from services.occurrence_db_config_service import (
    OccurrenceDatabaseConfig,
    OccurrenceDatabaseConfigService,
)


logger = logging.getLogger('EMC Novidade Visual IA')

# O aplicativo apenas consulta as ocorrencias; qualquer escrita e bloqueada
# antes de chegar ao servidor para que o banco de origem nunca seja alterado.
READ_ONLY_STATEMENTS = frozenset({'select', 'with'})

OCCURRENCE_TABLE_HINTS = ('ocorrenc', 'chamado', 'ticket', 'atendiment', 'incident', 'oc_')

_IDENTIFIER_PATTERN = re.compile(r'^[A-Za-z0-9_$#@]{1,128}$')


class OccurrenceDatabaseError(RuntimeError):
    pass


@dataclass(slots=True)
class ConnectionCheck:
    ok: bool
    message: str
    server_version: str = ''
    database: str = ''
    login: str = ''
    latency_seconds: float = 0.0


@dataclass(slots=True)
class TableColumn:
    name: str
    data_type: str
    nullable: bool
    max_length: int = 0


@dataclass(slots=True)
class TableSummary:
    name: str
    schema: str = 'dbo'
    estimated_rows: int = 0
    columns: list[TableColumn] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f'{self.schema}.{self.name}'


def load_driver() -> Any:
    """Importa o pyodbc somente quando ele e realmente necessario."""
    try:
        import pyodbc
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise OccurrenceDatabaseError(
            'Driver ODBC indisponivel. Instale pyodbc na .venv do projeto.'
        ) from exc
    return pyodbc


def validate_identifier(name: str) -> str:
    """Aceita apenas nomes simples de tabela/schema, pois eles nao podem ir por parametro."""
    candidate = str(name or '').strip().strip('[]')
    if not _IDENTIFIER_PATTERN.match(candidate):
        raise OccurrenceDatabaseError(f'Nome de tabela ou schema invalido: {name!r}.')
    return candidate


def split_table_name(table: str) -> tuple[str, str]:
    """Aceita 'tabela' ou 'schema.tabela' e valida as duas partes."""
    parts = str(table or '').strip().split('.')
    if len(parts) == 1:
        return 'dbo', validate_identifier(parts[0])
    if len(parts) == 2:
        return validate_identifier(parts[0]), validate_identifier(parts[1])
    raise OccurrenceDatabaseError(f'Nome de tabela invalido: {table!r}.')


def assert_read_only(query: str) -> str:
    statement = str(query or '').strip().rstrip(';').strip()
    if not statement:
        raise OccurrenceDatabaseError('Consulta vazia.')
    if ';' in statement:
        raise OccurrenceDatabaseError('Envie apenas uma instrucao por consulta.')
    first_word = statement.split(None, 1)[0].lower()
    if first_word not in READ_ONLY_STATEMENTS:
        raise OccurrenceDatabaseError(
            'Somente leitura e permitida no banco de ocorrencias; '
            f'instrucao recusada: {first_word.upper()}.'
        )
    return statement


class OccurrenceDatabase:

    def __init__(
        self,
        config: OccurrenceDatabaseConfig | None = None,
        *,
        config_service: OccurrenceDatabaseConfigService | None = None,
        driver_module: Any | None = None,
    ) -> None:
        self.config_service = config_service or OccurrenceDatabaseConfigService()
        self.config = config if config is not None else self.config_service.load()
        self._driver_module = driver_module

    @property
    def driver_module(self) -> Any:
        if self._driver_module is None:
            self._driver_module = load_driver()
        return self._driver_module

    def available_drivers(self) -> list[str]:
        try:
            drivers = list(self.driver_module.drivers())
        except Exception:  # pragma: no cover - depende do gerenciador ODBC
            return []
        return [name for name in drivers if 'SQL Server' in name]

    def resolve_driver(self) -> str:
        """Usa o driver configurado ou o mais recente instalado na maquina."""
        configured = str(self.config.odbc_driver or '').strip()
        if configured:
            return configured
        available = self.available_drivers()
        if not available:
            raise OccurrenceDatabaseError(
                'Nenhum driver ODBC de SQL Server encontrado nesta maquina. '
                'Instale o "ODBC Driver 18 for SQL Server" ou informe o driver na configuracao.'
            )
        for preferred in ('ODBC Driver 18', 'ODBC Driver 17', 'ODBC Driver 13', 'ODBC Driver 11'):
            for name in available:
                if name.startswith(preferred):
                    return name
        return available[0]

    def connection_string(self) -> str:
        missing = self.config_service.missing_fields(self.config)
        if missing:
            raise OccurrenceDatabaseError(
                'Configuracao do banco incompleta: ' + ', '.join(missing) + '.'
            )
        password = self.config_service.decrypt_password(self.config)
        parts = [
            f'DRIVER={{{self.resolve_driver()}}}',
            f'SERVER={self.config.host},{self.config.port}',
            f'DATABASE={self.config.database}',
            f'UID={self.config.user}',
            f'PWD={password}',
            f'Connection Timeout={self.config.connect_timeout_seconds}',
            'APP=EMC Novidade Visual IA',
        ]
        if self.config.encrypt:
            parts.append('Encrypt=yes')
        if self.config.trust_server_certificate:
            parts.append('TrustServerCertificate=yes')
        return ';'.join(parts) + ';'

    @contextmanager
    def connection(self) -> Iterator[Any]:
        connection_string = self.connection_string()
        try:
            connection = self.driver_module.connect(
                connection_string,
                timeout=self.config.connect_timeout_seconds,
                readonly=True,
            )
        except Exception as exc:
            raise self._wrap_error(exc, 'abrir a conexao') from exc
        try:
            yield connection
        finally:
            try:
                connection.close()
            except Exception:  # pragma: no cover - encerramento nao deve mascarar erro anterior
                logger.warning('Falha ao fechar a conexao com o banco de ocorrencias.')

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        with self.connection() as connection:
            cursor = connection.cursor()
            try:
                yield cursor
            finally:
                cursor.close()

    def fetch_all(
        self,
        query: str,
        params: Sequence[Any] | None = None,
        *,
        limit: int | None = OCCURRENCE_DB_QUERY_LIMIT,
    ) -> list[dict[str, Any]]:
        statement = assert_read_only(query)
        with self.cursor() as cursor:
            try:
                cursor.execute(statement, tuple(params or ()))
                columns = [column[0] for column in cursor.description or ()]
                rows = cursor.fetchmany(limit) if limit else cursor.fetchall()
            except Exception as exc:
                raise self._wrap_error(exc, 'executar a consulta') from exc
        return [dict(zip(columns, row)) for row in rows]

    def fetch_one(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> dict[str, Any] | None:
        rows = self.fetch_all(query, params, limit=1)
        return rows[0] if rows else None

    def test_connection(self) -> ConnectionCheck:
        started = time.perf_counter()
        try:
            row = self.fetch_one(
                'SELECT @@VERSION AS versao, DB_NAME() AS banco, SUSER_NAME() AS login'
            )
        except OccurrenceDatabaseError as exc:
            return ConnectionCheck(False, str(exc), latency_seconds=time.perf_counter() - started)
        latency = time.perf_counter() - started
        versao = str((row or {}).get('versao') or '').splitlines()
        return ConnectionCheck(
            ok=True,
            message=f'Conexao concluida com {self.config.describe()} em {latency:.2f} s.',
            server_version=versao[0].strip() if versao else '',
            database=str((row or {}).get('banco') or ''),
            login=str((row or {}).get('login') or ''),
            latency_seconds=latency,
        )

    def list_tables(self) -> list[TableSummary]:
        rows = self.fetch_all(
            'SELECT s.name AS esquema, t.name AS tabela,'
            ' SUM(CASE WHEN p.index_id IN (0, 1) THEN p.rows ELSE 0 END) AS linhas'
            ' FROM sys.tables AS t'
            ' INNER JOIN sys.schemas AS s ON s.schema_id = t.schema_id'
            ' LEFT JOIN sys.partitions AS p ON p.object_id = t.object_id'
            ' GROUP BY s.name, t.name'
            ' ORDER BY s.name, t.name',
            limit=None,
        )
        return [
            TableSummary(
                name=str(row.get('tabela') or ''),
                schema=str(row.get('esquema') or 'dbo'),
                estimated_rows=int(row.get('linhas') or 0),
            )
            for row in rows
        ]

    def describe_table(self, table: str) -> TableSummary:
        schema, name = split_table_name(table)
        rows = self.fetch_all(
            'SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, CHARACTER_MAXIMUM_LENGTH'
            ' FROM INFORMATION_SCHEMA.COLUMNS'
            ' WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?'
            ' ORDER BY ORDINAL_POSITION',
            (schema, name),
            limit=None,
        )
        if not rows:
            raise OccurrenceDatabaseError(
                f'Tabela nao encontrada no banco configurado: {schema}.{name}.'
            )
        return TableSummary(
            name=name,
            schema=schema,
            columns=[
                TableColumn(
                    name=str(row.get('COLUMN_NAME') or ''),
                    data_type=str(row.get('DATA_TYPE') or ''),
                    nullable=str(row.get('IS_NULLABLE') or '').upper() == 'YES',
                    max_length=int(row.get('CHARACTER_MAXIMUM_LENGTH') or 0),
                )
                for row in rows
            ],
        )

    def discover_occurrence_tables(
        self,
        hints: Sequence[str] = OCCURRENCE_TABLE_HINTS,
    ) -> list[TableSummary]:
        """Sugere as tabelas candidatas a conter ocorrencias, pelo nome."""
        normalized = [hint.lower() for hint in hints]
        return [
            table
            for table in self.list_tables()
            if any(hint in table.name.lower() for hint in normalized)
        ]

    def sample_rows(self, table: str, limit: int = 5) -> list[dict[str, Any]]:
        schema, name = split_table_name(table)
        size = max(1, min(OCCURRENCE_DB_QUERY_LIMIT, int(limit)))
        columns = ', '.join(f'[{column.name}]' for column in self.describe_table(f'{schema}.{name}').columns)
        return self.fetch_all(
            f'SELECT TOP {size} {columns} FROM [{schema}].[{name}]',
            limit=size,
        )

    def close(self) -> None:
        """Mantido por simetria: o pyodbc devolve as conexoes ao pool do ODBC."""
        return None

    def _wrap_error(self, exc: BaseException, action: str) -> OccurrenceDatabaseError:
        detail = sanitize_database_error(exc, self.config, self.config_service)
        logger.error('Erro ao %s no banco %s: %s', action, self.config.describe(), detail)
        return OccurrenceDatabaseError(f'Nao foi possivel {action}: {detail}')


def sanitize_database_error(
    exc: BaseException,
    config: OccurrenceDatabaseConfig,
    config_service: OccurrenceDatabaseConfigService | None = None,
) -> str:
    """Remove usuario e senha da mensagem do driver antes de exibir ou registrar."""
    message = str(exc).strip() or exc.__class__.__name__
    secrets = [config.user]
    if config_service is not None and config.has_password:
        try:
            secrets.append(config_service.decrypt_password(config))
        except Exception:  # pragma: no cover - sem senha legivel nao ha o que mascarar
            pass
    for secret in secrets:
        if secret:
            message = message.replace(secret, '***')
    return message


__all__ = [
    'ConnectionCheck',
    'OCCURRENCE_TABLE_HINTS',
    'OccurrenceDatabase',
    'OccurrenceDatabaseError',
    'READ_ONLY_STATEMENTS',
    'TableColumn',
    'TableSummary',
    'assert_read_only',
    'load_driver',
    'sanitize_database_error',
    'split_table_name',
    'validate_identifier',
]
