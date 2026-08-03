from __future__ import annotations

from typing import Any

from nicegui import run, ui

from services.whatsapp_gateway_service import (
    WhatsAppGatewayError,
    WhatsAppGatewayService,
)


STATUS_COLORS = {
    'conectado': 'green',
    'aguardando_qr': 'orange',
    'conectando': 'blue',
    'desconectado': 'grey',
}


def render_whatsapp_panel(gateway: WhatsAppGatewayService | None = None) -> None:
    """Vinculo da conta do WhatsApp, por leitura do QR."""
    service = gateway or WhatsAppGatewayService()
    state: dict[str, Any] = {'status': None, 'qr': '', 'busy': False}

    async def refresh_state() -> None:
        state['status'] = await run.io_bound(service.status)
        situacao = state['status']
        state['qr'] = (
            await run.io_bound(service.qr_code) if situacao.has_qr else ''
        )
        painel.refresh()

    async def start_service() -> None:
        if state['busy']:
            return
        state['busy'] = True
        painel.refresh()
        try:
            await run.io_bound(service.start)
            ui.notify('Serviço do WhatsApp iniciado.', type='positive')
        except WhatsAppGatewayError as exc:
            ui.notify(exc.user_message, type='negative')
        finally:
            state['busy'] = False
            await refresh_state()

    async def disconnect() -> None:
        try:
            await run.io_bound(service.logout)
            ui.notify('Conta desconectada.', type='warning')
        except WhatsAppGatewayError as exc:
            ui.notify(exc.user_message, type='negative')
        await refresh_state()

    @ui.refreshable
    def painel() -> None:
        situacao = state['status']
        if situacao is None:
            with ui.row().classes('items-center gap-3 p-4'):
                ui.spinner(size='lg')
                ui.label('Verificando o serviço...').classes('muted')
            return

        cor = STATUS_COLORS.get(situacao.connection, 'grey')
        with ui.row().classes('w-full items-center gap-3'):
            ui.badge(situacao.label).props(f'color={cor}')
            if situacao.phone:
                ui.label(f'Número vinculado: {situacao.phone}').classes('text-sm')
            ui.space()
            ui.button(icon='refresh', on_click=refresh_state).props(
                'flat color=primary'
            ).tooltip('Atualizar')

        if situacao.last_error:
            ui.label(situacao.last_error).classes('text-sm text-amber-800')

        if not situacao.running:
            ui.label(
                'O serviço que conversa com o WhatsApp está parado. '
                'Inicie para vincular a conta.'
            ).classes('muted text-sm')
            ui.button(
                'Iniciar serviço', icon='play_arrow', on_click=start_service,
            ).props('color=primary').mark('iniciar-whatsapp')
            return

        if situacao.connected:
            ui.label(
                'A conta está vinculada. As novidades podem ser enviadas pelo WhatsApp.'
            ).classes('text-sm')
            ui.button(
                'Desconectar conta', icon='logout', on_click=disconnect,
            ).props('outline color=negative').mark('desconectar-whatsapp')
            return

        if state['qr']:
            ui.label('Vincular a conta').classes('text-lg font-bold')
            ui.label(
                'No celular, abra o WhatsApp, toque em Aparelhos conectados e '
                'aponte a câmera para o código abaixo.'
            ).classes('text-sm muted')
            ui.image(state['qr']).classes('w-72 h-72').mark('qr-whatsapp')
            ui.label(
                'O código expira em cerca de 20 segundos. Use Atualizar para gerar outro.'
            ).classes('text-xs muted')
            return

        ui.label(
            'Aguardando o código de vinculação. Use Atualizar em alguns segundos.'
        ).classes('muted text-sm')

    with ui.card().classes('simple-card w-full p-5 gap-3'):
        with ui.column().classes('gap-0'):
            ui.label('WhatsApp').classes('text-2xl font-bold')
        painel()
        ui.timer(0.1, refresh_state, once=True)


__all__ = ['render_whatsapp_panel']
