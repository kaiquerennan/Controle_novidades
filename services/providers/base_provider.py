from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from models.novelty_plan import FidelityReview, NoveltyPlan


class AIProvider(ABC):
    def set_api_audit_context(self, audit, logical_call: int) -> None:
        self._api_audit = audit
        self._api_logical_call = logical_call

    def clear_api_audit_context(self) -> None:
        self._api_audit = None
        self._api_logical_call = 0

    @abstractmethod
    def treat_image(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        source: Path,
        destination: Path,
    ) -> Path:
        """OperaÃ§Ã£o legada de imagem, preservada fora do fluxo editorial principal."""

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
        """Anonimiza somente areas manuais confirmadas pelo usuario."""
        raise NotImplementedError
    @abstractmethod
    def suggest_orientation(
        self,
        *,
        api_key: str,
        model: str,
        source: Path,
    ) -> str:
        """Gera uma orientaÃ§Ã£o curta baseada somente na imagem."""

    @abstractmethod
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
        """Analisa texto e imagens e retorna a montagem estruturada da novidade."""

    def review_novelty(
        self,
        *,
        api_key: str,
        model: str,
        raw_text: str,
        document: dict,
        image_ids: list[str],
        image_descriptions: list[dict] | None = None,
        prompt: str = '',
        max_output_tokens: int | None = None,
    ) -> FidelityReview:
        """Revisa fidelidade sem reenviar as imagens."""
        raise NotImplementedError

    def generate_cover(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        destination: Path,
    ) -> Path:
        """Gera uma capa editorial separada das capturas do sistema."""
        raise NotImplementedError
