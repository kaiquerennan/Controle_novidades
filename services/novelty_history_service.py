from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from services.image_service import DATA_ROOT, safe_path


EXPORTS_ROOT = DATA_ROOT / 'exports'
METADATA_NAME = 'novidade.json'


@dataclass(slots=True)
class LaunchedNovelty:
    session_id: str
    title: str = ''
    created_at: datetime | None = None
    text: str = ''
    image_count: int = 0
    has_cover: bool = False
    directory: Path | None = None
    html_path: Path | None = None
    xml_path: Path | None = None
    zip_path: Path | None = None

    @property
    def created_at_label(self) -> str:
        return self.created_at.strftime('%d/%m/%Y %H:%M') if self.created_at else ''

    @property
    def display_title(self) -> str:
        return self.title.strip() or f'Sem título (sessão {self.session_id[:8]})'

    @property
    def excerpt(self) -> str:
        linhas = [linha.strip() for linha in self.text.splitlines() if linha.strip()]
        return linhas[0][:280] if linhas else ''


class NoveltyHistoryService:
    """Lista as novidades que este aplicativo exportou.

    Cada sessão exportada vira uma pasta em data/exports. Só entram na lista as
    que têm novidade.json: as demais pastas são artefatos parciais de sessões
    que não chegaram à exportação.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = safe_path(root or EXPORTS_ROOT)

    def list_novelties(self, *, search: str = '') -> list[LaunchedNovelty]:
        if not self.root.is_dir():
            return []
        found = [
            novelty
            for folder in self.root.iterdir()
            if folder.is_dir()
            if (novelty := self._read(folder)) is not None
        ]
        term = str(search or '').strip().lower()
        if term:
            found = [
                item
                for item in found
                if term in item.title.lower()
                or term in item.text.lower()
                or term in item.session_id.lower()
            ]
        found.sort(key=lambda item: (item.created_at is None, item.created_at), reverse=True)
        return found

    def _read(self, folder: Path) -> LaunchedNovelty | None:
        metadata_path = folder / 'novidade' / METADATA_NAME
        if not metadata_path.is_file():
            return None
        try:
            payload = json.loads(metadata_path.read_text(encoding='utf-8-sig'))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        novelty_dir = metadata_path.parent
        return LaunchedNovelty(
            session_id=str(payload.get('session_id') or folder.name),
            title=str(payload.get('titulo') or ''),
            created_at=self._parse_datetime(payload.get('created_at')),
            text=str(payload.get('texto_novidade') or ''),
            image_count=len(payload.get('imagens') or []),
            has_cover=bool(payload.get('capa')),
            directory=novelty_dir,
            html_path=self._existing(novelty_dir / 'novidade.html'),
            xml_path=self._existing(novelty_dir / 'novidade.xml'),
            zip_path=self._existing(folder / 'emc_novidade_visual_ia.zip'),
        )

    @staticmethod
    def _existing(path: Path) -> Path | None:
        return path if path.is_file() else None

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(value.strip().replace('Z', '+00:00')).replace(
                tzinfo=None
            )
        except ValueError:
            return None


__all__ = ['EXPORTS_ROOT', 'LaunchedNovelty', 'NoveltyHistoryService']
