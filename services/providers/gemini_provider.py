from __future__ import annotations

import io
import json
import re
from pathlib import Path

try:
    from google import genai
    from google.genai import types
except ImportError:
    from types import SimpleNamespace

    class _MissingPart:
        @staticmethod
        def from_bytes(*, data, mime_type):
            return SimpleNamespace(inline_data=SimpleNamespace(data=data, mime_type=mime_type))

    class _MissingGenerateContentConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    genai = SimpleNamespace(Client=None)
    types = SimpleNamespace(Part=_MissingPart, GenerateContentConfig=_MissingGenerateContentConfig)
from PIL import Image

from models.novelty_plan import FidelityReview, NoveltyPlan
from services.image_service import safe_path, validate_image_bytes
from services.model_routing import (
    configured_text_model,
    validate_image_model,
    validate_text_model,
)
from services.providers.base_provider import AIProvider
from services.providers.exceptions import ProviderError, map_provider_exception


def _validate_api_key(api_key: str) -> str:
    cleaned = api_key.strip()
    if not cleaned:
        raise ProviderError('Informe uma chave da API do Gemini.', code='missing_api_key')
    try:
        cleaned.encode('ascii')
    except UnicodeEncodeError as exc:
        raise ProviderError(
            'A chave Gemini informada nÃ£o Ã© vÃ¡lida. Cole somente o valor da chave, '
            'sem rÃ³tulos, espaÃ§os ou caracteres nÃ£o ASCII.',
            code='invalid_api_key_format',
        ) from exc
    if not re.fullmatch(r'[A-Za-z0-9._~-]+', cleaned):
        raise ProviderError(
            'A chave Gemini informada nÃ£o Ã© vÃ¡lida. Cole somente o valor da chave, '
            'sem rÃ³tulos, aspas ou espaÃ§os.',
            code='invalid_api_key_format',
        )
    return cleaned


class GeminiProvider(AIProvider):
    name = 'gemini'

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
        _validate_api_key(api_key)
        validate_image_model(self.name, model)
        raise ProviderError(
            'Anonimização por áreas marcadas indisponível no Gemini: o fluxo atual não oferece garantia equivalente de edição restrita por máscara. Nenhum fallback automático será executado.',
            code='gemini_masked_anonymization_unavailable',
            provider=self.name,
            model=model,
            stage='anonimizacao_manual',
        )
    def suggest_orientation(
        self,
        *,
        api_key: str,
        model: str,
        source: Path,
    ) -> str:
        validated_api_key = _validate_api_key(api_key)
        model = configured_text_model(self.name, model)
        image_bytes = safe_path(source).read_bytes()
        image_format, _ = validate_image_bytes(image_bytes)
        mime_type = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}[
            image_format
        ]
        instruction = (
            'Observe somente as informaÃ§Ãµes visÃ­veis nesta tela. Escreva uma orientaÃ§Ã£o curta, '
            'em portuguÃªs do Brasil, para acompanhar a imagem. NÃ£o invente etapas, teclas, '
            'atalhos ou funcionalidades que nÃ£o estejam visÃ­veis. Retorne somente a sugestÃ£o.'
        )
        try:
            if genai.Client is None:
                raise ProviderError('Biblioteca google-genai não está disponível.', code='gemini_dependency_missing')
            client = genai.Client(api_key=validated_api_key)
            response = client.models.generate_content(
                model=model,
                contents=[
                    instruction,
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                ],
            )
            suggestion = (response.text or '').strip()
            if suggestion:
                return suggestion
            raise ProviderError(
                'O Gemini nÃ£o retornou uma sugestÃ£o.',
                code='gemini_empty_suggestion',
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise map_provider_exception(self.name, exc, validated_api_key) from exc

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
        validated_api_key = _validate_api_key(api_key)
        model = validate_text_model(self.name, model)
        contents: list[object] = [prompt, f'TEXTO BRUTO DA NOVIDADE:\n{raw_text}']
        for image_id, path in images:
            image_bytes = safe_path(path).read_bytes()
            image_format, _ = validate_image_bytes(image_bytes)
            mime_type = {
                'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'
            }[image_format]
            contents.extend((
                f'IMAGEM: {image_id}',
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            ))
        try:
            if genai.Client is None:
                raise ProviderError('Biblioteca google-genai não está disponível.', code='gemini_dependency_missing')
            client = genai.Client(api_key=validated_api_key)
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type='application/json',
                    response_json_schema=NoveltyPlan.model_json_schema(),
                    max_output_tokens=max_output_tokens,
                ),
            )
            if response.parsed:
                return NoveltyPlan.model_validate(response.parsed)
            if response.text:
                return NoveltyPlan.model_validate_json(response.text)
            raise ProviderError(
                'O Gemini nÃ£o retornou a estrutura da novidade.',
                code='gemini_empty_novelty_plan',
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise map_provider_exception(self.name, exc, validated_api_key) from exc

    def review_novelty(
        self, *, api_key, model, raw_text, document, image_ids, prompt,
        image_descriptions=None, max_output_tokens: int | None = None
    ) -> FidelityReview:
        validated_api_key = _validate_api_key(api_key)
        model = validate_text_model(self.name, model)
        payload = json.dumps({
            'texto_bruto': raw_text,
            'documento': document,
            'imagens_enviadas': image_ids,
        }, ensure_ascii=False)
        try:
            if genai.Client is None:
                raise ProviderError('Biblioteca google-genai não está disponível.', code='gemini_dependency_missing')
            client = genai.Client(api_key=validated_api_key)
            response = client.models.generate_content(
                model=model,
                contents=[prompt, payload],
                config=types.GenerateContentConfig(
                    response_mime_type='application/json',
                    response_json_schema=FidelityReview.model_json_schema(),
                    max_output_tokens=max_output_tokens,
                ),
            )
            if response.parsed:
                return FidelityReview.model_validate(response.parsed)
            if response.text:
                return FidelityReview.model_validate_json(response.text)
            raise ProviderError(
                'O Gemini n\u00e3o retornou a revis\u00e3o da mat\u00e9ria.',
                code='gemini_empty_review',
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise map_provider_exception(self.name, exc, validated_api_key) from exc

    def generate_cover(self, *, api_key, model, prompt, destination) -> Path:
        validated_api_key = _validate_api_key(api_key)
        model = validate_image_model(self.name, model)
        destination = safe_path(destination)
        try:
            if genai.Client is None:
                raise ProviderError('Biblioteca google-genai não está disponível.', code='gemini_dependency_missing')
            client = genai.Client(api_key=validated_api_key)
            response = client.models.generate_content(
                model=model,
                contents=[prompt],
                config=types.GenerateContentConfig(response_modalities=['IMAGE']),
            )
            for candidate in response.candidates or []:
                for part in candidate.content.parts if candidate.content else []:
                    if part.inline_data and part.inline_data.data:
                        with Image.open(io.BytesIO(bytes(part.inline_data.data))) as image:
                            image.load()
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            image.convert('RGB').save(destination, 'PNG')
                        return destination
            raise ProviderError(
                'O Gemini n\u00e3o retornou a imagem de capa.',
                code='gemini_empty_cover',
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise map_provider_exception(self.name, exc, validated_api_key) from exc
