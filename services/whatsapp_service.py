from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
import socket
import time
from typing import Any, Callable
from urllib import error, request

from services.providers.exceptions import sanitize_technical_detail
from services.whatsapp_config_service import WhatsAppConfig, WhatsAppConfigService


logger = logging.getLogger('EMC Central de Novidades')
DEFAULT_TIMEOUT = 20.0


@dataclass(slots=True)
class WhatsAppResult:
    success: bool
    message: str
    message_id: str = ''
    status_http: int | None = None
    provider_result: str = ''


class WhatsAppApiError(RuntimeError):
    def __init__(
        self,
        user_message: str,
        *,
        status_http: int | None = None,
        provider_result: str = '',
        technical_detail: str = '',
    ) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.status_http = status_http
        self.provider_result = provider_result
        self.technical_detail = technical_detail


class WhatsAppService:
    def __init__(self, opener: Callable[..., Any] | None = None) -> None:
        self.opener = opener or request.urlopen

    def test_connection(self, config: WhatsAppConfig, token: str) -> WhatsAppResult:
        normalized = WhatsAppConfigService.normalize(config)
        self._validate_config_and_token(normalized, token)
        started = time.perf_counter()
        response = self._request('GET', self._url(normalized), normalized, token)
        self._log_safe(
            'teste_conexao', normalized, response.get('status_http'), started, 'sucesso', ''
        )
        return WhatsAppResult(
            success=True,
            message='Conexao com a API de WhatsApp validada com sucesso.',
            status_http=response.get('status_http'),
        )

    def send_message(
        self,
        config: WhatsAppConfig,
        token: str,
        *,
        recipient: str,
        message: str,
        novelty_title: str = '',
        novelty_link: str = '',
    ) -> WhatsAppResult:
        normalized = WhatsAppConfigService.normalize(config)
        self._validate_config_and_token(normalized, token)
        normalized_recipient = normalize_whatsapp_phone(recipient)
        clean_message = str(message or '').strip()
        if not clean_message:
            raise WhatsAppApiError('A mensagem nao pode ficar vazia.')
        payload = self.build_message_payload(
            normalized,
            recipient=normalized_recipient,
            message=clean_message,
            novelty_title=novelty_title,
            novelty_link=novelty_link,
        )
        started = time.perf_counter()
        response = self._request('POST', self._url(normalized), normalized, token, payload)
        message_id = self._message_id(response)
        self._log_safe(
            'envio', normalized, response.get('status_http'), started, 'sucesso', message_id
        )
        return WhatsAppResult(
            success=True,
            message='Novidade enviada pelo WhatsApp.',
            message_id=message_id,
            status_http=response.get('status_http'),
            provider_result=str(response.get('status') or response.get('result') or ''),
        )

    @staticmethod
    def build_message_payload(
        config: WhatsAppConfig,
        *,
        recipient: str,
        message: str,
        novelty_title: str = '',
        novelty_link: str = '',
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'to': recipient,
            'message': message,
            'novelty': {
                'title': novelty_title,
                'link': novelty_link,
            },
        }
        if config.sender_id:
            payload['sender_id'] = config.sender_id
        if config.instance_name:
            payload['instance_name'] = config.instance_name
        return payload

    def _request(
        self,
        method: str,
        url: str,
        config: WhatsAppConfig,
        token: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = self._headers(config, token)
        headers['Content-Type'] = 'application/json'
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener(req, timeout=config.timeout_seconds) as response:
                raw = response.read().decode('utf-8')
                body = json.loads(raw) if raw else {}
                if not isinstance(body, dict):
                    body = {'raw': body}
                body['status_http'] = getattr(response, 'status', None)
                return body
        except error.HTTPError as exc:
            raise self._http_error(exc, config, token) from exc
        except (socket.timeout, TimeoutError) as exc:
            self._log_safe('timeout', config, None, None, 'erro', '')
            raise WhatsAppApiError(
                'Tempo esgotado ao conectar com a API de WhatsApp.',
                technical_detail='timeout',
            ) from exc
        except error.URLError as exc:
            reason = sanitize_technical_detail(getattr(exc, 'reason', exc), token)
            message = (
                'Tempo esgotado ao conectar com a API de WhatsApp.'
                if 'timed out' in reason.lower()
                else 'Nao foi possivel conectar com a API de WhatsApp.'
            )
            self._log_safe('conexao', config, None, None, 'erro', '')
            raise WhatsAppApiError(message, technical_detail=reason) from exc
        except Exception as exc:
            detail = sanitize_technical_detail(exc, token)
            self._log_safe('resposta_inesperada', config, None, None, 'erro', '')
            raise WhatsAppApiError(
                'Resposta inesperada do provedor de WhatsApp.',
                technical_detail=detail,
            ) from exc

    def _http_error(self, exc: error.HTTPError, config: WhatsAppConfig, token: str) -> WhatsAppApiError:
        raw = exc.read().decode('utf-8', errors='replace')
        detail = sanitize_technical_detail(raw or exc, token)
        payload: dict[str, Any] = {}
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                payload = loaded
        except json.JSONDecodeError:
            pass
        provider_error = payload.get('error') if isinstance(payload.get('error'), dict) else payload
        provider_result = str(
            provider_error.get('code') or provider_error.get('type') or provider_error.get('status') or ''
        )
        self._log_safe('erro_http', config, exc.code, None, 'erro', provider_result)
        return WhatsAppApiError(
            self._friendly_error(exc.code, provider_error),
            status_http=exc.code,
            provider_result=provider_result,
            technical_detail=detail,
        )

    @staticmethod
    def _friendly_error(status: int, provider_error: dict[str, Any]) -> str:
        text = str(provider_error.get('message') or provider_error.get('error') or '').lower()
        if status in {401, 403} or any(term in text for term in ('token', 'auth', 'unauthorized', 'forbidden')):
            return 'Token ausente, invalido, expirado ou sem permissao para envio.'
        if status == 400 and any(term in text for term in ('phone', 'numero', 'number', 'recipient')):
            return 'Numero de destinatario rejeitado pelo provedor.'
        if status == 408:
            return 'Tempo esgotado no provedor de WhatsApp.'
        if status == 429:
            return 'Limite temporario de envio atingido no provedor de WhatsApp.'
        if status >= 500:
            return 'O provedor de WhatsApp esta temporariamente indisponivel.'
        return 'A API de WhatsApp recusou a requisicao. Verifique a configuracao.'

    @staticmethod
    def _headers(config: WhatsAppConfig, token: str) -> dict[str, str]:
        if config.auth_scheme == 'api_key_header':
            return {config.auth_header_name: token}
        return {'Authorization': f'Bearer {token}'}

    @staticmethod
    def _message_id(response: dict[str, Any]) -> str:
        for key in ('message_id', 'id', 'messageId'):
            if response.get(key):
                return str(response[key])
        messages = response.get('messages')
        if isinstance(messages, list) and messages:
            first = messages[0]
            if isinstance(first, dict):
                return str(first.get('id') or first.get('message_id') or '')
        data = response.get('data')
        if isinstance(data, dict):
            return str(data.get('id') or data.get('message_id') or '')
        return ''

    @staticmethod
    def _url(config: WhatsAppConfig) -> str:
        return f'{config.base_url.rstrip("/")}/{config.send_endpoint.lstrip("/")}'

    @staticmethod
    def _validate_config_and_token(config: WhatsAppConfig, token: str) -> None:
        if not token.strip():
            raise WhatsAppApiError('Token ou chave de autenticacao nao configurado.')
        missing = WhatsAppConfigService.missing_fields(
            WhatsAppConfig(**{**config.public_dict(), 'encrypted_token': 'ok'})
        )
        if missing:
            raise WhatsAppApiError('Configuracao incompleta: ' + ', '.join(missing) + '.')

    @staticmethod
    def _log_safe(
        step: str,
        config: WhatsAppConfig,
        status_http: int | None,
        started: float | None,
        result: str,
        provider_id: str,
    ) -> None:
        duration_ms = int((time.perf_counter() - started) * 1000) if started else None
        logger.info(
            '[WHATSAPP] etapa=%s endpoint=%s duracao_ms=%s status_http=%s resultado=%s id=%s',
            step,
            config.send_endpoint,
            duration_ms if duration_ms is not None else 'nao informado',
            status_http if status_http is not None else 'nao informado',
            result,
            provider_id or 'nao informado',
        )


def normalize_whatsapp_phone(value: str, *, default_country_code: str = '55') -> str:
    raw = str(value or '').strip()
    digits = re.sub(r'\D+', '', raw)
    if not digits:
        raise WhatsAppApiError('Informe um numero de WhatsApp com DDD.')
    if raw.startswith('+'):
        normalized = digits
    elif digits.startswith(default_country_code) and len(digits) in {12, 13}:
        normalized = digits
    else:
        normalized = f'{default_country_code}{digits}'
    if not re.fullmatch(r'\d{12,15}', normalized):
        raise WhatsAppApiError('Informe um destinatario valido com codigo do pais e DDD.')
    if normalized.startswith('55') and not re.fullmatch(r'55\d{10,11}', normalized):
        raise WhatsAppApiError('Informe um numero brasileiro valido com DDD.')
    return normalized


def mask_recipient(value: str) -> str:
    digits = re.sub(r'\D+', '', str(value or ''))
    if len(digits) <= 6:
        return '*' * len(digits)
    return f'{digits[:4]}{"*" * (len(digits) - 6)}{digits[-2:]}'
