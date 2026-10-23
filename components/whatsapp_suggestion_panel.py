from __future__ import annotations

from typing import Any, Callable

from nicegui import run, ui

from components.date_field import render_date_field
from components.occurrence_menu import KIND_BADGE_COLORS, SEGMENTED_CSS
from components.whatsapp_button import render_whatsapp_button
from services.occurrence_query_service import (
    OccurrenceQueryService,
    parse_filter_date,
)
from services.whatsapp_suggestion_service import (
    DEFAULT_MONTHS,
    ORIGIN_ANY,
    ORIGIN_LABELS,
    ORIGIN_PENDING,
    ORIGIN_PUBLISHED,
    Suggestion,
    SuggestionBoard,
    WhatsAppSuggestionService,
)


PRIORITY_COLORS = {'alta': 'green-7', 'média': 'amber-8', 'baixa': 'blue-grey'}


def render_whatsapp_suggestions(
    *,
    on_send: Callable[[Suggestion], Any],
    on_launch: Callable[[Suggestion], Any],
    suggestion_service: WhatsAppSuggestionService | None = None,
    catalog_service: OccurrenceQueryService | None = None,
) -> Callable[[], Any]:
    """Tela de sugestoes de divulgacao.

    Duas filas, uma embaixo da outra: a novidade que ja esta publicada, pronta
    para enviar com o link do portal, e a ocorrencia que ainda nao virou materia
    e precisa ser escrita antes. Cada card mostra por que esta ali.

    Devolve a funcao que carrega a tela. A carga nao acontece no desenho da
    pagina: sao duas consultas pesadas, e cobrar isso de quem nunca abre esta
    tela atrasaria a abertura do aplicativo inteiro.
    """
    suggestions = suggestion_service or WhatsAppSuggestionService()
    # O catálogo de sistemas/módulos é o mesmo das outras telas.
    catalog = catalog_service or OccurrenceQueryService(database=suggestions.database)
    state: dict[str, Any] = {
        'origin': ORIGIN_ANY,
        'start_date': '', 'end_date': '',
        'system': '', 'module': '', 'catalog': {},
        'search': '',
        'only_sent': False,
        'loading': False, 'loaded': False, 'error': '',
        'board': SuggestionBoard(published=[], pending=[]),
    }

    def load_board() -> SuggestionBoard:
        return suggestions.suggest(
            origin=str(state['origin']),
            months=DEFAULT_MONTHS,
            start_date=state['start_date'] or None,
            end_date=state['end_date'] or None,
            system=str(state['system']), module=str(state['module']),
            search=str(state['search']),
            only_sent=bool(state['only_sent']),
        )

    async def reload() -> None:
        state['loading'] = True
        suggestion_list.refresh()
        try:
            if not state['catalog']:
                state['catalog'] = await run.io_bound(
                    catalog.list_systems_and_modules
                )
                _fill_system_options()
            state['board'] = await run.io_bound(load_board)
            state['error'] = ''
        except Exception as exc:  # pragma: no cover - falha inesperada do driver
            state['board'] = SuggestionBoard(published=[], pending=[])
            state['error'] = f'Não foi possível montar as sugestões: {exc}'
        state['loading'] = False
        state['loaded'] = True
        suggestion_list.refresh()

    async def activate() -> None:
        """Primeira abertura da tela; depois disso o botão de recarregar manda."""
        if state['loaded'] or state['loading']:
            return
        await reload()

    def _fill_system_options() -> None:
        select_sistema.options = sorted(state['catalog'])
        select_sistema.update()
        _fill_module_options()

    def _fill_module_options() -> None:
        """Sem sistema escolhido, oferece os módulos de todos eles."""
        sistema = str(state['system'])
        if sistema:
            modulos = sorted(state['catalog'].get(sistema) or [])
        else:
            modulos = sorted(
                {modulo for lista in state['catalog'].values() for modulo in lista}
            )
        select_modulo.options = modulos
        select_modulo.update()

    async def apply_system(value: Any) -> None:
        novo = str(value or '')
        if novo == state['system']:
            return
        state['system'] = novo
        # O módulo escolhido pode não pertencer ao novo sistema; cruzar os dois
        # incompatíveis não traria linha alguma.
        if state['module'] and state['module'] not in (state['catalog'].get(novo) or []):
            state['module'] = ''
            select_modulo.value = None
        _fill_module_options()
        await reload()

    async def apply_module(value: Any) -> None:
        novo = str(value or '')
        if novo == state['module']:
            return
        state['module'] = novo
        await reload()

    async def apply_origin(value: Any) -> None:
        escolhido = str(value or ORIGIN_ANY)
        if escolhido == state['origin']:
            return
        state['origin'] = escolhido
        await reload()

    async def apply_only_sent(value: Any) -> None:
        marcado = bool(value)
        if marcado == state['only_sent']:
            return
        state['only_sent'] = marcado
        await reload()

    def mark_sent(item: Suggestion) -> None:
        """Marca como divulgada e tira da fila, sem ir ao banco de novo.

        Nada é enviado aqui: a marca é do usuário, depois que ele mesmo mandou a
        mensagem. Recarregar custaria segundos e a lista só precisa perder uma
        linha.
        """
        suggestions.dispatch.mark(item.identifier, item.title)
        _remove_from_board(item)
        ui.notify('Marcada como já enviada.', type='positive')

    def unmark_sent(item: Suggestion) -> None:
        suggestions.dispatch.unmark(item.identifier)
        _remove_from_board(item)
        ui.notify('Voltou para a fila de sugestões.', type='warning')

    def _remove_from_board(item: Suggestion) -> None:
        board: SuggestionBoard = state['board']
        board.published = [
            outra for outra in board.published if outra.identifier != item.identifier
        ]
        board.pending = [
            outra for outra in board.pending if outra.identifier != item.identifier
        ]
        suggestion_list.refresh()

    async def apply_search(value: str) -> None:
        termo = str(value or '').strip()
        if termo == state['search']:
            return
        state['search'] = termo
        await reload()

    async def apply_dates(campo: str, value: str) -> None:
        novo = str(value or '').strip()
        if novo == state[campo]:
            return
        # Data pela metade durante a digitação não vira consulta.
        if novo and parse_filter_date(novo) is None:
            return
        state[campo] = novo
        inicio = parse_filter_date(state['start_date'])
        fim = parse_filter_date(state['end_date'])
        if inicio and fim and inicio > fim:
            ui.notify('A data inicial não pode ser maior que a final.', type='warning')
            return
        await reload()

    async def clear_dates() -> None:
        if not state['start_date'] and not state['end_date']:
            return
        state['start_date'] = ''
        state['end_date'] = ''
        campo_inicio.value = ''
        campo_fim.value = ''
        await reload()

    @ui.refreshable
    def suggestion_list() -> None:
        if state['loading']:
            with ui.row().classes('w-full items-center gap-3 p-4'):
                ui.spinner(size='lg')
                ui.label('Procurando o que vale divulgar...').classes('muted')
            return
        if not state['loaded']:
            return
        if state['error']:
            with ui.row().classes('w-full items-center gap-2 p-4'):
                ui.icon('error_outline').classes('text-red-500')
                ui.label(state['error']).classes('text-red-600')
            return

        board: SuggestionBoard = state['board']
        if board.warning:
            with ui.row().classes('w-full items-start gap-2 p-3'):
                ui.icon('warning_amber').classes('text-amber-600')
                ui.label(board.warning).classes('text-amber-800 text-sm')
        if not board.total:
            ui.label(
                'Nenhuma marcada como enviada neste filtro.'
                if state['only_sent']
                else 'Nada a sugerir para este filtro. '
                'Amplie o período ou limpe o sistema.'
            ).classes('muted p-4')
            return

        if state['origin'] in (ORIGIN_ANY, ORIGIN_PUBLISHED):
            _section(
                'Prontas para enviar',
                'Já estão no portal: a mensagem vai com o título e o link.',
                'send', 'text-green-700', board.published,
            )
        if state['origin'] in (ORIGIN_ANY, ORIGIN_PENDING):
            _section(
                'Ainda não lançadas',
                'Têm material para render matéria, mas precisam ser escritas antes.',
                'edit_note', 'text-amber-700', board.pending,
            )
        if not state['only_sent']:
            ui.label(
                'O botão de visto tira a sugestão da fila; ele não envia nada. '
                'Para rever o que saiu, use "Ver já enviadas".'
            ).classes('muted text-xs pt-2')

    def _section(
        titulo: str, explicacao: str, icone: str, cor: str,
        itens: list[Suggestion],
    ) -> None:
        with ui.column().classes('w-full gap-2'):
            with ui.row().classes('w-full items-center gap-2 no-wrap pt-2'):
                ui.icon(icone).classes(cor)
                ui.label(titulo).classes('text-lg font-bold')
                ui.badge(str(len(itens))).props('color=blue-grey outline')
            ui.label(explicacao).classes('muted text-sm')
            if not itens:
                ui.label('Nenhuma nesta faixa de datas.').classes('muted text-sm pb-2')
                return
            for item in itens:
                _suggestion_card(item)

    def _sent_button(item: Suggestion) -> None:
        """A marca é manual: o aplicativo não sabe se a mensagem foi mesmo enviada."""
        ui.button(
            icon='check_circle',
            on_click=lambda _=None, alvo=item: mark_sent(alvo),
        ).props('flat round dense color=grey-7').mark(
            f'marcar-{item.identifier}'
        ).tooltip('Já enviei: tirar das sugestões')

    def _suggestion_card(item: Suggestion) -> None:
        with ui.card().classes('simple-card w-full p-4 gap-2'):
            with ui.row().classes('w-full items-start justify-between no-wrap gap-3'):
                with ui.column().classes('gap-1'):
                    ui.label(item.title).classes('text-base font-bold')
                    with ui.row().classes('items-center gap-2'):
                        ui.badge(f'prioridade {item.priority_label}').props(
                            f'color={PRIORITY_COLORS.get(item.priority_label, "grey")}'
                        ).tooltip(f'Escore {item.score} de 100')
                        if item.published:
                            ui.badge(f'ITN {item.code}').props('color=deep-purple')
                        if item.occurrence_number:
                            ui.badge(f'OC #{item.occurrence_number}').props(
                                'color=blue-grey outline'
                            )
                        ui.label(item.date_label).classes('muted text-sm')
                        if item.kind_label:
                            ui.badge(item.kind_label).props(
                                f'color={KIND_BADGE_COLORS.get(item.kind_label, "grey")}'
                            )
                        if item.system:
                            ui.badge(item.system).props('color=blue-grey outline')
                        if item.module:
                            ui.badge(item.module).props('color=indigo outline')
                with ui.row().classes('items-center gap-2 no-wrap'):
                    if state['only_sent']:
                        ui.button(
                            'Não enviei', icon='undo',
                            on_click=lambda _=None, alvo=item: unmark_sent(alvo),
                        ).props('flat color=primary').mark(
                            f'desmarcar-{item.identifier}'
                        ).tooltip('Devolver para a fila de sugestões')
                    elif item.published:
                        render_whatsapp_button(
                            lambda _=None, alvo=item: on_send(alvo), label='Enviar',
                        ).mark(f'enviar-{item.identifier}')
                        if item.public_url:
                            ui.link(
                                'Ver no portal', item.public_url, new_tab=True,
                            ).classes('text-sm')
                        _sent_button(item)
                    else:
                        ui.button(
                            'Lançar novidade', icon='note_add',
                            on_click=lambda _=None, alvo=item: on_launch(alvo),
                        ).props('color=primary').mark(f'lancar-{item.identifier}')
                        render_whatsapp_button(
                            lambda _=None, alvo=item: on_send(alvo),
                        ).mark(f'enviar-{item.identifier}').tooltip(
                            'Mandar um aviso agora, sem a novidade publicada'
                        )
                        _sent_button(item)
            if item.sent_at:
                with ui.row().classes('items-center gap-1 no-wrap'):
                    ui.icon('check_circle').classes('text-green-700 text-sm')
                    ui.label(f'Enviada em {item.sent_at}').classes(
                        'text-green-800 text-xs'
                    )
            if item.excerpt:
                ui.label(item.excerpt).classes('text-sm whitespace-pre-wrap')
            ui.label(item.reasons_label).classes('muted text-xs')
            for parecida in item.similar[:3]:
                with ui.row().classes('items-center gap-2 no-wrap'):
                    ui.icon('content_copy').classes('text-amber-700 text-sm')
                    ui.label(
                        f'{parecida.score_label} parecida com a ITN {parecida.code}'
                        f' ({parecida.date_label}, {parecida.link_label}): '
                        f'{parecida.title}'
                    ).classes('text-amber-800 text-xs')

    # O menu de ocorrências injeta o mesmo estilo, mas esta tela não pode
    # depender de ter sido desenhada depois dele.
    ui.add_head_html(SEGMENTED_CSS)
    with ui.card().classes('simple-card w-full p-5 gap-3'):
        with ui.row().classes('w-full items-center justify-between no-wrap gap-3'):
            with ui.column().classes('gap-0'):
                ui.label('Sugestões para o WhatsApp').classes('text-2xl font-bold')
                ui.label(
                    'O que vale divulgar agora, do mais recente para trás: o que '
                    'já está publicado e o que ainda não foi lançado nos últimos '
                    f'{DEFAULT_MONTHS} meses.'
                ).classes('muted text-sm')
            with ui.row().classes('items-center gap-2 no-wrap'):
                busca = ui.input(
                    placeholder='Buscar por número ou texto',
                    on_change=lambda event: apply_search(event.value),
                ).props('outlined dense clearable debounce=500 style="min-width: 18rem"')
                busca.props('prepend-icon=search')
                busca.mark('busca-sugestao')
                ui.button(icon='refresh', on_click=reload).props(
                    'flat color=primary'
                ).tooltip('Recarregar')
        with ui.row().classes('w-full items-center gap-2 no-wrap'):
            campo_inicio = render_date_field(
                'De', lambda valor: apply_dates('start_date', valor),
                mark='data-inicio-sugestao',
            )
            campo_fim = render_date_field(
                'Até', lambda valor: apply_dates('end_date', valor),
                mark='data-fim-sugestao',
            )
            ui.button(icon='backspace', on_click=clear_dates).props(
                'flat dense color=grey-7'
            ).tooltip(f'Limpar as datas e voltar aos últimos {DEFAULT_MONTHS} meses')
            ui.space()
            select_sistema = ui.select(
                [], label='Sistema', with_input=True, clearable=True,
                on_change=lambda event: apply_system(event.value),
            ).props('outlined dense options-dense style="min-width: 15rem"').mark(
                'filtro-sistema-sugestao'
            )
            select_modulo = ui.select(
                [], label='Módulo', with_input=True, clearable=True,
                on_change=lambda event: apply_module(event.value),
            ).props('outlined dense options-dense style="min-width: 17rem"').mark(
                'filtro-modulo-sugestao'
            )
        with ui.row().classes('w-full items-center gap-2 no-wrap'):
            # Mesmo controle segmentado das outras telas.
            ui.toggle(
                ORIGIN_LABELS, value=ORIGIN_ANY,
                on_change=lambda event: apply_origin(event.value),
            ).props(
                'no-caps unelevated dense rounded text-color=grey-8 '
                'toggle-color=primary toggle-text-color=white'
            ).classes('emc-segmented').mark('filtro-origem-sugestao')
            ui.space()
            ui.switch(
                'Ver já enviadas',
                on_change=lambda event: apply_only_sent(event.value),
            ).props('dense').mark('filtro-enviadas-sugestao').tooltip(
                'Mostra o que foi marcado como já divulgado.'
            )
        suggestion_list()
    return activate


__all__ = ['PRIORITY_COLORS', 'render_whatsapp_suggestions']
