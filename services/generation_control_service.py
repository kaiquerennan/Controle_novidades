from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, slots=True)
class ActiveGeneration:
    generation_id: str
    idempotency_key: str


class SessionGenerationRegistry:
    """Coordena gerações entre páginas/reconexões da mesma sessão NiceGUI."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._active: dict[str, ActiveGeneration] = {}
        self._registry_lock = Lock()

    def lock_for(self, session_id: str) -> asyncio.Lock:
        with self._registry_lock:
            return self._locks.setdefault(session_id, asyncio.Lock())

    def is_active(self, session_id: str) -> bool:
        with self._registry_lock:
            return session_id in self._active

    def try_begin(
        self,
        session_id: str,
        generation_id: str,
        idempotency_key: str,
    ) -> bool:
        with self._registry_lock:
            lock = self._locks.setdefault(session_id, asyncio.Lock())
            if session_id in self._active or lock.locked():
                return False
            self._active[session_id] = ActiveGeneration(
                generation_id=generation_id,
                idempotency_key=idempotency_key,
            )
            return True

    def finish(self, session_id: str, generation_id: str) -> None:
        with self._registry_lock:
            active = self._active.get(session_id)
            if active and active.generation_id == generation_id:
                self._active.pop(session_id, None)

    def active_generation(self, session_id: str) -> ActiveGeneration | None:
        with self._registry_lock:
            return self._active.get(session_id)


GENERATION_REGISTRY = SessionGenerationRegistry()
