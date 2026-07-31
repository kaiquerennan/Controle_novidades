"""Configura e verifica a conexao com o SQL Server de ocorrencias.

Uso (sempre pelo Python da .venv do projeto):

    .venv\\Scripts\\python.exe configurar_banco.py configurar --host 191.252.197.44 --banco emcsistemas1 --usuario emc_homolog
    .venv\\Scripts\\python.exe configurar_banco.py testar
    .venv\\Scripts\\python.exe configurar_banco.py tabelas
    .venv\\Scripts\\python.exe configurar_banco.py colunas nome_da_tabela
    .venv\\Scripts\\python.exe configurar_banco.py amostra nome_da_tabela --linhas 5

A senha e solicitada de forma oculta e gravada protegida pelo DPAPI da maquina
em data/occurrence_db.local.json. Ela nunca aparece na tela nem nos logs.
"""

from __future__ import annotations

import argparse
from getpass import getpass
import sys

from services.occurrence_database_service import (
    OccurrenceDatabase,
    OccurrenceDatabaseError,
)
from services.occurrence_db_config_service import (
    OccurrenceDatabaseConfig,
    OccurrenceDatabaseConfigService,
)


def _configurar(args: argparse.Namespace) -> int:
    service = OccurrenceDatabaseConfigService()
    current = service.load()
    config = OccurrenceDatabaseConfig(
        host=args.host or current.host,
        port=args.porta or current.port,
        database=args.banco or current.database,
        user=args.usuario or current.user,
        odbc_driver=args.driver or current.odbc_driver,
        connect_timeout_seconds=args.timeout or current.connect_timeout_seconds,
        encrypt=current.encrypt if args.criptografar is None else args.criptografar,
        trust_server_certificate=current.trust_server_certificate,
        occurrence_table=args.tabela or current.occurrence_table,
    )
    password = ''
    if args.pedir_senha or not current.has_password:
        password = getpass('Senha do banco (nao sera exibida): ')
    saved = service.save(config, password=password)
    status = service.status(saved)
    print(f'Configuracao gravada em {service.path}')
    print(f'Servidor: {saved.describe()}')
    print(f'Usuario: {saved.user or "(nao informado)"}')
    print(f'Senha protegida: {"sim" if saved.has_password else "nao"}')
    print(f'Situacao: {status.message}')
    return 0 if status.valid else 1


def _testar(_: argparse.Namespace) -> int:
    database = OccurrenceDatabase()
    try:
        check = database.test_connection()
    finally:
        database.close()
    print(check.message)
    if check.ok:
        print(f'Driver ODBC: {database.resolve_driver()}')
        print(f'Versao do servidor: {check.server_version}')
        print(f'Banco em uso: {check.database}')
        print(f'Login: {check.login}')
    return 0 if check.ok else 1


def _tabelas(args: argparse.Namespace) -> int:
    database = OccurrenceDatabase()
    try:
        tables = database.list_tables() if args.todas else database.discover_occurrence_tables()
    finally:
        database.close()
    if not tables:
        print('Nenhuma tabela encontrada com os termos de ocorrencia. Use --todas para listar tudo.')
        return 1
    print(f'{len(tables)} tabela(s):')
    for table in tables:
        print(f'  {table.full_name} (~{table.estimated_rows} linhas)')
    return 0


def _colunas(args: argparse.Namespace) -> int:
    database = OccurrenceDatabase()
    try:
        summary = database.describe_table(args.tabela)
    finally:
        database.close()
    print(f'Tabela {summary.full_name}: {len(summary.columns)} coluna(s)')
    for column in summary.columns:
        tamanho = f'({column.max_length})' if column.max_length else ''
        obrigatorio = '' if column.nullable else ' NOT NULL'
        print(f'  {column.name}: {column.data_type}{tamanho}{obrigatorio}')
    return 0


def _amostra(args: argparse.Namespace) -> int:
    database = OccurrenceDatabase()
    try:
        rows = database.sample_rows(args.tabela, limit=args.linhas)
    finally:
        database.close()
    if not rows:
        print(f'A tabela {args.tabela} nao retornou linhas.')
        return 1
    for index, row in enumerate(rows, start=1):
        print(f'--- linha {index} ---')
        for key, value in row.items():
            texto = str(value)
            if len(texto) > 200:
                texto = texto[:200] + '...'
            print(f'  {key}: {texto}')
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Banco de ocorrencias da EMC.')
    subparsers = parser.add_subparsers(dest='comando', required=True)

    configurar = subparsers.add_parser('configurar', help='Grava host, banco, usuario e senha.')
    configurar.add_argument('--host')
    configurar.add_argument('--porta', type=int)
    configurar.add_argument('--banco')
    configurar.add_argument('--usuario')
    configurar.add_argument('--driver', help='Driver ODBC; em branco usa o mais recente instalado.')
    configurar.add_argument('--timeout', type=int, help='Tempo limite de conexao em segundos.')
    configurar.add_argument('--tabela', help='Tabela principal de ocorrencias, se ja conhecida.')
    configurar.add_argument(
        '--criptografar', dest='criptografar', action='store_true', default=None
    )
    configurar.add_argument('--sem-criptografia', dest='criptografar', action='store_false')
    configurar.add_argument('--pedir-senha', action='store_true', help='Solicita a senha novamente.')
    configurar.set_defaults(handler=_configurar)

    testar = subparsers.add_parser('testar', help='Abre a conexao e confirma o acesso.')
    testar.set_defaults(handler=_testar)

    tabelas = subparsers.add_parser('tabelas', help='Lista as tabelas candidatas a ocorrencias.')
    tabelas.add_argument('--todas', action='store_true', help='Lista todas as tabelas do banco.')
    tabelas.set_defaults(handler=_tabelas)

    colunas = subparsers.add_parser('colunas', help='Mostra as colunas de uma tabela.')
    colunas.add_argument('tabela')
    colunas.set_defaults(handler=_colunas)

    amostra = subparsers.add_parser('amostra', help='Mostra algumas linhas de uma tabela.')
    amostra.add_argument('tabela')
    amostra.add_argument('--linhas', type=int, default=5)
    amostra.set_defaults(handler=_amostra)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except OccurrenceDatabaseError as exc:
        print(f'ERRO: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
