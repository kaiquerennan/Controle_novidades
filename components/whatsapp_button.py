from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from nicegui import app as ui_app, ui


WHATSAPP_LOGO_PATH = Path(__file__).resolve().parents[1] / 'WhatsApp_Logo.png'
# A rota e registrada uma vez, no import. Passar o caminho local direto para o
# ui.image faria o NiceGUI criar uma rota nova a cada card e a cada sessao.
WHATSAPP_LOGO_URL = (
    ui_app.add_static_file(local_file=WHATSAPP_LOGO_PATH)
    if WHATSAPP_LOGO_PATH.is_file()
    else ''
)


def render_whatsapp_button(on_click: Callable[..., Any], *, label: str = '') -> Any:
    """Botão de envio com o logo real; cai no ícone do tema se o arquivo sumir."""
    if not WHATSAPP_LOGO_URL:
        if label:
            return ui.button(label, icon='send', on_click=on_click).props(
                'color=green'
            )
        return ui.button(icon='send', on_click=on_click).props('flat round color=green')
    props = 'outline color=green no-caps' if label else 'flat round dense'
    with ui.button(on_click=on_click).props(props) as botao:
        ui.image(WHATSAPP_LOGO_URL).classes(
            'w-5 h-5 mr-2' if label else 'w-7 h-7'
        )
        if label:
            ui.label(label)
    return botao


__all__ = ['WHATSAPP_LOGO_PATH', 'WHATSAPP_LOGO_URL', 'render_whatsapp_button']
