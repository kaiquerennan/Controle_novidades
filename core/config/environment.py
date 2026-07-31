from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / '.env'


def load_environment(path: Path | None = None) -> bool:
    """Carrega o .env do projeto nas variaveis de ambiente do processo.

    Variaveis ja definidas no ambiente têm precedência: o .env preenche apenas o
    que estiver faltando, para que um valor exportado no terminal continue
    valendo. Retorna se o arquivo existia.
    """
    target = path or ENV_PATH
    if not target.is_file():
        return False
    load_dotenv(target, override=False)
    return True


__all__ = ['ENV_PATH', 'load_environment']
