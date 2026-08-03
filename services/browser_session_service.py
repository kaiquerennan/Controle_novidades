from __future__ import annotations

import os
import secrets

from services.image_service import DATA_ROOT, safe_path


STORAGE_SECRET_ENVIRONMENT = 'EMC_STORAGE_SECRET'
SECRET_PATH = DATA_ROOT / 'storage_secret.local'
SESSIONS_ROOT = DATA_ROOT / 'sessions'


def resolve_storage_secret() -> str:
    """Segredo que assina o cookie de identificacao do navegador.

    Precisa ser estavel entre reinicios: se mudar, todo navegador perde o
    vinculo com a propria sessao. Vem do ambiente quando definido; senao é
    gerado uma vez e guardado em data/storage_secret.local.
    """
    do_ambiente = os.getenv(STORAGE_SECRET_ENVIRONMENT, '').strip()
    if do_ambiente:
        return do_ambiente
    caminho = safe_path(SECRET_PATH)
    if caminho.is_file():
        gravado = caminho.read_text(encoding='utf-8').strip()
        if gravado:
            return gravado
    gerado = secrets.token_urlsafe(48)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(gerado, encoding='utf-8')
    return gerado


def session_state_path(session_id: str) -> 'os.PathLike[str]':
    """Arquivo de estado da tela de uma sessao, isolado por navegador."""
    return safe_path(SESSIONS_ROOT / f'{session_id}.json')


__all__ = [
    'SECRET_PATH',
    'SESSIONS_ROOT',
    'STORAGE_SECRET_ENVIRONMENT',
    'resolve_storage_secret',
    'session_state_path',
]
