from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from core.config.settings import (
    OCCURRENCE_DB_CONNECT_TIMEOUT_SECONDS,
    OCCURRENCE_DB_DEFAULT_PORT,
    OCCURRENCE_DB_ENVIRONMENTS,
)
from services.credential_protection import (
    CredentialProtectionError,
    WindowsMachineCredentialProtector,
)
from services.image_service import DATA_ROOT, safe_path


CONFIG_VERSION = 1
DEFAULT_CONFIG_PATH = DATA_ROOT / 'occurrence_db.local.json'
PASSWORD_PLACEHOLDER = 'Senha salva com protecao local'


@dataclass(slots=True)
class OccurrenceDatabaseConfig:
    host: str = ''
    port: int = OCCURRENCE_DB_DEFAULT_PORT
    database: str = ''
    user: str = ''
    odbc_driver: str = ''
    connect_timeout_seconds: int = OCCURRENCE_DB_CONNECT_TIMEOUT_SECONDS
    encrypt: bool = False
    trust_server_certificate: bool = True
    occurrence_table: str = ''
    encrypted_password: str = ''

    @property
    def has_password(self) -> bool:
        return bool(self.encrypted_password.strip())

    def public_dict(self) -> dict[str, Any]:
        return {
            'host': self.host,
            'port': self.port,
            'database': self.database,
            'user': self.user,
            'odbc_driver': self.odbc_driver,
            'connect_timeout_seconds': self.connect_timeout_seconds,
            'encrypt': self.encrypt,
            'trust_server_certificate': self.trust_server_certificate,
            'occurrence_table': self.occurrence_table,
        }

    def describe(self) -> str:
        """Identificacao segura para logs: nunca inclui usuario nem senha."""
        return f'{self.host},{self.port}/{self.database}' if self.host else 'banco nao configurado'


@dataclass(slots=True)
class OccurrenceDatabaseStatus:
    configured: bool
    valid: bool
    message: str
    missing_fields: list[str]


