from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from services.image_service import DATA_ROOT, safe_path


STATE_VERSION = 1
_SECRET_FIELDS = {'api_key', 'apikey', 'authorization', 'bearer', 'token', 'secret'}


def _without_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _without_secrets(item)
            for key, item in value.items()
            if str(key).lower() not in _SECRET_FIELDS
        }
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    return value


class ScreenStateStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = safe_path(path or DATA_ROOT / 'screen_state.json')

    def load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or value.get('version') != STATE_VERSION:
            return None
        return value

    def save(self, payload: dict[str, Any]) -> Path:
        value = _without_secrets(deepcopy(payload))
        value['version'] = STATE_VERSION
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = safe_path(self.path.with_suffix('.tmp'))
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        temporary.replace(self.path)
        return self.path

    def clear(self) -> None:
        if self.path.is_file():
            self.path.unlink()