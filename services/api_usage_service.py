from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
from threading import Lock
import time
from typing import Any


logger = logging.getLogger('EMC Novidade Visual IA')


TEXT_PRICING_PER_MILLION: dict[str, tuple[float, float, float]] = {
    'gpt-5-mini': (0.25, 0.025, 2.00),
    'gpt-4o-mini': (0.15, 0.075, 0.60),
}

IMAGE_TEXT_PRICING_PER_MILLION: dict[str, tuple[float, float, float]] = {
    'gpt-image-1.5': (5.00, 1.25, 10.00),
}

IMAGE_PRICING_PER_MILLION: dict[str, tuple[float, float, float]] = {
    'gpt-image-1.5': (8.00, 2.00, 32.00),
}

IMAGE_GENERATION_FALLBACK_COST: dict[str, float] = {
    # Estimativa equivalente à qualidade média em 1536x1024 quando a API
    # não devolve uso detalhado. O log identifica explicitamente o método.
    'gpt-image-1.5': 0.05,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _value(source: Any, name: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _integer(source: Any, name: str) -> int:
    value = _value(source, name, 0)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def response_usage(response: Any) -> dict[str, int]:
    usage = _value(response, 'usage')
    input_details = _value(usage, 'input_tokens_details')
    output_details = _value(usage, 'output_tokens_details')
    input_tokens = _integer(usage, 'input_tokens')
    output_tokens = _integer(usage, 'output_tokens')
    cached_tokens = _integer(input_details, 'cached_tokens')
    reasoning_tokens = _integer(output_details, 'reasoning_tokens')
    input_image_tokens = _integer(input_details, 'image_tokens')
    output_image_tokens = _integer(output_details, 'image_tokens')
    unclassified_image_tokens = _integer(usage, 'image_tokens')
    image_tokens = input_image_tokens + output_image_tokens + unclassified_image_tokens
    return {
        'input_tokens': input_tokens,
        'cached_tokens': cached_tokens,
        'output_tokens': output_tokens,
        'reasoning_tokens': reasoning_tokens,
        'image_tokens': image_tokens,
        'input_image_tokens': input_image_tokens,
        'output_image_tokens': output_image_tokens + unclassified_image_tokens,
    }


def response_request_id(response: Any) -> str:
    direct = _value(response, '_request_id') or _value(response, 'request_id')
    if direct:
        return str(direct)
    headers = _value(_value(response, 'response'), 'headers') or _value(response, 'headers')
    if headers:
        try:
            return str(headers.get('x-request-id') or headers.get('request-id') or '')
        except AttributeError:
            return ''
    return ''


def estimate_cost(
    model: str,
    *,
    endpoint: str,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    image_tokens: int,
    input_image_tokens: int = 0,
    output_image_tokens: int = 0,
) -> tuple[float, str]:
    normalized = str(model or '').strip().lower()
    if normalized.startswith('gpt-image'):
        image_rates = IMAGE_PRICING_PER_MILLION.get(normalized)
        text_rates = IMAGE_TEXT_PRICING_PER_MILLION.get(normalized)
        if image_rates and text_rates and any((input_tokens, cached_tokens, output_tokens, image_tokens)):
            text_input_rate, text_cached_rate, text_output_rate = text_rates
            image_input_rate, _, image_output_rate = image_rates
            text_input = max(0, input_tokens - cached_tokens - input_image_tokens)
            text_output = max(0, output_tokens - output_image_tokens)
            cost = (
                text_input * text_input_rate
                + cached_tokens * text_cached_rate
                + text_output * text_output_rate
                + input_image_tokens * image_input_rate
                + output_image_tokens * image_output_rate
            ) / 1_000_000
            return cost, 'tokens_reportados'
        if endpoint.startswith('/v1/images'):
            return IMAGE_GENERATION_FALLBACK_COST.get(normalized, 0.0), 'fallback_imagem_media'
        return 0.0, 'uso_indisponivel'
    rates = TEXT_PRICING_PER_MILLION.get(normalized)
    if not rates:
        return 0.0, 'preco_nao_configurado'
    input_rate, cached_rate, output_rate = rates
    uncached = max(0, input_tokens - cached_tokens)
    cost = (
        uncached * input_rate
        + cached_tokens * cached_rate
        + output_tokens * output_rate
    ) / 1_000_000
    return cost, 'tokens_reportados'


@dataclass(slots=True)
class ApiCallRecord:
    generation_id: str
    idempotency_key: str
    stage: str
    endpoint: str
    model: str
    logical_call: int
    started_at: str
    attempts: int = 0
    request_id: str = ''
    duration_seconds: float = 0.0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    image_tokens: int = 0
    input_image_tokens: int = 0
    output_image_tokens: int = 0
    estimated_cost_usd: float = 0.0
    cost_method: str = 'uso_indisponivel'
    status: str = 'in_progress'
    error_type: str = ''


class GenerationApiAudit:
    def __init__(self, generation_id: str, idempotency_key: str) -> None:
        self.generation_id = generation_id
        self.idempotency_key = idempotency_key
        self.started = time.perf_counter()
        self._records: dict[int, ApiCallRecord] = {}
        self._call_started: dict[int, float] = {}
        self._stage_durations: dict[str, float] = {}
        self._lock = Lock()

    def start_call(self, logical_call: int, stage: str, endpoint: str, model: str) -> None:
        with self._lock:
            self._records[logical_call] = ApiCallRecord(
                generation_id=self.generation_id,
                idempotency_key=self.idempotency_key,
                stage=stage,
                endpoint=endpoint,
                model=model,
                logical_call=logical_call,
                started_at=_utc_now(),
            )
            self._call_started[logical_call] = time.perf_counter()

    def record_attempt(self, logical_call: int, endpoint: str, attempt: int) -> None:
        with self._lock:
            record = self._records.get(logical_call)
            if not record:
                return
            record.attempts = max(record.attempts, attempt)
            payload = {
                'event': 'http_attempt',
                'timestamp': _utc_now(),
                'generation': self.generation_id,
                'stage': record.stage,
                'endpoint': endpoint,
                'model': record.model,
                'logical_call': logical_call,
                'attempt': attempt,
                'retry': attempt > 1,
            }
        logger.info('[API_AUDIT] %s', json.dumps(payload, ensure_ascii=False, separators=(',', ':')))

    def record_http_response(
        self,
        logical_call: int,
        *,
        request_id: str,
        status_http: int | None,
    ) -> None:
        with self._lock:
            record = self._records.get(logical_call)
            if not record:
                return
            if request_id:
                record.request_id = request_id
            payload = {
                'event': 'http_response',
                'timestamp': _utc_now(),
                'generation': self.generation_id,
                'stage': record.stage,
                'endpoint': record.endpoint,
                'model': record.model,
                'logical_call': logical_call,
                'attempt': record.attempts,
                'request_id': record.request_id,
                'status_http': status_http,
            }
        logger.info('[API_AUDIT] %s', json.dumps(payload, ensure_ascii=False, separators=(',', ':')))

    def record_response_usage(self, logical_call: int, response: Any) -> None:
        usage = response_usage(response)
        request_id = response_request_id(response)
        with self._lock:
            record = self._records.get(logical_call)
            if not record:
                return
            record.input_tokens = usage['input_tokens']
            record.cached_tokens = usage['cached_tokens']
            record.output_tokens = usage['output_tokens']
            record.reasoning_tokens = usage['reasoning_tokens']
            record.image_tokens = usage['image_tokens']
            record.input_image_tokens = usage['input_image_tokens']
            record.output_image_tokens = usage['output_image_tokens']
            if request_id:
                record.request_id = request_id

    def finish_call(self, logical_call: int, error: BaseException | None = None) -> ApiCallRecord | None:
        with self._lock:
            record = self._records.get(logical_call)
            if not record:
                return None
            record.duration_seconds = time.perf_counter() - self._call_started.pop(
                logical_call, time.perf_counter()
            )
            record.status = 'error' if error else 'success'
            record.error_type = error.__class__.__name__ if error else ''
            record.estimated_cost_usd, record.cost_method = estimate_cost(
                record.model,
                endpoint=record.endpoint,
                input_tokens=record.input_tokens,
                cached_tokens=record.cached_tokens,
                output_tokens=record.output_tokens,
                image_tokens=record.image_tokens,
                input_image_tokens=record.input_image_tokens,
                output_image_tokens=record.output_image_tokens,
            )
            payload = {'event': 'api_call', 'timestamp': _utc_now(), **asdict(record)}
        logger.info('[API_AUDIT] %s', json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        return record

    def record_stage(self, stage: str, duration_seconds: float) -> None:
        with self._lock:
            self._stage_durations[stage] = self._stage_durations.get(stage, 0.0) + duration_seconds
            payload = {
                'event': 'stage_usage',
                'timestamp': _utc_now(),
                'generation': self.generation_id,
                'stage': stage,
                'endpoint': 'local',
                'duration_seconds': round(duration_seconds, 6),
                'input_tokens': 0,
                'cached_tokens': 0,
                'output_tokens': 0,
                'reasoning_tokens': 0,
                'image_tokens': 0,
                'estimated_cost_usd': 0.0,
            }
        logger.info('[API_AUDIT] %s', json.dumps(payload, ensure_ascii=False, separators=(',', ':')))

    def summary(self) -> dict[str, Any]:
        with self._lock:
            records = list(self._records.values())
            endpoint_responses = sum(record.endpoint == '/v1/responses' for record in records)
            endpoint_images = sum(record.endpoint.startswith('/v1/images') for record in records)
            return {
                'generation': self.generation_id,
                'idempotency_key': self.idempotency_key,
                'duration_seconds': time.perf_counter() - self.started,
                'logical_calls': len(records),
                'http_attempts': sum(record.attempts for record in records),
                'responses_calls': endpoint_responses,
                'images_calls': endpoint_images,
                'input_tokens': sum(record.input_tokens for record in records),
                'cached_tokens': sum(record.cached_tokens for record in records),
                'output_tokens': sum(record.output_tokens for record in records),
                'reasoning_tokens': sum(record.reasoning_tokens for record in records),
                'image_tokens': sum(record.image_tokens for record in records),
                'input_image_tokens': sum(record.input_image_tokens for record in records),
                'output_image_tokens': sum(record.output_image_tokens for record in records),
                'estimated_cost_usd': sum(record.estimated_cost_usd for record in records),
                'stages': dict(self._stage_durations),
            }

    def log_summary(self) -> dict[str, Any]:
        summary = self.summary()
        logger.info('=========================')
        logger.info('Resumo da geração')
        logger.info('=========================')
        logger.info('Geração: %s', summary['generation'])
        logger.info('Tempo total: %.2f s', summary['duration_seconds'])
        logger.info('Quantidade de chamadas: %s', summary['logical_calls'])
        logger.info('/v1/responses: %s', summary['responses_calls'])
        logger.info('/v1/images: %s', summary['images_calls'])
        logger.info('Tentativas HTTP: %s', summary['http_attempts'])
        logger.info('Tokens entrada: %s', summary['input_tokens'])
        logger.info('Tokens saída: %s', summary['output_tokens'])
        logger.info('Tokens cache: %s', summary['cached_tokens'])
        logger.info('Tokens raciocínio: %s', summary['reasoning_tokens'])
        logger.info('Tokens imagem: %s', summary['image_tokens'])
        logger.info('Custo aproximado: US$ %.6f', summary['estimated_cost_usd'])
        logger.info('=========================')
        logger.info(
            '[API_AUDIT] %s',
            json.dumps(
                {'event': 'generation_summary', 'timestamp': _utc_now(), **summary},
                ensure_ascii=False,
                separators=(',', ':'),
            ),
        )
        return summary
