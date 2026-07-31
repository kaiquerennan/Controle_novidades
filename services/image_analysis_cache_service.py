from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from services.image_service import DATA_ROOT, safe_path


CACHE_VERSION = 1


@dataclass(slots=True)
class StructuredImageDescription:
    file_hash: str
    description: str
    visible_text: list[str]
    elements: list[str]

    def to_prompt_dict(self, image_id: str) -> dict[str, Any]:
        return {
            'imagem_id': image_id,
            'descricao': self.description,
            'texto_visivel': list(self.visible_text),
            'elementos': list(self.elements),
        }


class ImageAnalysisCache:
    def __init__(self, root: Path | None = None) -> None:
        self.root = safe_path(root or DATA_ROOT / 'image_analysis_cache')
        self.thumbnail_dir = safe_path(self.root / 'thumbnails')

    def description_path(self, file_hash: str) -> Path:
        return safe_path(self.root / f'{file_hash}.json')

    def thumbnail_path(self, file_hash: str) -> Path:
        return safe_path(self.thumbnail_dir / f'{file_hash}.png')

    def load(self, file_hash: str) -> StructuredImageDescription | None:
        path = self.description_path(file_hash)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if value.get('version') != CACHE_VERSION or value.get('file_hash') != file_hash:
                return None
            return StructuredImageDescription(
                file_hash=file_hash,
                description=str(value.get('description') or ''),
                visible_text=[str(item) for item in value.get('visible_text') or []],
                elements=[str(item) for item in value.get('elements') or []],
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def save(self, description: StructuredImageDescription) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.description_path(description.file_hash)
        payload = {
            'version': CACHE_VERSION,
            **asdict(description),
        }
        temporary = safe_path(path.with_suffix('.tmp'))
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
            encoding='utf-8',
        )
        temporary.replace(path)
        return path
