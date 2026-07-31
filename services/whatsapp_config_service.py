from __future__ import annotations

from dataclasses import dataclass
import base64
import ctypes
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from services.image_service import DATA_ROOT, safe_path


CONFIG_VERSION = 2
DEFAULT_CONFIG_PATH = DATA_ROOT / 'whatsapp_config.local.json'
TOKEN_PLACEHOLDER = 'Token salvo com protecao local'
DEFAULT_PROVIDER = 'generic_json'
DEFAULT_TIMEOUT_SECONDS = 20.0
AUTH_SCHEMES = {'bearer', 'api_key_header'}
PROVIDER_LABELS = {
    'generic_json': 'API generica JSON',
}


class CredentialProtectionError(RuntimeError):
    pass


@dataclass(slots=True)
class WhatsAppConfig:
    provider: str = DEFAULT_PROVIDER
    base_url: str = ''
    send_endpoint: str = ''
    auth_scheme: str = 'bearer'
    auth_header_name: str = 'Authorization'
    sender_id: str = ''
    instance_name: str = ''
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    encrypted_token: str = ''
    validated_signature: str = ''

    @property
    def has_token(self) -> bool:
        return bool(self.encrypted_token.strip())

    def public_dict(self) -> dict[str, str | float]:
        return {
            'provider': self.provider,
            'base_url': self.base_url,
            'send_endpoint': self.send_endpoint,
            'auth_scheme': self.auth_scheme,
            'auth_header_name': self.auth_header_name,
            'sender_id': self.sender_id,
            'instance_name': self.instance_name,
            'timeout_seconds': self.timeout_seconds,
        }


@dataclass(slots=True)
class WhatsAppConfigStatus:
    configured: bool
    valid: bool
    message: str
    missing_fields: list[str]


class _Blob(ctypes.Structure):
    _fields_ = [
        ('cbData', ctypes.c_uint),
        ('pbData', ctypes.POINTER(ctypes.c_byte)),
    ]


class WindowsMachineCredentialProtector:
    CRYPTPROTECT_LOCAL_MACHINE = 0x4

    def protect(self, value: str) -> str:
        if os.name != 'nt':
            raise CredentialProtectionError('Protecao DPAPI esta disponivel apenas no Windows.')
        data = value.encode('utf-8')
        input_blob, _ = self._blob_from_bytes(data)
        output_blob = _Blob()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(input_blob), None, None, None, None,
            self.CRYPTPROTECT_LOCAL_MACHINE, ctypes.byref(output_blob),
        )
        if not ok:
            raise CredentialProtectionError('Nao foi possivel proteger o token.')
        try:
            protected = ctypes.string_at(output_blob.pbData, output_blob.cbData)
            return base64.b64encode(protected).decode('ascii')
        finally:
            ctypes.windll.kernel32.LocalFree(output_blob.pbData)

    def unprotect(self, value: str) -> str:
        if os.name != 'nt':
            raise CredentialProtectionError('Protecao DPAPI esta disponivel apenas no Windows.')
        protected = base64.b64decode(value.encode('ascii'), validate=True)
        input_blob, _ = self._blob_from_bytes(protected)
        output_blob = _Blob()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(input_blob), None, None, None, None, 0,
            ctypes.byref(output_blob),
        )
        if not ok:
            raise CredentialProtectionError('Nao foi possivel recuperar o token.')
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode('utf-8')
        finally:
            ctypes.windll.kernel32.LocalFree(output_blob.pbData)

    @staticmethod
    def _blob_from_bytes(data: bytes) -> tuple[_Blob, ctypes.Array]:
        buffer = ctypes.create_string_buffer(data)
        blob = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        return blob, buffer


