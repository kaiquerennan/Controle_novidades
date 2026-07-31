from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import Path
import time

from openai import DefaultHttpxClient, OpenAI
from PIL import Image

from models.novelty_plan import FidelityReview, NoveltyPlan, OpenAINoveltyPlan
from services.image_service import safe_path, validate_image_bytes
from services.model_routing import (
    configured_text_model,
    validate_image_model,
    validate_text_model,
)
from services.providers.base_provider import AIProvider
from services.providers.exceptions import ProviderError, map_provider_exception


logger = logging.getLogger('EMC Novidade Visual IA')


class OpenAIProvider(AIProvider):
    name = 'openai'

    def _client(self, api_key: str, timeout: float, max_retries: int = 1) -> OpenAI:  # default max_retries=1
        attempts = {'count': 0}
        audit = getattr(self, '_api_audit', None)
        logical_call = int(getattr(self, '_api_logical_call', 0) or 0)

        def request_started(request) -> None:
            attempts['count'] += 1
            request.extensions['emc_attempt'] = attempts['count']
            request.extensions['emc_started'] = time.perf_counter()
            logger.info(
                '[OPENAI] RequisiÃ§Ã£o iniciada; endpoint=%s tentativa=%s timeout=%.0f s',
                request.url.path, attempts['count'], timeout,
            )
            if audit:
                audit.record_attempt(logical_call, request.url.path, attempts['count'])

        def response_received(response) -> None:
            started = response.request.extensions.get('emc_started', time.perf_counter())
            logger.info(
                '[OPENAI] RequisiÃ§Ã£o concluÃ­da; endpoint=%s tentativa=%s duracao=%.2f s '
                'status_http=%s',
                response.request.url.path,
                response.request.extensions.get('emc_attempt', attempts['count']),
                time.perf_counter() - started,
                response.status_code,
            )
            if audit:
                audit.record_http_response(
                    logical_call,
                    request_id=str(response.headers.get('x-request-id') or ''),
                    status_http=response.status_code,
                )

        http_client = DefaultHttpxClient(event_hooks={
            'request': [request_started],
            'response': [response_received],
        })
        return OpenAI(
            api_key=api_key.strip(), timeout=timeout, max_retries=max_retries,
            http_client=http_client,
        )

    def _record_usage(self, response) -> None:
        audit = getattr(self, '_api_audit', None)
        logical_call = int(getattr(self, '_api_logical_call', 0) or 0)
        if audit and logical_call:
            audit.record_response_usage(logical_call, response)

    def treat_image(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        source: Path,
        destination: Path,
    ) -> Path:
        raise ProviderError(
            'Anonimização automática desabilitada. Marque áreas e use o botão explícito de anonimização.',
            code='automatic_anonymization_disabled',
            provider=self.name,
            model=model,
            stage='anonimizacao',
        )

    def anonymize_marked_areas(
        self,
        *,
        api_key: str,
        model: str,
        source: Path,
        mask: Path,
        instructions: str,
        destination: Path,
    ) -> Path:
        if not api_key.strip():
            raise ProviderError('Informe uma chave da API da OpenAI.', code='missing_api_key')
        model = validate_image_model(self.name, model)
        source = safe_path(source)
        mask = safe_path(mask)
        destination = safe_path(destination)
        _, source_size = validate_image_bytes(source.read_bytes())
        try:
            client = self._client(api_key, 120.0, max_retries=0)
            with source.open('rb') as image_file, mask.open('rb') as mask_file:
                response = client.images.edit(
                    model=model,
                    image=image_file,
                    mask=mask_file,
                    prompt=instructions,
                    input_fidelity='high',
                    output_format='png',
                    size='auto',
                )
            self._record_usage(response)
            encoded = response.data[0].b64_json if response.data else None
            if not encoded:
                raise ProviderError(
                    'A OpenAI não retornou uma imagem.',
                    code='openai_empty_image',
                )
            with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as image:
                image.load()
                result = image.convert('RGB')
                if result.size != source_size:
                    result = result.resize(source_size, Image.Resampling.LANCZOS)
                destination.parent.mkdir(parents=True, exist_ok=True)
                result.save(destination, 'PNG')
            return destination
        except ProviderError:
            raise
        except Exception as exc:
            raise map_provider_exception(self.name, exc, api_key) from exc
    def suggest_orientation(
        self,
        *,
        api_key: str,
        model: str,
        source: Path,
    ) -> str:
        if not api_key.strip():
            raise ProviderError('Informe uma chave da API da OpenAI.', code='missing_api_key')
        model = configured_text_model(self.name, model)
        image_bytes = safe_path(source).read_bytes()
        image_format, _ = validate_image_bytes(image_bytes)
        mime_type = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}[
            image_format
        ]
        data_url = f'data:{mime_type};base64,{base64.b64encode(image_bytes).decode("ascii")}'
        instruction = (
            'Observe somente as informaÃ§Ãµes visÃ­veis nesta tela. Escreva uma orientaÃ§Ã£o curta, '
            'em portuguÃªs do Brasil, para acompanhar a imagem. NÃ£o invente etapas, teclas, '
            'atalhos ou funcionalidades que nÃ£o estejam visÃ­veis. Retorne somente a sugestÃ£o.'
        )
        try:
            client = self._client(api_key, 120.0)
            response = client.responses.create(
                model=model,
                input=[
                    {
                        'role': 'user',
                        'content': [
                            {'type': 'input_text', 'text': instruction},
                            {'type': 'input_image', 'image_url': data_url},
                        ],
                    }
                ],
                store=False,
                max_output_tokens=500,
            )
            self._record_usage(response)
            suggestion = (response.output_text or '').strip()
            if suggestion:
                return suggestion
            raise ProviderError(
                'A OpenAI nÃ£o retornou uma sugestÃ£o.',
                code='openai_empty_suggestion',
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise map_provider_exception(self.name, exc, api_key) from exc

    def generate_novelty_plan(
        self,
        *,
        api_key: str,
        model: str,
        raw_text: str,
        images: list[tuple[str, Path]],
        prompt: str,
        max_output_tokens: int | None = None,
    ) -> NoveltyPlan:
        if not api_key.strip():
            raise ProviderError('Informe uma chave da API da OpenAI.', code='missing_api_key')
        model = validate_text_model(self.name, model)
        content: list[dict[str, str]] = [
            {'type': 'input_text', 'text': f'TEXTO BRUTO DA NOVIDADE:\n{raw_text}'}
        ]
        for image_id, path in images:
            image_bytes = safe_path(path).read_bytes()
            image_format, _ = validate_image_bytes(image_bytes)
            mime_type = {
                'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'
            }[image_format]
            encoded = base64.b64encode(image_bytes).decode('ascii')
            content.extend((
                {'type': 'input_text', 'text': f'IMAGEM: {image_id}'},
                {'type': 'input_image', 'image_url': f'data:{mime_type};base64,{encoded}'},
            ))
        try:
            client = self._client(api_key, 180.0)
            response = client.responses.parse(
                model=model,
                instructions=prompt,
                input=[{'role': 'user', 'content': content}],
                text_format=OpenAINoveltyPlan,
                store=False,
                max_output_tokens=max_output_tokens or 12000,
            )
            self._record_usage(response)
            parsed = response.output_parsed
            if parsed:
                return parsed
            raise ProviderError(
                'A OpenAI nÃ£o retornou a estrutura da novidade.',
                code='openai_empty_novelty_plan',
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise map_provider_exception(self.name, exc, api_key) from exc

    def review_novelty(
        self, *, api_key, model, raw_text, document, image_ids,
        image_descriptions=None, prompt='', max_output_tokens: int | None = None
    ) -> FidelityReview:
        if not api_key.strip():
            raise ProviderError('Informe uma chave da API da OpenAI.', code='missing_api_key')
        model = validate_text_model(self.name, model)
        payload = json.dumps({
            'texto_bruto': raw_text,
            'documento': document,
            'imagens_enviadas': image_ids,
            'descricoes_imagens': image_descriptions or [],
        }, ensure_ascii=False, separators=(',', ':'))
        try:
            client = self._client(api_key, 180.0)
            response = client.responses.parse(
                model=model,
                instructions=prompt,
                input=[{'role': 'user', 'content': [
                    {'type': 'input_text', 'text': payload}
                ]}],
                text_format=FidelityReview,
                store=False,
                max_output_tokens=max_output_tokens or 6500,
            )
            self._record_usage(response)
            if response.output_parsed:
                return response.output_parsed
            raise ProviderError(
                'A OpenAI n\u00e3o retornou a revis\u00e3o da mat\u00e9ria.',
                code='openai_empty_review',
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise map_provider_exception(self.name, exc, api_key) from exc

    def generate_cover(self, *, api_key, model, prompt, destination) -> Path:
        if not api_key.strip():
            raise ProviderError('Informe uma chave da API da OpenAI.', code='missing_api_key')
        model = validate_image_model(self.name, model)
        destination = safe_path(destination)
        try:
            client = self._client(api_key, 180.0)
            response = client.images.generate(
                model=model, prompt=prompt, size='1536x1024',
                output_format='png',
            )
            self._record_usage(response)
            encoded = response.data[0].b64_json if response.data else None
            if not encoded:
                raise ProviderError(
                    'A OpenAI n\u00e3o retornou a imagem de capa.',
                    code='openai_empty_cover',
                )
            with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as image:
                image.load()
                destination.parent.mkdir(parents=True, exist_ok=True)
                image.convert('RGB').save(destination, 'PNG')
            return destination
        except ProviderError:
            raise
        except Exception as exc:
            raise map_provider_exception(self.name, exc, api_key) from exc
