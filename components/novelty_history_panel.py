from __future__ import annotations

from typing import Any

from nicegui import ui

from services.novelty_history_service import LaunchedNovelty, NoveltyHistoryService


def render_novelty_history(
    *,
    history_service: NoveltyHistoryService | None = None,
) -> None:
    """Lista as novidades já exportadas por este aplicativo."""
    history = history_service or NoveltyHistoryService()
    state: dict[str, Any] = {'search': ''}

    @ui.refreshable
    def novelty_list() -> None:
        novelties = history.list_novelties(search=str(state['search']))
        ui.label(f'{len(novelties)} novidade(s) exportada(s)').classes('muted text-sm px-1')
        if not novelties:
            ui.label(
                'Nenhuma novidade exportada ainda. Elas aparecem aqui depois que você '
                'gera e exporta uma novidade.'
            ).classes('muted p-4')
            return
        with ui.column().classes('w-full gap-2'):
            for novelty in novelties:
                _novelty_row(novelty)

    def _novelty_row(novelty: LaunchedNovelty) -> None:
        with ui.card().classes('simple-card w-full p-4 gap-2'):
            with ui.row().classes('w-full items-start justify-between no-wrap gap-3'):
                with ui.column().classes('gap-1'):
                    ui.label(novelty.display_title).classes('text-lg font-bold')
                    with ui.row().classes('items-center gap-2'):
                        if novelty.created_at_label:
                            ui.label(novelty.created_at_label).classes('muted text-sm')
                        ui.badge(f'{novelty.image_count} imagem(ns)').props(
                            'color=blue-grey outline'
                        )
                        if novelty.has_cover:
                            ui.badge('com capa').props('color=indigo outline')
                with ui.row().classes('items-center gap-2 no-wrap'):
                    if novelty.html_path:
                        ui.button(
                            'HTML',
                            icon='code',
                            on_click=lambda _, path=novelty.html_path: ui.download(str(path)),
                        ).props('flat color=primary')
                    if novelty.zip_path:
                        ui.button(
                            'ZIP',
                            icon='download',
                            on_click=lambda _, path=novelty.zip_path: ui.download(str(path)),
                        ).props('flat color=primary')
            if novelty.excerpt:
                ui.label(novelty.excerpt).classes('text-sm whitespace-pre-wrap')
            ui.label(f'Sessão {novelty.session_id}').classes('muted text-xs')

    def apply_search(value: str) -> None:
        state['search'] = str(value or '').strip()
        novelty_list.refresh()

    with ui.card().classes('simple-card w-full p-5 gap-3'):
        with ui.row().classes('w-full items-center justify-between no-wrap gap-3'):
            with ui.column().classes('gap-0'):
                ui.label('Novidades lançadas').classes('text-2xl font-bold')
                ui.label(
                    'Novidades geradas e exportadas por este aplicativo.'
                ).classes('muted text-sm')
            with ui.row().classes('items-center gap-2 no-wrap'):
                busca = ui.input(
                    placeholder='Buscar por título ou texto',
                    on_change=lambda event: apply_search(event.value),
                ).props('outlined dense clearable debounce=500 style="min-width: 20rem"')
                busca.props('prepend-icon=search')
                busca.mark('busca-novidade')
                ui.button(icon='refresh', on_click=novelty_list.refresh).props(
                    'flat color=primary'
                ).tooltip('Recarregar')
        novelty_list()


__all__ = ['render_novelty_history']
