from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from services.image_service import DATA_ROOT, safe_path


CONFIG_VERSION = 1
DEFAULT_PATH = DATA_ROOT / 'whatsapp_enviadas.local.json'


@dataclass(slots=True)
class SentSuggestion:
    identifier: str
    sent_at: str
    title: str = ''

    @property
    def sent_at_label(self) -> str:
        try:
            momento = datetime.fromisoformat(self.sent_at)
        except ValueError:
            return ''
        return momento.astimezone().strftime('%d/%m/%Y')


class SuggestionDispatchService:
    """Guarda o que ja foi divulgado, para a tela parar de sugerir.

    E uma marca local, igual a da ocorrencia dispensada: nada e gravado no banco
    e nada e enviado por aqui. Quem marca e o usuario, depois de mandar a
    mensagem -- o envio em si continua sendo um ato dele, na tela do WhatsApp.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = safe_path(path or DEFAULT_PATH)

    def load(self) -> dict[str, SentSuggestion]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8-sig'))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict) or payload.get('version') != CONFIG_VERSION:
            return {}
        enviadas: dict[str, SentSuggestion] = {}
        for item in payload.get('enviadas') or []:
            if not isinstance(item, dict):
                continue
            identificador = str(item.get('id') or '').strip()
            if not identificador:
                continue
            enviadas[identificador] = SentSuggestion(
                identifier=identificador,
                sent_at=str(item.get('enviada_em') or ''),
                title=str(item.get('titulo') or ''),
            )
        return enviadas

    def identifiers(self) -> set[str]:
        return set(self.load())

    def mark(self, identifier: str, title: str = '') -> SentSuggestion:
        enviadas = self.load()
        registro = SentSuggestion(
            identifier=str(identifier).strip(),
            sent_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
            title=str(title or '').strip(),
        )
        enviadas[registro.identifier] = registro
        self._save(enviadas)
        return registro

    def unmark(self, identifier: str) -> bool:
        enviadas = self.load()
        chave = str(identifier).strip()
        if chave not in enviadas:
            return False
        del enviadas[chave]
        self._save(enviadas)
        return True

    def _save(self, enviadas: dict[str, SentSuggestion]) -> None:
        payload: dict[str, Any] = {
            'version': CONFIG_VERSION,
            'enviadas': [
                {
                    'id': registro.identifier,
                    'enviada_em': registro.sent_at,
                    'titulo': registro.title,
                }
                for registro in sorted(
                    enviadas.values(), key=lambda item: item.sent_at, reverse=True
                )
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = safe_path(self.path.with_suffix('.tmp'))
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        temporary.replace(self.path)


__all__ = [
    'CONFIG_VERSION',
    'DEFAULT_PATH',
    'SentSuggestion',
    'SuggestionDispatchService',
]
