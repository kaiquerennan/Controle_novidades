from __future__ import annotations

from core.config import IMAGE_MODEL_MARKERS, IMAGE_MODELS, TEXT_MODELS
from services.providers.exceptions import ProviderError


def configured_text_model(provider_name: str, selected_model: str = '') -> str:
    provider = provider_name.strip().lower()
    model = str(selected_model or TEXT_MODELS.get(provider) or '').strip()
    if not model:
        raise ProviderError(
            f'Nenhum modelo textual interno foi configurado para o provedor {provider_name}.',
            code='missing_text_model', provider=provider,
            stage='configuracao_modelo_textual',
        )
    return validate_text_model(provider, model)


def _is_image_model(provider_name: str, model: str) -> bool:
    lowered = model.strip().lower()
    markers = IMAGE_MODEL_MARKERS.get(provider_name.strip().lower(), ('gpt-image',))
    return any(marker in lowered for marker in markers)


def validate_text_model(provider_name: str, model: str) -> str:
    cleaned = model.strip()
    if not cleaned:
        raise ProviderError(
            'O modelo textual interno não foi configurado.',
            code='missing_text_model', provider=provider_name,
            stage='configuracao_modelo_textual',
        )
    if cleaned.lower().startswith('gpt-image') or _is_image_model(provider_name, cleaned):
        raise ProviderError(
            f'O modelo {cleaned} é exclusivo para imagens e não pode ser usado no fluxo editorial.',
            code='image_model_in_text_flow', provider=provider_name, model=cleaned,
            stage='configuracao_modelo_textual',
        )
    return cleaned


def validate_image_model(provider_name: str, model: str) -> str:
    cleaned = model.strip()
    if not cleaned:
        raise ProviderError(
            'Informe o modelo de imagem.',
            code='missing_image_model', provider=provider_name,
            stage='configuracao_modelo_imagem',
        )
    if cleaned in TEXT_MODELS.values() or not _is_image_model(provider_name, cleaned):
        raise ProviderError(
            f'O modelo {cleaned} não é um modelo de imagem válido para gerar a capa.',
            code='text_model_in_image_flow', provider=provider_name, model=cleaned,
            stage='configuracao_modelo_imagem',
        )
    return cleaned


def configured_image_model(provider_name: str, selected_model: str = '') -> str:
    provider = provider_name.strip().lower()
    model = str(selected_model or IMAGE_MODELS.get(provider) or '').strip()
    if not model:
        raise ProviderError(
            f'Nenhum modelo de imagem interno foi configurado para o provedor {provider_name}.',
            code='missing_image_model', provider=provider,
            stage='configuracao_modelo_imagem',
        )
    return validate_image_model(provider, model)


def is_model_availability_error(error: Exception) -> bool:
    code = str(getattr(error, 'code', '')).lower()
    return any(marker in code for marker in (
        'model_not_found', 'model_permission_denied', 'model_incompatible',
    ))


def text_model_unavailable_error(
    provider_name: str, model: str, error: Exception,
) -> ProviderError:
    return ProviderError(
        f'O modelo textual configurado ({model}) não está disponível para esta chave '
        'ou não é compatível com o fluxo editorial. Verifique o acesso ao modelo na conta.',
        code='text_model_unavailable', provider=provider_name, model=model,
        stage='configuracao_modelo_textual',
        status_http=getattr(error, 'status_http', None),
        exception_type=error.__class__.__name__,
    )