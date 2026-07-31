from __future__ import annotations

import re


class ProviderError(RuntimeError):
    def __init__(
        self,
        user_message: str,
        *,
        code: str = 'provider_error',
        technical_detail: str = '',
        provider: str = '',
        model: str = '',
        stage: str = '',
        status_http: int | None = None,
        exception_type: str = '',
        image_id: str = '',
    ) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.code = code
        self.technical_detail = technical_detail
        self.provider = provider
        self.model = model
        self.stage = stage
        self.status_http = status_http
        self.exception_type = exception_type
        self.image_id = image_id

    def sanitized_details(self) -> dict[str, str]:
        return {
            'Provedor': self.provider or 'não informado',
            'Modelo': self.model or 'não informado',
            'Etapa': self.stage or 'não identificada',
            'Status HTTP': str(self.status_http) if self.status_http is not None else 'não aplicável',
            'Tipo da exceção': self.exception_type or self.__class__.__name__,
            'Mensagem técnica': self.technical_detail or self.user_message,
            'Imagem': self.image_id or 'não aplicável',
        }


def sanitize_technical_detail(value: object, api_key: str = '') -> str:
    text = str(value)
    for secret in {api_key, api_key.strip()}:
        if secret:
            text = text.replace(secret, '[REDACTED]')
    text = re.sub(
        r'(?i)(bearer\s+)[^\s,;}\]"\']+',
        r'\1[REDACTED]',
        text,
    )
    text = re.sub(r'\bsk-[A-Za-z0-9_-]{8,}\b', '[REDACTED]', text)
    text = re.sub(
        r'''(?ix)
        ((?:authorization|x-goog-api-key|api[_-]?key|access[_-]?token|token)\s*['"]?\s*[:=]\s*)
        (?:
            bearer\s+(?:\[[A-Z_]+\]|[^\s,;}\]]+)
            |\[[A-Z_]+\]
            |['"][^'"]*['"]
            |[^\s,;}\]]+
        )
        ''',
        r'\1[REDACTED]',
        text,
    )
    text = re.sub(
        r'(?i)(data:image/[^;,]+;base64,)[A-Za-z0-9+/=_-]+',
        r'\1[REDACTED_IMAGE]',
        text,
    )
    text = re.sub(
        r'''(?ix)
        (['"]?data['"]?\s*:\s*['"])
        [A-Za-z0-9+/=_-]{256,}
        (['"])
        ''',
        r'\1[REDACTED_IMAGE]\2',
        text,
    )
    text = re.sub(
        r'(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
        '[REDACTED_EMAIL]',
        text,
    )
    text = re.sub(r'\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b', '[REDACTED_CPF]', text)
    text = re.sub(
        r'\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b',
        '[REDACTED_CNPJ]',
        text,
    )
    text = re.sub(
        r'(?<!\d)(?:\+?55\s*)?\(?\d{2}\)?\s*\d{4,5}[-.\s]?\d{4}(?!\d)',
        '[REDACTED_PHONE]',
        text,
    )
    return text


def _error_payload(exc: Exception) -> dict:
    for attribute in ('body', 'details'):
        value = getattr(exc, attribute, None)
        if isinstance(value, dict):
            payload = value.get('error', value)
            if isinstance(payload, dict):
                return payload
    return {}


def get_http_status(exc: Exception) -> int | None:
    value = getattr(exc, 'status_code', None)
    if value is None:
        response = getattr(exc, 'response', None)
        value = getattr(response, 'status_code', None)
    if value is None:
        candidate = getattr(exc, 'code', None)
        value = candidate if isinstance(candidate, int) else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def sanitized_exception_chain(exc: BaseException, api_key: str = '') -> str:
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(
            f'{current.__class__.__name__}: '
            f'{sanitize_technical_detail(current, api_key)}'
        )
        current = current.__cause__ or current.__context__
    return ' <- '.join(parts)


