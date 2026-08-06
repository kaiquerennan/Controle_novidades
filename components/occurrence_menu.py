from __future__ import annotations

from typing import Any, Callable

from nicegui import run, ui

from services.occurrence_database_service import OccurrenceDatabaseError
from services.occurrence_dismissal_service import OccurrenceDismissalService
from services.occurrence_query_service import (
    DEFAULT_MONTHS,
    KIND_ANY,
    KIND_FILTER_LABELS,
    OccurrenceQueryService,
    OccurrenceSummary,
)


PREVIEW_LENGTH = 320
KIND_BADGE_COLORS = {'Erro': 'red', 'Mudança': 'teal'}

# Controle segmentado: a faixa clara e a trilha, e so a opcao ativa ganha a
# pastilha. Os botoes inativos precisam ficar transparentes no CSS porque o
# q-btn-toggle sempre pinta um fundo neles.
SEGMENTED_CSS = '''
    <style>
      .emc-segmented {
        background: #eef1f6;
        border: 1px solid #e4e9f2;
        border-radius: 9999px;
        padding: 3px;
      }
      .emc-segmented .q-btn {
        border-radius: 9999px !important;
        min-height: 30px;
        padding: 0 18px;
        font-weight: 600;
        transition: background-color .18s ease, color .18s ease, box-shadow .18s ease;
      }
      .emc-segmented .q-btn:not(.q-btn--active) {
        background: transparent !important;
      }
      .emc-segmented .q-btn:not(.q-btn--active):hover {
        background: rgba(15, 23, 42, .06) !important;
      }
      .emc-segmented .q-btn--active {
        box-shadow: 0 1px 3px rgba(16, 24, 40, .2);
      }
      .emc-segmented .q-btn .q-focus-helper { display: none; }
    </style>
'''