class OccurrenceDatabaseConfigService:
    """Le e grava a configuracao do banco de ocorrencias.

    A senha nunca fica em texto puro no disco: é protegida por DPAPI da
    maquina, no mesmo padrao usado pela integracao de WhatsApp. Variaveis de
    ambiente têm precedência sobre o arquivo, para permitir execucao em
    servidores sem arquivo local.
    """

    def __init__(
        self,
        path: Path | None = None,
        protector: WindowsMachineCredentialProtector | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.path = safe_path(path or DEFAULT_CONFIG_PATH)
        self.protector = protector or WindowsMachineCredentialProtector()
        self.environment = environment if environment is not None else os.environ

    def load(self) -> OccurrenceDatabaseConfig:
        return self._apply_environment(self._load_file())

    def _load_file(self) -> OccurrenceDatabaseConfig:
        if not self.path.is_file():
            return OccurrenceDatabaseConfig()
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8-sig'))
        except (OSError, json.JSONDecodeError):
            return OccurrenceDatabaseConfig()
        if not isinstance(payload, dict) or payload.get('version') != CONFIG_VERSION:
            return OccurrenceDatabaseConfig()
        return OccurrenceDatabaseConfig(
            host=str(payload.get('host') or ''),
            port=self.normalize_port(payload.get('port')),
            database=str(payload.get('database') or ''),
            user=str(payload.get('user') or ''),
            odbc_driver=str(payload.get('odbc_driver') or ''),
            connect_timeout_seconds=self.normalize_timeout(payload.get('connect_timeout_seconds')),
            encrypt=bool(payload.get('encrypt')),
            trust_server_certificate=bool(payload.get('trust_server_certificate', True)),
            occurrence_table=str(payload.get('occurrence_table') or ''),
            encrypted_password=str(payload.get('encrypted_password') or ''),
        )

    def _apply_environment(self, config: OccurrenceDatabaseConfig) -> OccurrenceDatabaseConfig:
        def read(field: str) -> str:
            return self.environment.get(OCCURRENCE_DB_ENVIRONMENTS[field], '').strip()

        host = read('host')
        port = read('port')
        database = read('database')
        user = read('user')
        password = read('password')
        odbc_driver = read('odbc_driver')
        if host:
            config.host = host
        if port:
            config.port = self.normalize_port(port)
        if database:
            config.database = database
        if user:
            config.user = user
        if odbc_driver:
            config.odbc_driver = odbc_driver
        if password:
            # Mantem a senha do ambiente apenas em memoria, protegida da mesma
            # forma que a persistida, para que o servico tenha um caminho unico.
            config.encrypted_password = self.protector.protect(password)
        return config

    def save(
        self,
        config: OccurrenceDatabaseConfig,
        *,
        password: str = '',
        keep_existing_password: bool = True,
    ) -> OccurrenceDatabaseConfig:
        current = self._load_file()
        encrypted_password = current.encrypted_password if keep_existing_password else ''
        if password.strip():
            encrypted_password = self.protector.protect(password.strip())
        normalized = self.normalize(config, encrypted_password=encrypted_password)
        payload: dict[str, Any] = {
            'version': CONFIG_VERSION,
            **normalized.public_dict(),
            'encrypted_password': normalized.encrypted_password,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = safe_path(self.path.with_suffix('.tmp'))
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)
        return normalized

    def remove(self) -> None:
        if self.path.is_file():
            self.path.unlink()

    def decrypt_password(self, config: OccurrenceDatabaseConfig | None = None) -> str:
        target = config or self.load()
        if not target.encrypted_password:
            return ''
        return self.protector.unprotect(target.encrypted_password)

    def status(self, config: OccurrenceDatabaseConfig | None = None) -> OccurrenceDatabaseStatus:
        target = config or self.load()
        missing = self.missing_fields(target)
        configured = any(
            str(value or '').strip()
            for key, value in target.public_dict().items()
            if key in {'host', 'database', 'user'}
        ) or target.has_password
        if missing:
            return OccurrenceDatabaseStatus(
                configured=configured,
                valid=False,
                message='Configuracao incompleta: ' + ', '.join(missing) + '.',
                missing_fields=missing,
            )
        return OccurrenceDatabaseStatus(True, True, 'Configuracao completa.', [])

    @classmethod
    def normalize(
        cls,
        config: OccurrenceDatabaseConfig,
        *,
        encrypted_password: str | None = None,
    ) -> OccurrenceDatabaseConfig:
        return OccurrenceDatabaseConfig(
            host=str(config.host or '').strip(),
            port=cls.normalize_port(config.port),
            database=str(config.database or '').strip(),
            user=str(config.user or '').strip(),
            odbc_driver=str(config.odbc_driver or '').strip(),
            connect_timeout_seconds=cls.normalize_timeout(config.connect_timeout_seconds),
            encrypt=bool(config.encrypt),
            trust_server_certificate=bool(config.trust_server_certificate),
            occurrence_table=str(config.occurrence_table or '').strip(),
            encrypted_password=(
                config.encrypted_password if encrypted_password is None else encrypted_password
            ),
        )

    @staticmethod
    def missing_fields(config: OccurrenceDatabaseConfig) -> list[str]:
        fields = []
        if not str(config.host or '').strip():
            fields.append('host do servidor')
        if not str(config.database or '').strip():
            fields.append('nome do banco')
        if not str(config.user or '').strip():
            fields.append('usuario')
        if not str(config.encrypted_password or '').strip():
            fields.append('senha')
        return fields

    @staticmethod
    def normalize_port(value: object) -> int:
        try:
            port = int(str(value).strip())
        except (TypeError, ValueError):
            return OCCURRENCE_DB_DEFAULT_PORT
        return min(65535, max(1, port))

    @staticmethod
    def normalize_timeout(value: object) -> int:
        try:
            timeout = int(float(value))
        except (TypeError, ValueError):
            return OCCURRENCE_DB_CONNECT_TIMEOUT_SECONDS
        return min(120, max(1, timeout))


__all__ = [
    'CONFIG_VERSION',
    'CredentialProtectionError',
    'DEFAULT_CONFIG_PATH',
    'OccurrenceDatabaseConfig',
    'OccurrenceDatabaseConfigService',
    'OccurrenceDatabaseStatus',
    'PASSWORD_PLACEHOLDER',
]