class WhatsAppConfigService:
    def __init__(
        self,
        path: Path | None = None,
        protector: WindowsMachineCredentialProtector | None = None,
    ) -> None:
        self.path = safe_path(path or DEFAULT_CONFIG_PATH)
        self.protector = protector or WindowsMachineCredentialProtector()

    def load(self) -> WhatsAppConfig:
        if not self.path.is_file():
            return WhatsAppConfig()
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8-sig'))
        except (OSError, json.JSONDecodeError):
            return WhatsAppConfig()
        if not isinstance(payload, dict):
            return WhatsAppConfig()
        if payload.get('version') != CONFIG_VERSION:
            return self._migrate_legacy_payload(payload)
        return self._from_payload(payload)

    def _from_payload(self, payload: dict[str, Any]) -> WhatsAppConfig:
        return WhatsAppConfig(
            provider=str(payload.get('provider') or DEFAULT_PROVIDER),
            base_url=str(payload.get('base_url') or ''),
            send_endpoint=str(payload.get('send_endpoint') or ''),
            auth_scheme=str(payload.get('auth_scheme') or 'bearer'),
            auth_header_name=str(payload.get('auth_header_name') or 'Authorization'),
            sender_id=str(payload.get('sender_id') or ''),
            instance_name=str(payload.get('instance_name') or ''),
            timeout_seconds=self.normalize_timeout(payload.get('timeout_seconds')),
            encrypted_token=str(payload.get('encrypted_token') or ''),
            validated_signature=str(payload.get('validated_signature') or ''),
        )

    def _migrate_legacy_payload(self, payload: dict[str, Any]) -> WhatsAppConfig:
        phone_number_id = str(payload.get('phone_number_id') or '')
        version = str(payload.get('graph_api_version') or 'v25.0').strip().strip('/')
        endpoint = f'/{version}/{phone_number_id}/messages' if phone_number_id else ''
        return WhatsAppConfig(
            provider=DEFAULT_PROVIDER,
            base_url='https://graph.facebook.com' if endpoint else '',
            send_endpoint=endpoint,
            auth_scheme='bearer',
            auth_header_name='Authorization',
            sender_id=phone_number_id,
            instance_name=str(payload.get('business_account_id') or ''),
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            encrypted_token=str(payload.get('encrypted_token') or ''),
            validated_signature='',
        )

    def save(
        self,
        config: WhatsAppConfig,
        *,
        token: str = '',
        keep_existing_token: bool = True,
        mark_valid: bool = False,
    ) -> WhatsAppConfig:
        current = self.load()
        encrypted_token = current.encrypted_token if keep_existing_token else ''
        if token.strip():
            encrypted_token = self.protector.protect(token.strip())
        normalized = self.normalize(config, encrypted_token=encrypted_token)
        normalized.validated_signature = self.signature(normalized) if mark_valid else ''
        payload: dict[str, Any] = {
            'version': CONFIG_VERSION,
            **normalized.public_dict(),
            'encrypted_token': normalized.encrypted_token,
            'validated_signature': normalized.validated_signature,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = safe_path(self.path.with_suffix('.tmp'))
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)
        return normalized

    def remove(self) -> None:
        if self.path.is_file():
            self.path.unlink()

    def decrypt_token(self, config: WhatsAppConfig | None = None) -> str:
        target = config or self.load()
        if not target.encrypted_token:
            return ''
        return self.protector.unprotect(target.encrypted_token)

    def status(self, config: WhatsAppConfig | None = None) -> WhatsAppConfigStatus:
        target = config or self.load()
        missing = self.missing_fields(target)
        configured = any(str(value or '').strip() for value in target.public_dict().values()) or target.has_token
        if missing:
            return WhatsAppConfigStatus(
                configured=configured,
                valid=False,
                message='Configuracao incompleta: ' + ', '.join(missing) + '.',
                missing_fields=missing,
            )
        if target.validated_signature != self.signature(target):
            return WhatsAppConfigStatus(
                configured=True,
                valid=False,
                message='Configuracao ainda nao validada. Use Testar conexao.',
                missing_fields=[],
            )
        return WhatsAppConfigStatus(True, True, 'Configuracao validada.', [])

    @classmethod
    def normalize(cls, config: WhatsAppConfig, *, encrypted_token: str | None = None) -> WhatsAppConfig:
        auth_scheme = str(config.auth_scheme or 'bearer').strip().lower()
        if auth_scheme not in AUTH_SCHEMES:
            auth_scheme = 'bearer'
        header = str(config.auth_header_name or '').strip()
        if auth_scheme == 'bearer':
            header = 'Authorization'
        return WhatsAppConfig(
            provider=str(config.provider or DEFAULT_PROVIDER).strip() or DEFAULT_PROVIDER,
            base_url=cls.normalize_base_url(config.base_url),
            send_endpoint=cls.normalize_endpoint(config.send_endpoint),
            auth_scheme=auth_scheme,
            auth_header_name=header or 'X-API-Key',
            sender_id=str(config.sender_id or '').strip(),
            instance_name=str(config.instance_name or '').strip(),
            timeout_seconds=cls.normalize_timeout(config.timeout_seconds),
            encrypted_token=config.encrypted_token if encrypted_token is None else encrypted_token,
        )

    @staticmethod
    def signature(config: WhatsAppConfig) -> str:
        payload = json.dumps(
            {**config.public_dict(), 'encrypted_token': config.encrypted_token},
            ensure_ascii=False,
            separators=(',', ':'),
        )
        return sha256(payload.encode('utf-8')).hexdigest()

    @classmethod
    def missing_fields(cls, config: WhatsAppConfig) -> list[str]:
        target = cls.normalize(config)
        fields = []
        if not target.encrypted_token:
            fields.append('token ou chave de autenticacao')
        if not target.base_url:
            fields.append('URL base da API')
        if not target.send_endpoint:
            fields.append('endpoint de envio')
        if not target.auth_header_name:
            fields.append('cabecalho de autenticacao')
        if target.timeout_seconds <= 0:
            fields.append('timeout da requisicao')
        return fields

    @staticmethod
    def normalize_base_url(value: str) -> str:
        url = str(value or '').strip().rstrip('/')
        if not url:
            return ''
        parsed = urlparse(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            raise ValueError('Informe uma URL base valida, iniciando com http:// ou https://.')
        return url

    @staticmethod
    def normalize_endpoint(value: str) -> str:
        endpoint = str(value or '').strip()
        if not endpoint:
            return ''
        return '/' + endpoint.lstrip('/')

    @staticmethod
    def normalize_timeout(value: object) -> float:
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT_SECONDS
        return min(120.0, max(1.0, timeout))