def render_occurrence_menu(
    on_launch: Callable[[OccurrenceSummary], None],
    *,
    query_service: OccurrenceQueryService | None = None,
    dismissal_service: OccurrenceDismissalService | None = None,
    months: int = DEFAULT_MONTHS,
) -> None:
    """Menu das ocorrencias de mudanca candidatas a virar novidade.

    A exclusao e local: o banco de origem e somente leitura, entao a ocorrencia
    apenas deixa de aparecer aqui.
    """
    queries = query_service or OccurrenceQueryService()
    dismissals = dismissal_service or OccurrenceDismissalService()
    state: dict[str, Any] = {
        'search': '',
        'kind': KIND_ANY,
        'show_dismissed': False,
        'error': '',
        'loading': True,
        'occurrences': [],
    }

    def load_occurrences() -> list[OccurrenceSummary]:
        return queries.list_changes(
            search=str(state['search']), months=months, kind=str(state['kind']),
        )

    async def reload() -> None:
        """A consulta vai para outra thread para nao travar a interface."""
        state['loading'] = True
        occurrence_list.refresh()
        try:
            state['occurrences'] = await run.io_bound(load_occurrences)
            state['error'] = ''
        except OccurrenceDatabaseError as exc:
            state['occurrences'] = []
            state['error'] = str(exc)
        except Exception as exc:  # pragma: no cover - falha inesperada do driver
            state['occurrences'] = []
            state['error'] = f'Falha ao consultar as ocorrências: {exc}'
        state['loading'] = False
        occurrence_list.refresh()

    @ui.refreshable
    def occurrence_list() -> None:
        if state['loading']:
            with ui.row().classes('w-full items-center gap-3 p-4'):
                ui.spinner(size='lg')
                ui.label('Carregando ocorrências...').classes('muted')
            return

        occurrences: list[OccurrenceSummary] = list(state['occurrences'])
        if state['error']:
            with ui.row().classes('w-full items-center gap-2 p-4'):
                ui.icon('error_outline').classes('text-red-500')
                ui.label(state['error']).classes('text-red-600')
            ui.label(
                'Configure o acesso com: .venv\\Scripts\\python.exe configurar_banco.py testar'
            ).classes('muted text-xs px-4 pb-3')
            return

        dismissed = dismissals.load()
        if state['show_dismissed']:
            visible = [item for item in occurrences if item.number in dismissed]
        else:
            visible = [item for item in occurrences if item.number not in dismissed]

        if state['show_dismissed']:
            summary = f'{len(visible)} ocorrência(s) excluída(s)'
        elif dismissed:
            summary = f'{len(dismissed)} excluída(s)'
        else:
            summary = ''
        if summary:
            ui.label(summary).classes('muted text-sm px-1')

        if not visible:
            ui.label(
                'Nenhuma ocorrência excluída.'
                if state['show_dismissed']
                else 'Nenhuma ocorrência encontrada para o filtro atual.'
            ).classes('muted p-4')
            return

        # Ocupa a altura restante da janela em vez de uma faixa fixa.
        with ui.column().classes(
            'w-full gap-2 max-h-[calc(100vh-20rem)] min-h-[24rem] overflow-y-auto pr-1'
        ):
            for occurrence in visible:
                _occurrence_row(occurrence)

    def _occurrence_row(occurrence: OccurrenceSummary) -> None:
        with ui.card().classes('simple-card w-full p-4 gap-2'):
            with ui.row().classes('w-full items-center justify-between no-wrap gap-3'):
                with ui.row().classes('items-center gap-2 no-wrap'):
                    ui.label(f'#{occurrence.number}').classes('text-lg font-bold')
                    if occurrence.kind_label:
                        ui.badge(occurrence.kind_label).props(
                            f'color={KIND_BADGE_COLORS.get(occurrence.kind_label, "grey")}'
                        )
                    if occurrence.changed_at_label:
                        ui.label(occurrence.changed_at_label).classes('muted text-sm')
                    if occurrence.system:
                        ui.badge(occurrence.system).props('color=blue-grey outline')
                    if occurrence.module:
                        ui.badge(occurrence.module).props('color=indigo outline')
                with ui.row().classes('items-center gap-2 no-wrap'):
                    if state['show_dismissed']:
                        ui.button(
                            'Restaurar',
                            icon='undo',
                            on_click=lambda _, item=occurrence: restore(item),
                        ).props('flat color=primary')
                    else:
                        ui.button(
                            'Lançar novidade',
                            icon='note_add',
                            on_click=lambda _, item=occurrence: on_launch(item),
                        ).props('color=primary').mark(f'lancar-{occurrence.number}')
                        ui.button(
                            icon='delete',
                            on_click=lambda _, item=occurrence: confirm_dismissal(item),
                        ).props('flat color=negative').mark(
                            f'excluir-{occurrence.number}'
                        ).tooltip('Não lançar novidade para esta ocorrência')
            if occurrence.solicitation or occurrence.justification:
                _section('SOLICITAÇÃO', occurrence.solicitation)
                _section('JUSTIFICATIVA', occurrence.justification)
            else:
                # Nem toda ocorrencia usa os rotulos; mostra a descricao inteira.
                _section('DESCRIÇÃO', occurrence.description)

    def _section(title: str, content: str) -> None:
        texto = str(content or '').strip()
        if not texto:
            return
        preview = texto[:PREVIEW_LENGTH]
        if len(texto) > PREVIEW_LENGTH:
            preview += '...'
        with ui.column().classes('w-full gap-0'):
            ui.label(title).classes('text-xs font-bold muted tracking-wide')
            ui.label(preview).classes('text-sm whitespace-pre-wrap')

    def confirm_dismissal(occurrence: OccurrenceSummary) -> None:
        pending['occurrence'] = occurrence
        confirm_detail.text = (
            f'Confirma que a ocorrência número {occurrence.number} não deve virar novidade? '
            'Ela sai deste menu e nada é alterado no banco de origem.'
        )
        confirm_reason.value = ''
        confirm_dialog.open()

    def dismiss() -> None:
        occurrence = pending.get('occurrence')
        confirm_dialog.close()
        if occurrence is None:
            return
        pending['occurrence'] = None
        dismissals.dismiss(occurrence.number, str(confirm_reason.value or ''))
        occurrence_list.refresh()
        ui.notify(f'Ocorrência #{occurrence.number} não será lançada.', type='warning')

    def restore(occurrence: OccurrenceSummary) -> None:
        dismissals.restore(occurrence.number)
        occurrence_list.refresh()
        ui.notify(f'Ocorrência #{occurrence.number} voltou para a lista.', type='positive')

    async def apply_search(value: str) -> None:
        term = str(value or '').strip()
        if term == state['search']:
            return
        state['search'] = term
        await reload()

    async def apply_kind(value: str) -> None:
        # O tipo é filtrado no banco, então trocar a opção exige nova consulta.
        escolhido = str(value or KIND_ANY)
        if escolhido == state['kind']:
            return
        state['kind'] = escolhido
        await reload()

    def toggle_dismissed(value: bool) -> None:
        # As excluidas ja estao na lista carregada; basta trocar o filtro local.
        state['show_dismissed'] = bool(value)
        occurrence_list.refresh()

    # Um unico dialogo reaproveitado; recriar a cada clique deixaria copias no DOM.
    pending: dict[str, OccurrenceSummary | None] = {'occurrence': None}
    with ui.dialog() as confirm_dialog, ui.card().classes('w-full max-w-lg p-5 gap-3'):
        ui.label('Não lançar novidade?').classes('text-xl font-bold')
        confirm_detail = ui.label()
        confirm_reason = ui.input('Motivo (opcional)').classes('w-full')
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('Cancelar', on_click=confirm_dialog.close).props('flat')
            ui.button('Confirmar exclusão', icon='delete', on_click=dismiss).props(
                'color=negative'
            ).mark('confirmar-exclusao')

    ui.add_head_html(SEGMENTED_CSS)
    with ui.card().classes('simple-card w-full p-5 gap-3'):
        with ui.row().classes('w-full items-center justify-between no-wrap gap-3'):
            with ui.column().classes('gap-0'):
                ui.label('Ocorrências').classes('text-2xl font-bold')
                ui.label(
                    'Apenas as que ainda não viraram novidade.'
                ).classes('muted text-sm')

            with ui.row().classes('items-center gap-2 no-wrap'):
                search = ui.input(
                    placeholder='Buscar por número ou texto',
                    on_change=lambda event: apply_search(event.value),
                ).props('outlined dense clearable debounce=500 style="min-width: 20rem"')
                search.props('prepend-icon=search')
                search.mark('busca-ocorrencia')
                ui.button(icon='refresh', on_click=reload).props('flat color=primary').tooltip(
                    'Recarregar do banco'
                )
        with ui.row().classes('w-full items-center gap-4 no-wrap'):
            # "color" pinta os botoes nao selecionados e "toggle-color" o ativo.
            ui.toggle(
                KIND_FILTER_LABELS, value=KIND_ANY,
                on_change=lambda event: apply_kind(event.value),
            ).props(
                'no-caps unelevated dense rounded text-color=grey-8 '
                'toggle-color=primary toggle-text-color=white'
            ).classes('emc-segmented').mark('filtro-tipo-ocorrencia')
            ui.space()
            ui.switch(
                'Ver excluídas', on_change=lambda event: toggle_dismissed(event.value),
            ).props('dense')
        occurrence_list()
        # A primeira carga acontece depois que a pagina existe, para nao atrasar o render.
        ui.timer(0.1, reload, once=True)


__all__ = ['render_occurrence_menu']
