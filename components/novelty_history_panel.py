from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from nicegui import app as ui_app, run, ui

from components.date_field import render_date_field
from components.novelty_preview import render_novelty_preview
from services.launched_novelty_query_service import LaunchedNoveltyQueryService
from services.novelty_history_service import LaunchedNovelty, NoveltyHistoryService
from services.occurrence_query_service import parse_filter_date


ORIGIN_LABELS = {'aplicativo': 'Gerada aqui', 'cadastro': 'Publicada'}
ORIGIN_COLORS = {'aplicativo': 'primary', 'cadastro': 'deep-purple'}

WHATSAPP_LOGO_PATH = Path(__file__).resolve().parents[1] / 'WhatsApp_Logo.png'
# A rota e registrada uma vez, no import. Passar o caminho local direto para o
# ui.image faria o NiceGUI criar uma rota nova a cada card e a cada sessao.
WHATSAPP_LOGO_URL = (
    ui_app.add_static_file(local_file=WHATSAPP_LOGO_PATH)
    if WHATSAPP_LOGO_PATH.is_file()
    else ''
)


def _whatsapp_button(on_click: Callable[[], Any], *, label: str = '') -> Any:
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


def render_novelty_history(
    *,
    history_service: NoveltyHistoryService | None = None,
    novelty_query_service: LaunchedNoveltyQueryService | None = None,
    on_send_whatsapp: Callable[[LaunchedNovelty], Any] | None = None,
    on_edit: Callable[[LaunchedNovelty], Any] | None = None,
) -> Callable[[], None]:
    """Lista as novidades publicadas no cadastro e as exportadas por este aplicativo.

    Devolve a função que recarrega a lista, para que o fluxo de criação possa
    atualizá-la assim que uma novidade for aprovada.
    """
    history = history_service or NoveltyHistoryService()
    queries = novelty_query_service or LaunchedNoveltyQueryService()
    state: dict[str, Any] = {
        'search': '', 'aberta': None,
        'start_date': '', 'end_date': '',
        'loading': True, 'published': [], 'aviso': '',
    }

    def load_published() -> list[LaunchedNovelty]:
        return queries.list_novelties(
            search=str(state['search']),
            start_date=state['start_date'] or None,
            end_date=state['end_date'] or None,
        )

    async def reload() -> None:
        """As publicadas vêm do banco; as locais são lidas do disco no render."""
        state['loading'] = True
        novelty_list.refresh()
        try:
            state['published'] = await run.io_bound(load_published)
            state['aviso'] = ''
        except Exception as exc:
            # O banco fora do ar não pode esconder o que já foi gerado aqui.
            state['published'] = []
            state['aviso'] = (
                f'Não foi possível ler as novidades publicadas: {exc} '
                'A lista abaixo mostra apenas as geradas neste aplicativo.'
            )
        state['loading'] = False
        novelty_list.refresh()

    def abrir(novelty: LaunchedNovelty) -> None:
        state['aberta'] = novelty
        # Os filtros vivem fora da parte refreshable, entao precisam ser
        # escondidos na mao: dentro da novidade aberta eles nao filtram nada.
        linha_filtros.set_visibility(False)
        novelty_list.refresh()

    def voltar() -> None:
        state['aberta'] = None
        linha_filtros.set_visibility(True)
        novelty_list.refresh()

    def _detalhe(novelty: LaunchedNovelty) -> None:
        if novelty.from_database:
            _detalhe_publicada(novelty)
            return
        documento, imagens = history.load_content(novelty)
        with ui.row().classes('w-full items-center justify-between no-wrap gap-3'):
            ui.button('Voltar', icon='arrow_back', on_click=voltar).props('flat')
            with ui.row().classes('items-center gap-2 no-wrap'):
                if on_edit is not None:
                    ui.button(
                        'Editar novidade', icon='edit',
                        on_click=lambda: on_edit(novelty),
                    ).props('outline color=primary').mark('editar-novidade')
                if on_send_whatsapp is not None:
                    _whatsapp_button(
                        lambda: on_send_whatsapp(novelty),
                        label='Enviar pelo WhatsApp',
                    ).mark('enviar-novidade')
                if novelty.html_path:
                    ui.button(
                        'HTML', icon='code',
                        on_click=lambda path=novelty.html_path: ui.download(str(path)),
                    ).props('flat color=primary')
                if novelty.zip_path:
                    ui.button(
                        'ZIP', icon='download',
                        on_click=lambda path=novelty.zip_path: ui.download(str(path)),
                    ).props('flat color=primary')
        with ui.row().classes('items-center gap-2'):
            if novelty.created_at_label:
                ui.label(novelty.created_at_label).classes('muted text-sm')
            ui.badge(f'{novelty.image_count} imagem(ns)').props('color=blue-grey outline')
        if not documento.blocks:
            ui.label(
                'Não foi possível reconstruir o conteúdo desta exportação. '
                'Os arquivos continuam disponíveis para download.'
            ).classes('muted p-4')
            return
        with ui.card().classes('simple-card publication w-full p-0'):
            # Somente leitura: a edição acontece no fluxo de criação.
            render_novelty_preview(documento, imagens)

    def _detalhe_publicada(novelty: LaunchedNovelty) -> None:
        """Novidade do cadastro: não há arquivos nem imagens, só título e texto."""
        with ui.row().classes('w-full items-center justify-between no-wrap gap-3'):
            ui.button('Voltar', icon='arrow_back', on_click=voltar).props('flat')
            if on_send_whatsapp is not None:
                _whatsapp_button(
                    lambda: on_send_whatsapp(novelty),
                    label='Enviar pelo WhatsApp',
                ).mark('enviar-novidade')
        with ui.row().classes('items-center gap-2'):
            if novelty.created_at_label:
                ui.label(novelty.created_at_label).classes('muted text-sm')
            if novelty.occurrence_number:
                ui.badge(f'Ocorrência #{novelty.occurrence_number}').props(
                    'color=blue-grey outline'
                )
        with ui.card().classes('simple-card publication w-full p-8 gap-4'):
            ui.label(novelty.display_title).classes('text-3xl font-bold leading-tight')
            if novelty.text:
                ui.label(novelty.text).classes(
                    'text-lg leading-8 whitespace-pre-wrap text-slate-700'
                )
            else:
                ui.label('Esta novidade não tem texto cadastrado.').classes('muted')

    def _combined() -> list[LaunchedNovelty]:
        """Junta as duas procedências e ordena pela data, sem data por último."""
        locais = _local_novelties()
        todas = [*state['published'], *locais]
        todas.sort(
            key=lambda item: (item.created_at is None, item.created_at),
            reverse=True,
        )
        return todas

    def _local_novelties() -> list[LaunchedNovelty]:
        """As geradas aqui: filtradas em memória, já que vêm do disco."""
        novelties = history.list_novelties(search=str(state['search']))
        inicio = parse_filter_date(state['start_date'])
        fim = parse_filter_date(state['end_date'])
        if inicio is None and fim is None:
            return novelties
        mantidas = []
        for item in novelties:
            if item.created_at is None:
                continue
            dia = item.created_at.date()
            if inicio is not None and dia < inicio:
                continue
            if fim is not None and dia > fim:
                continue
            mantidas.append(item)
        return mantidas

    @ui.refreshable
    def novelty_list() -> None:
        if state['aberta'] is not None:
            _detalhe(state['aberta'])
            return
        if state['loading']:
            with ui.row().classes('w-full items-center gap-3 p-4'):
                ui.spinner(size='lg')
                ui.label('Carregando novidades...').classes('muted')
            return
        if state['aviso']:
            with ui.row().classes('w-full items-start gap-2 p-3'):
                ui.icon('warning_amber').classes('text-amber-600')
                ui.label(state['aviso']).classes('text-amber-800 text-sm')
        novelties = _combined()
        publicadas = sum(1 for item in novelties if item.from_database)
        ui.label(
            f'{len(novelties)} novidade(s) · {publicadas} publicada(s) · '
            f'{len(novelties) - publicadas} gerada(s) aqui'
        ).classes('muted text-sm px-1')
        if not novelties:
            ui.label(
                'Nenhuma novidade para os filtros atuais.'
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
                        ui.badge(ORIGIN_LABELS.get(novelty.origin, '')).props(
                            f'color={ORIGIN_COLORS.get(novelty.origin, "grey")}'
                        )
                        if novelty.created_at_label:
                            ui.label(novelty.created_at_label).classes('muted text-sm')
                        if novelty.occurrence_number:
                            ui.badge(f'#{novelty.occurrence_number}').props(
                                'color=blue-grey outline'
                            )
                        if not novelty.from_database:
                            ui.badge(f'{novelty.image_count} imagem(ns)').props(
                                'color=blue-grey outline'
                            )
                            if novelty.has_cover:
                                ui.badge('com capa').props('color=indigo outline')
                with ui.row().classes('items-center gap-2 no-wrap'):
                    ui.button(
                        'Abrir', icon='open_in_new',
                        on_click=lambda _, item=novelty: abrir(item),
                    ).props('color=primary').mark(f'abrir-{novelty.identifier}')
                    if on_send_whatsapp is not None:
                        _whatsapp_button(
                            lambda _=None, item=novelty: on_send_whatsapp(item),
                        ).mark(f'whatsapp-{novelty.identifier}').tooltip(
                            'Enviar pelo WhatsApp'
                        )
                    if novelty.zip_path:
                        ui.button(
                            icon='download',
                            on_click=lambda _, path=novelty.zip_path: ui.download(str(path)),
                        ).props('flat color=primary').tooltip('Baixar o ZIP')
            if novelty.excerpt:
                ui.label(novelty.excerpt).classes('text-sm whitespace-pre-wrap')
            if not novelty.from_database:
                ui.label(f'Sessão {novelty.session_id}').classes('muted text-xs')

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
        # Data pela metade durante a digitação não vira consulta: sem isso, um
        # "05/08" recém-digitado limparia o filtro e recarregaria a lista inteira.
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

    with ui.card().classes('simple-card w-full p-5 gap-3'):
        with ui.row().classes('w-full items-center justify-between no-wrap gap-3'):
            with ui.column().classes('gap-0'):
                ui.label('Novidades lançadas').classes('text-2xl font-bold')
                ui.label(
                    'As publicadas no cadastro da EMC e as geradas por este aplicativo.'
                ).classes('muted text-sm')
            with ui.row().classes('items-center gap-2 no-wrap'):
                busca = ui.input(
                    placeholder='Buscar por título ou texto',
                    on_change=lambda event: apply_search(event.value),
                ).props('outlined dense clearable debounce=500 style="min-width: 20rem"')
                busca.props('prepend-icon=search')
                busca.mark('busca-novidade')
                ui.button(icon='refresh', on_click=reload).props(
                    'flat color=primary'
                ).tooltip('Recarregar')
        with ui.row().classes('w-full items-center gap-2 no-wrap') as linha_filtros:
            campo_inicio = render_date_field(
                'De', lambda valor: apply_dates('start_date', valor),
                mark='data-inicio-novidade',
            )
            campo_fim = render_date_field(
                'Até', lambda valor: apply_dates('end_date', valor),
                mark='data-fim-novidade',
            )
            ui.button(icon='backspace', on_click=clear_dates).props(
                'flat dense color=grey-7'
            ).tooltip('Limpar as datas')
        novelty_list()
        # A primeira carga acontece depois que a pagina existe, para nao atrasar o render.
        ui.timer(0.1, reload, once=True)
    return reload


__all__ = ['render_novelty_history']
