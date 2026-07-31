from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from services.image_service import DATA_ROOT, safe_path


CONFIG_VERSION = 1
DEFAULT_PATH = DATA_ROOT / 'occurrence_dismissed.local.json'


@dataclass(slots=True)
class DismissedOccurrence:
    number: int
    dismissed_at: str
    reason: str = ''


class OccurrenceDismissalService:
    """Guarda quais ocorrencias nao devem virar novidade.

    O banco de ocorrencias e somente leitura, entao a exclusao acontece apenas
    nesta lista local: a ocorrencia some do menu, mas nada e alterado na origem.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = safe_path(path or DEFAULT_PATH)

    def load(self) -> dict[int, DismissedOccurrence]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8-sig'))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict) or payload.get('version') != CONFIG_VERSION:
            return {}
        dismissed: dict[int, DismissedOccurrence] = {}
        for item in payload.get('ocorrencias') or []:
            if not isinstance(item, dict):
                continue
            try:
                number = int(item.get('numero'))
            except (TypeError, ValueError):
                continue
            dismissed[number] = DismissedOccurrence(
                number=number,
                dismissed_at=str(item.get('excluida_em') or ''),
                reason=str(item.get('motivo') or ''),
            )
        return dismissed

    def numbers(self) -> set[int]:
        return set(self.load())

    def is_dismissed(self, number: int) -> bool:
        return int(number) in self.load()

    def dismiss(self, number: int, reason: str = '') -> DismissedOccurrence:
        dismissed = self.load()
        record = DismissedOccurrence(
            number=int(number),
            dismissed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
            reason=str(reason or '').strip(),
        )
        dismissed[record.number] = record
        self._save(dismissed)
        return record

    def restore(self, number: int) -> bool:
        dismissed = self.load()
        if int(number) not in dismissed:
            return False
        del dismissed[int(number)]
        self._save(dismissed)
        return True

    def _save(self, dismissed: dict[int, DismissedOccurrence]) -> None:
        payload: dict[str, Any] = {
            'version': CONFIG_VERSION,
            'ocorrencias': [
                {
                    'numero': record.number,
                    'excluida_em': record.dismissed_at,
                    'motivo': record.reason,
                }
                for record in sorted(dismissed.values(), key=lambda item: item.number)
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = safe_path(self.path.with_suffix('.tmp'))
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)


__all__ = ['CONFIG_VERSION', 'DEFAULT_PATH', 'DismissedOccurrence', 'OccurrenceDismissalService']