def _zero_quota_model(provider: str, status_code: object, payload: dict) -> str:
    if provider != 'gemini' or status_code != 429:
        return ''
    status = str(payload.get('status') or '').upper()
    message = str(payload.get('message') or '')
    if status != 'RESOURCE_EXHAUSTED' or not re.search(r'\blimit:\s*0\b', message, re.I):
        return ''
    match = re.search(r'\blimit:\s*0,\s*model:\s*([A-Za-z0-9._-]+)', message, re.I)
    return match.group(1) if match else ''


def map_provider_exception(provider: str, exc: Exception, api_key: str) -> ProviderError:
    detail = sanitize_technical_detail(exc, api_key)
    lowered = detail.lower()
    status_code = get_http_status(exc)
    payload = _error_payload(exc)
    error_code = str(payload.get('code') or payload.get('type') or '').lower()
    zero_quota_model = _zero_quota_model(provider, status_code, payload)

    if (
        'billing_hard_limit_reached' in lowered
        or error_code == 'billing_hard_limit_reached'
    ):
        message, code = (
            'A conta do provedor atingiu o limite financeiro configurado. '
            'Verifique o saldo, os cr\u00e9ditos e os limites de cobran\u00e7a da API.',
            'billing_hard_limit_reached',
        )
    elif zero_quota_model:
        message, code = (
            f'A cota do projeto para o modelo {zero_quota_model} está definida como 0. '
            'Criar outra chave no mesmo projeto não resolve. Pode ser necessário habilitar '
            'o faturamento ou utilizar outro modelo com cota disponível.',
            'quota_exceeded',
        )
    elif exc.__class__.__name__ == 'ValidationError':
        message, code = (
            'A resposta da IA não correspondeu ao formato esperado.',
            'invalid_schema_response',
        )
    elif status_code == 401 or exc.__class__.__name__ == 'AuthenticationError':
        message, code = 'A chave de API informada não foi aceita pelo provedor.', 'invalid_api_key'
    elif status_code == 403 or exc.__class__.__name__ == 'PermissionDeniedError':
        message, code = (
            'Esta chave não possui acesso ao modelo selecionado.',
            'model_permission_denied',
        )
    elif (
        status_code == 404
        or error_code in {'model_not_found', 'invalid_model'}
        or 'model does not exist' in lowered
    ):
        message, code = 'O modelo informado não existe.', 'model_not_found'
    elif status_code == 400 and any(
        term in lowered
        for term in ('does not support', 'not supported by this model', 'unsupported model')
    ):
        message, code = (
            'O modelo selecionado não é compatível com esta operação.',
            'model_incompatible',
        )
    elif (
        status_code == 429
        or exc.__class__.__name__ == 'RateLimitError'
        or any(term in lowered for term in ('quota', 'rate limit', 'resource_exhausted'))
    ):
        message, code = (
            'A cota ou o limite de requisições do provedor foi excedido.',
            'quota_exceeded',
        )
    elif exc.__class__.__name__ == 'APITimeoutError' or 'timeout' in lowered:
        message, code = 'O provedor demorou demais para responder. Tente novamente.', 'timeout'
    elif (
        exc.__class__.__name__ == 'APIConnectionError'
        or any(term in lowered for term in ('connection', 'network'))
    ):
        message, code = 'Falha de conexão com o provedor de IA. Tente novamente.', 'connection_error'
    elif any(term in lowered for term in ('unsupported image', 'image format', 'formato')):
        message, code = 'O formato da imagem não é suportado pelo provedor.', 'unsupported_image'
    elif any(term in lowered for term in ('service unavailable', 'server error', '502', '503')):
        message, code = (
            'O provedor de IA está temporariamente indisponível. Tente novamente.',
            'provider_unavailable',
        )
    elif status_code == 400:
        message, code = (
            'A solicitação foi rejeitada pelo provedor. '
            'Revise a configuração da API e tente novamente.',
            'invalid_request',
        )
    else:
        message, code = (
            'Não foi possível concluir a operação com o provedor selecionado.',
            'unexpected_provider_error',
        )
    return ProviderError(
        message,
        code=f'{provider}_{code}',
        technical_detail=detail,
        provider=provider,
        status_http=status_code,
        exception_type=exc.__class__.__name__,
    )
