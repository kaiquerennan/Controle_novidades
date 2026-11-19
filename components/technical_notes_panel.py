"""Tela "Notas técnicas": lista as notas do arquivo conteudo/notas_tecnicas.md."""

from __future__ import annotations

from typing import Callable

from nicegui import ui

from services.technical_notes_service import NOTES_PATH, TechnicalNote, load_notes
from services.image_service import PROJECT_ROOT


def _relative_path() -> str:
    return str(NOTES_PATH.relative_to(PROJECT_ROOT)).replace('\\', '/')


def _render_note(nota: TechnicalNote) -> None:
    with ui.card().classes('simple-card w-full p-5 gap-2'):
        with ui.row().classes('w-full items-start justify-between no-wrap gap-3'):
            with ui.column().classes('gap-1'):
                ui.label(nota.title).classes('text-lg font-bold')
                if nota.date_label:
                    ui.label(nota.date_label).classes('muted text-sm')
            if nota.link:
                # Sem parâmetro na lambda: o NiceGUI passaria o evento do clique
                # no lugar do link. Cada cartão tem a sua função, então não há
                # o problema do laço reaproveitar a última nota.
                link = nota.link
                ui.button(
                    'Abrir nota', icon='open_in_new',
                    on_click=lambda: ui.navigate.to(link, new_tab=True),
                ).props('flat dense no-caps color=primary')
        if nota.tags:
            with ui.row().classes('gap-1'):
                for chave, valor in nota.tags:
                    ui.chip(f'{chave}: {valor}').props('dense outline color=primary')
        if nota.body:
            ui.markdown(nota.body).classes('w-full')


def render_technical_notes() -> Callable[[], None]:
    """Desenha a tela e devolve a função que relê o arquivo."""
    estado: dict = {'notas': [], 'busca': ''}

    @ui.refreshable
    def lista() -> None:
        notas = [nota for nota in estado['notas'] if nota.matches(estado['busca'])]
        if not estado['notas']:
            with ui.card().classes('simple-card w-full p-8 gap-2 items-center'):
                ui.icon('article').classes('text-5xl muted')
                ui.label('Nenhuma nota técnica cadastrada ainda.').classes('font-bold')
                ui.label(
                    f'Escreva as notas no arquivo {_relative_path()} da pasta do '
                    'aplicativo. Cada nota começa com uma linha "## Título".'
                ).classes('muted text-sm text-center')
            return
        if not notas:
            ui.label('Nenhuma nota encontrada para a busca.').classes('muted text-sm px-1')
            return
        ui.label(f'{len(notas)} nota(s)').classes('muted text-sm px-1')
        for nota in notas:
            _render_note(nota)

    def reload() -> None:
        try:
            estado['notas'] = load_notes()
        except (OSError, UnicodeDecodeError) as exc:
            estado['notas'] = []
            ui.notify(f'Não foi possível ler {_relative_path()}: {exc}', type='negative')
        lista.refresh()

    def buscar(valor: str | None) -> None:
        estado['busca'] = valor or ''
        lista.refresh()

    with ui.card().classes('simple-card w-full p-5 gap-3'):
        with ui.row().classes('w-full items-center justify-between no-wrap gap-3'):
            with ui.column().classes('gap-0'):
                ui.label('Notas técnicas').classes('text-2xl font-bold')
                ui.label(
                    'Notas técnicas dos órgãos fiscais e o que muda para a EMC Sistemas.'
                ).classes('muted text-sm')
            with ui.row().classes('items-center gap-2 no-wrap'):
                ui.input(
                    placeholder='Buscar por título ou texto',
                    on_change=lambda event: buscar(event.value),
                ).props(
                    'outlined dense clearable debounce=300 prepend-icon=search '
                    'style="min-width: 20rem"'
                ).mark('busca-nota-tecnica')
                ui.button(icon='refresh', on_click=reload).props(
                    'flat color=primary'
                ).tooltip('Recarregar o arquivo')
    # Ler um arquivo local é instantâneo: a tela já nasce com as notas, e abrir
    # o menu relê para pegar o que foi editado depois.
    try:
        estado['notas'] = load_notes()
    except (OSError, UnicodeDecodeError):
        estado['notas'] = []
    lista()
    return reload


__all__ = ['render_technical_notes']
