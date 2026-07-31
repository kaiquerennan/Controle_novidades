from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from typing import Any
from urllib.parse import urlparse

from models.composition import CompositionDocument


@dataclass(slots=True)
class EddieMessage:
    title: str
    link: str
    preview: str


class EddieMessageService:
    def build(
        self,
        document: CompositionDocument,
        *,
        public_link: str,
        exported_metadata: dict[str, Any] | None = None,
    ) -> EddieMessage:
        link = self.validate_public_link(public_link)
        title = document.title or str((exported_metadata or {}).get('titulo') or '').strip()
        title = self._clean_text(title) or 'Novidade EMC'
        preview = (
            'Ola!\n\n'
            'Eu sou o Eddie, Agente de Novidades EMC.\n\n'
            'Preparei uma novidade interessante para voce.\n\n'
            f'{title}\n\n'
            'Clique no link abaixo e conheca todos os detalhes.\n\n'
            f'{link}\n\n'
            'Boa leitura!'
        )
        return EddieMessage(title=title, link=link, preview=preview)

    @staticmethod
    def _clean_text(value: str) -> str:
        text = re.sub(r'\s+', ' ', str(value or '')).strip()
        return re.sub(r'[*_`#]+', '', text).strip()

    @staticmethod
    def validate_public_link(value: str) -> str:
        link = str(value or '').strip()
        parsed = urlparse(link)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            raise ValueError('Informe um link publico completo, iniciando com http:// ou https://.')
        host = (parsed.hostname or '').strip().lower()
        if not host:
            raise ValueError('Informe um link publico valido para a novidade.')
        if host == 'localhost' or host.endswith('.local'):
            raise ValueError('Nao e permitido enviar link localhost ou interno pelo WhatsApp.')
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            if '.' not in host:
                raise ValueError('Informe um dominio publico acessivel ao destinatario.')
        else:
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError('Nao e permitido enviar IP interno ou localhost pelo WhatsApp.')
        return link
