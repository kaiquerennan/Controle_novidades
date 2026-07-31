from __future__ import annotations

from core.config import PROVIDER_LABELS
from services.providers.base_provider import AIProvider
from services.providers.exceptions import ProviderError


class ProviderFactory:
    @staticmethod
    def create(provider_name: str) -> AIProvider:
        normalized = provider_name.strip().lower()
        if normalized == 'gemini':
            from services.providers.gemini_provider import GeminiProvider

            return GeminiProvider()
        if normalized == 'openai':
            from services.providers.openai_provider import OpenAIProvider

            return OpenAIProvider()
        available = ', '.join(PROVIDER_LABELS.values())
        raise ProviderError(
            f'Provedor inválido. Escolha uma das opções: {available}.',
            code='invalid_provider',
        )
