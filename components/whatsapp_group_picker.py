from __future__ import annotations

from typing import Any, Callable

from nicegui import run, ui

from services.whatsapp_gateway_service import (
    WhatsAppGatewayError,
    WhatsAppGatewayService,
    WhatsAppGroup,
)


def build_group_picker(
    gateway: WhatsAppGatewayService,
    *,
    on_pick: Callable[[WhatsAppGroup], Any],
    mark: str = '',
) -> Callable[[], Any]:
    """Escolha de grupo ou comunidade, em um diálogo reaproveitado.

    O diálogo é montado uma única vez, no lugar onde esta função é chamada, e a
    função devolvida apenas o abre. A lista é buscada na primeira abertura: em
    contas com muitos grupos a consulta leva alguns segundos, e cobrá-la de quem
    não vai mexer no destino atrasaria a tela inteira.
    """
    estado: dict[str, Any] = {
        'grupos': [], 'filtro': '', 'carregando': False, 'erro': '',
    }

    def escolher(grupo: WhatsAppGroup) -> None:
        on_pick(grupo)
        dialogo.close()

    def filtrar(valor: str) -> None:
        estado['filtro'] = str(valor or '')
        lista.refresh()

    @ui.refreshable
    def lista() -> None:
        if estado['carregando']:
            with ui.row().classes('items-center gap-2 p-2'):
                ui.spinner(size='sm')
                ui.label('Carregando os grupos...').classes('muted text-sm')
            return
        if estado['erro']:
            ui.label(estado['erro']).classes('text-sm text-amber-800')
            return
        if not estado['grupos']:
            ui.label(
                'Nenhum grupo encontrado nesta conta. Se você acabou de entrar '
                'em um, use Recarregar.'
            ).classes('muted text-sm p-2')
            return
        filtro = str(estado['filtro'] or '').strip().lower()
        visiveis = [
            grupo for grupo in estado['grupos'] if filtro in grupo.name.lower()
        ]
        if not visiveis:
            ui.label(f'Nenhum grupo com "{estado["filtro"]}".').classes(
                'muted text-sm p-2'
            )
            return
        ui.label(f'{len(visiveis)} grupo(s)').classes('muted text-xs px-1')
        with ui.list().props('bordered separator').classes(
            'w-full max-h-72 overflow-y-auto rounded-lg'
        ).mark(mark or 'picker-grupos'):
            for grupo in visiveis:
                with ui.item(
                    on_click=lambda _=None, alvo=grupo: escolher(alvo),
                ).props('clickable'):
                    with ui.item_section().props('avatar'):
                        ui.icon('groups').classes('text-slate-500')
                    with ui.item_section():
                        ui.item_label(grupo.name)
                        if grupo.summary:
                            ui.item_label(grupo.summary).props('caption')
                    if not grupo.can_send:
                        with ui.item_section().props('side'):
                            ui.badge('sem permissão').props('color=orange outline')

    async def carregar(*, forcar: bool = False) -> None:
        if estado['carregando']:
            return
        estado['carregando'] = True
        estado['erro'] = ''
        lista.refresh()
        try:
            estado['grupos'] = await run.io_bound(
                lambda: gateway.list_groups(refresh=forcar)
            )
        except WhatsAppGatewayError as exc:
            estado['grupos'] = []
            estado['erro'] = exc.user_message
        finally:
            estado['carregando'] = False
            lista.refresh()

    async def recarregar() -> None:
        await carregar(forcar=True)

    with ui.dialog() as dialogo, ui.card().classes('w-full max-w-xl p-5 gap-3'):
        ui.label('Escolher o destino').classes('text-xl font-bold')
        ui.label(
            'Os grupos e as comunidades de que a conta vinculada participa. O '
            'grupo de avisos de uma comunidade aparece marcado.'
        ).classes('muted text-sm')
        with ui.row().classes('w-full items-center no-wrap gap-2'):
            ui.input(
                placeholder='Buscar grupo pelo nome',
                on_change=lambda event: filtrar(event.value),
            ).props(
                'outlined dense clearable debounce=300 prepend-icon=search'
            ).classes('grow')
            ui.button(icon='refresh', on_click=recarregar).props(
                'flat color=primary'
            ).tooltip('Recarregar os grupos')
        lista()
        with ui.row().classes('w-full justify-end'):
            ui.button('Fechar', on_click=dialogo.close).props('flat')

    async def abrir() -> None:
        dialogo.open()
        if not estado['grupos'] and not estado['erro']:
            await carregar()

    return abrir


__all__ = ['build_group_picker']
