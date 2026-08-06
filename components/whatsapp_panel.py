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

# Enquanto a conta nao esta vinculada a tela precisa acompanhar o gateway de perto:
# o QR e trocado a cada 60 s e o pareamento acontece no celular, sem nada avisar o
# navegador. Ja conectada, uma verificacao esparsa basta para perceber uma queda.
POLL_WAITING_SECONDS = 3.0
POLL_CONNECTED_SECONDS = 20.0
CELEBRATION_SECONDS = 5.0

CONNECTION_ANIMATION_CSS = '''
    <style>
      @keyframes emc-connected-pop {
        0%   { transform: scale(0.4); opacity: 0; }
        60%  { transform: scale(1.12); opacity: 1; }
        100% { transform: scale(1); opacity: 1; }
      }
      @keyframes emc-connected-ring {
        0%   { transform: scale(0.6); opacity: 0.55; }
        100% { transform: scale(1.9); opacity: 0; }
      }
      @keyframes emc-connected-fade {
        0%   { transform: translateY(6px); opacity: 0; }
        100% { transform: translateY(0); opacity: 1; }
      }
      .emc-connected-ring {
        position: relative; display: flex;
        align-items: center; justify-content: center;
      }
      .emc-connected-ring::after {
        content: ''; position: absolute; inset: -10px;
        border-radius: 9999px; border: 3px solid #21ba45;
        animation: emc-connected-ring 1.1s ease-out 2;
      }
      .emc-connected-icon {
        animation: emc-connected-pop 0.6s cubic-bezier(.22, 1.2, .36, 1) both;
      }
      .emc-connected-text {
        animation: emc-connected-fade 0.5s ease-out 0.25s both;
      }
      @media (prefers-reduced-motion: reduce) {
        .emc-connected-icon, .emc-connected-text { animation: none; }
        .emc-connected-ring::after { animation: none; opacity: 0; }
      }
    </style>
'''


def render_whatsapp_panel(gateway: WhatsAppGatewayService | None = None) -> None:
    """Vinculo da conta do WhatsApp, por leitura do QR."""
    service = gateway or WhatsAppGatewayService()
    state: dict[str, Any] = {
        'status': None, 'qr': '', 'busy': False,
        'just_connected': False, 'poll': None,
    }

    def end_celebration() -> None:
        state['just_connected'] = False
        painel.refresh()

    async def refresh_state() -> None:
        anterior = state['status']
        state['status'] = await run.io_bound(service.status)
        situacao = state['status']
        state['qr'] = (
            await run.io_bound(service.qr_code) if situacao.has_qr else ''
        )
        # Só comemora a transição. Sem o estado anterior a animação dispararia a
        # cada abertura de tela com a conta já vinculada, virando ruído.
        if situacao.connected and anterior is not None and not anterior.connected:
            state['just_connected'] = True
            ui.notify('WhatsApp conectado!', type='positive')
            ui.timer(CELEBRATION_SECONDS, end_celebration, once=True)
        poll = state['poll']
        if poll is not None:
            poll.interval = (
                POLL_CONNECTED_SECONDS if situacao.connected else POLL_WAITING_SECONDS
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

    async def force_new_qr() -> None:
        """Descarta a sessao gravada para o gateway emitir um QR novo.

        Enquanto as credenciais existirem o Baileys insiste nelas e nao emite
        codigo algum, entao esta e a unica forma de forcar um novo pareamento.
        """
        try:
            await run.io_bound(service.logout)
            ui.notify(
                'Sessão anterior descartada. O novo código aparece em instantes.',
                type='warning',
            )
        except WhatsAppGatewayError as exc:
            ui.notify(exc.user_message, type='negative')
        await refresh_state()

    def confirm_new_qr() -> None:
        with ui.dialog() as dialog, ui.card().classes('p-5 gap-3 max-w-md'):
            ui.label('Gerar um novo código?').classes('text-lg font-bold')
            ui.label(
                'A sessão gravada é descartada e a conta precisa ser vinculada de '
                'novo pelo celular. Use apenas quando a reconexão não concluir '
                'sozinha.'
            ).classes('text-sm muted')

            async def confirmar() -> None:
                dialog.close()
                await force_new_qr()

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancelar', on_click=dialog.close).props('flat')
                ui.button(
                    'Gerar novo código', on_click=confirmar,
                ).props('color=negative')
        dialog.open()

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
            if state['just_connected']:
                with ui.column().classes('w-full items-center gap-2 py-4'):
                    with ui.element('div').classes('emc-connected-ring'):
                        ui.icon(
                            'check_circle', size='72px', color='positive',
                        ).classes('emc-connected-icon').mark('animacao-conectado')
                    ui.label('Conectado!').classes(
                        'text-2xl font-bold text-green-700 emc-connected-text'
                    )
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
                'O código expira em cerca de 60 segundos. Use Atualizar para gerar outro.'
            ).classes('text-xs muted')
            return

        # Sem QR e sem conexão: o Baileys está tentando voltar. Ele reconecta
        # sozinho quando as credenciais gravadas ainda valem e emite um código
        # novo quando não valem -- e o polling coloca esse código na tela assim
        # que ele existir, sem o usuário pedir.
        with ui.row().classes('items-center gap-3 py-2'):
            ui.spinner(size='md')
            ui.label('Reconectando ao WhatsApp...').classes('text-sm')
        ui.label(
            'Se for necessário vincular de novo, o código aparece aqui '
            'automaticamente. Se a reconexão não concluir, gere um código novo.'
        ).classes('muted text-sm')
        ui.button(
            'Gerar novo código', icon='qr_code_2', on_click=confirm_new_qr,
        ).props('outline color=primary').mark('novo-qr-whatsapp')

    ui.add_head_html(CONNECTION_ANIMATION_CSS)
    with ui.card().classes('simple-card w-full p-5 gap-3'):
        with ui.column().classes('gap-0'):
            ui.label('WhatsApp').classes('text-2xl font-bold')
        painel()
        state['poll'] = ui.timer(POLL_WAITING_SECONDS, refresh_state)
        ui.timer(0.1, refresh_state, once=True)


__all__ = ['render_whatsapp_panel']
