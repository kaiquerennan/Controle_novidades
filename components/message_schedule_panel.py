from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from nicegui import run, ui

from components.whatsapp_group_picker import build_group_picker
from services.message_schedule_service import (
    CATCH_UP_MINUTES,
    MESSAGE_TYPES,
    WEEKDAY_LABELS,
    MessageScheduleService,
    ScheduledMessage,
    parse_time_of_day,
    weekdays_label,
)
from services.whatsapp_gateway_service import (
    WhatsAppGatewayError,
    WhatsAppGatewayService,
    WhatsAppGroup,
)


STATUS_POLL_SECONDS = 30.0

DAY_NAMES = ('segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo')


def _moment_label(iso: str) -> str:
    try:
        momento = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ''
    return momento.strftime('%d/%m/%Y às %H:%M')


def _next_run_label(agendamento: ScheduledMessage) -> str:
    agora = datetime.now()
    proximo = agendamento.next_run(agora)
    if proximo is None:
        return ''
    if proximo.date() == agora.date():
        return f'hoje às {proximo.strftime("%H:%M")}'
    return f'{DAY_NAMES[proximo.weekday()]}, {proximo.strftime("%d/%m às %H:%M")}'


def render_message_schedules(
    *,
    gateway: WhatsAppGatewayService | None = None,
    schedule_service: MessageScheduleService | None = None,
) -> Callable[[], Any]:
    """Tela de agendamento das quatro mensagens de divulgação.

    Uma seção por tipo de mensagem, com horário, grupo de destino e um texto
    para cada dia da semana. O que a tela muda é gravado na hora: não há botão
    de salvar, e o interruptor de cada seção é o que decide se a mensagem sai.

    Devolve a função que relê tudo, para o menu chamar quando a tela abre.
    """
    service = schedule_service or MessageScheduleService()
    whatsapp = gateway or WhatsAppGatewayService()
    estado: dict[str, Any] = {
        'agendamentos': service.load(),
        'status': None,
        'enviando': '',
        # O dia aberto em cada seção; começa em hoje.
        'dia': {tipo.key: datetime.now().weekday() for tipo in MESSAGE_TYPES},
    }
    # Dois refreshables por seção: o quadro inteiro, quando um clique muda os
    # campos, e só o rodapé, quando a digitação muda o que ele explica. Redesenhar
    # o quadro a cada pausa da digitação tiraria o cursor do campo de texto.
    secoes: dict[str, Any] = {}
    rodapes: dict[str, Any] = {}

    def atual(type_key: str) -> ScheduledMessage:
        return estado['agendamentos'][type_key]

    def alterar(
        type_key: str, mudanca: Callable[[ScheduledMessage], Any],
        *, redesenhar: bool = True,
    ) -> None:
        service.change(type_key, mudanca)
        estado['agendamentos'] = service.load()
        if redesenhar:
            secoes[type_key].refresh()
        else:
            rodapes[type_key].refresh()

    async def atualizar_status() -> None:
        estado['status'] = await run.io_bound(whatsapp.status)
        cabecalho.refresh()

    async def recarregar() -> None:
        estado['agendamentos'] = service.load()
        for refrescar in secoes.values():
            refrescar.refresh()
        await atualizar_status()

    @ui.refreshable
    def cabecalho() -> None:
        situacao = estado['status']
        if situacao is None:
            with ui.row().classes('items-center gap-2'):
                ui.spinner(size='sm')
                ui.label('Verificando o WhatsApp...').classes('muted text-sm')
            return
        if situacao.connected:
            vinculo = f' ({situacao.phone})' if situacao.phone else ''
            with ui.row().classes('items-center gap-2 no-wrap'):
                ui.icon('check_circle').classes('text-green-600')
                ui.label(
                    f'WhatsApp conectado{vinculo}. Os agendamentos ativos são '
                    'enviados no horário.'
                ).classes('text-sm')
            return
        with ui.row().classes('w-full items-start gap-2 no-wrap'):
            ui.icon('warning_amber').classes('text-amber-600')
            ui.label(
                f'WhatsApp {situacao.label.lower()}. Sem a conta vinculada nada é '
                f'enviado: o horário vencido espera a conexão por até '
                f'{CATCH_UP_MINUTES} minutos. Vincule a conta no menu WhatsApp.'
            ).classes('text-amber-800 text-sm')

    def _secao(type_key: str) -> None:
        """Monta a seção de um tipo de mensagem, com seu próprio refresh."""

        def dia_aberto() -> int:
            return int(estado['dia'][type_key])

        def mudar_ativo(valor: Any) -> None:
            agendamento = atual(type_key)
            marcado = bool(valor)
            if marcado == agendamento.enabled:
                return
            if marcado and agendamento.issues:
                ui.notify(
                    'Complete o agendamento antes de ativar: falta '
                    + '; '.join(agendamento.issues) + '.',
                    type='warning',
                )
                secoes[type_key].refresh()
                return
            alterar(type_key, lambda item: setattr(item, 'enabled', marcado))
            ui.notify(
                f'{agendamento.type.label}: envio automático '
                + ('ativado.' if marcado else 'desativado.'),
                type='positive' if marcado else 'warning',
            )

        def mudar_horario(valor: Any) -> None:
            novo = str(valor or '').strip()
            if novo == atual(type_key).time_of_day:
                return
            # Horário pela metade durante a digitação não vira configuração.
            if parse_time_of_day(novo) is None:
                return
            alterar(
                type_key, lambda item: setattr(item, 'time_of_day', novo),
                redesenhar=False,
            )

        def mudar_repeticao(valor: Any) -> None:
            marcado = bool(valor)
            if marcado == atual(type_key).repeat_weekly:
                return
            alterar(
                type_key, lambda item: setattr(item, 'repeat_weekly', marcado),
                redesenhar=False,
            )

        def abrir_dia(dia: int) -> None:
            if dia == dia_aberto():
                return
            estado['dia'][type_key] = dia
            estado['agendamentos'] = service.load()
            secoes[type_key].refresh()

        def mudar_mensagem(dia: int, valor: Any) -> None:
            """Grava o texto do dia em que o campo foi desenhado.

            O dia vem amarrado ao campo, não ao que está aberto agora: a
            digitação chega com atraso, e trocar de aba nesse intervalo gravaria
            o texto de terça na quarta.
            """
            novo = str(valor or '')
            if novo == atual(type_key).message_for(dia):
                return
            alterar(
                type_key, lambda item: item.messages.__setitem__(dia, novo),
                redesenhar=False,
            )
            chips.refresh()

        def escolher_grupo(grupo: WhatsAppGroup) -> None:
            if not grupo.can_send:
                ui.notify(
                    f'Em "{grupo.name}" somente administradores enviam mensagens.',
                    type='warning',
                )
                return

            def aplicar(item: ScheduledMessage) -> None:
                item.group_id = grupo.id
                item.group_name = grupo.name

            alterar(type_key, aplicar)
            ui.notify(f'Destino: {grupo.name}.', type='positive')

        async def enviar_agora(dia: int) -> None:
            if estado['enviando']:
                return
            agendamento = atual(type_key)
            estado['enviando'] = type_key
            rodapes[type_key].refresh()
            enviado = False
            try:
                await run.io_bound(
                    lambda: whatsapp.send_to_group(
                        group_id=agendamento.group_id,
                        message=agendamento.compose(dia),
                    )
                )
            except WhatsAppGatewayError as exc:
                service.record_error(type_key, exc.user_message)
                ui.notify(exc.user_message, type='negative')
            else:
                enviado = True
                service.record_sent(type_key, dia)
                ui.notify(
                    f'{agendamento.type.label} de {DAY_NAMES[dia]} enviada para '
                    f'{agendamento.destination_label}.',
                    type='positive',
                )
            finally:
                estado['enviando'] = ''
                estado['agendamentos'] = service.load()
                if enviado:
                    # Sem repetição o texto enviado saiu da fila: o campo precisa
                    # ser redesenhado para não mostrar o que já não está gravado.
                    secoes[type_key].refresh()
                else:
                    rodapes[type_key].refresh()

        def confirmar_envio() -> None:
            """O envio manual é imediato e não tem volta: confirma antes."""
            agendamento = atual(type_key)
            dia = dia_aberto()
            faltas = [
                falta for falta in agendamento.issues
                if 'ao menos um dia' not in falta
            ]
            if not agendamento.message_for(dia).strip():
                faltas.append(f'escreva a mensagem de {DAY_NAMES[dia]}')
            if faltas:
                ui.notify(
                    'Antes de enviar, ' + '; '.join(faltas) + '.', type='warning',
                )
                return
            with ui.dialog() as confirmacao, ui.card().classes('p-5 gap-3 max-w-md'):
                ui.label('Enviar agora?').classes('text-lg font-bold')
                ui.label(
                    f'A mensagem de {DAY_NAMES[dia]} de {agendamento.type.label} '
                    f'vai para "{agendamento.destination_label}" neste momento.'
                    + (
                        '' if agendamento.repeat_weekly else
                        ' Depois de enviada ela sai da fila.'
                    )
                ).classes('text-sm muted')

                async def confirmar() -> None:
                    confirmacao.close()
                    await enviar_agora(dia)

                with ui.row().classes('w-full justify-end gap-2'):
                    ui.button('Cancelar', on_click=confirmacao.close).props('flat')
                    ui.button('Enviar', icon='send', on_click=confirmar).props(
                        'color=green'
                    ).mark(f'confirmar-envio-{type_key}')
            confirmacao.open()

        @ui.refreshable
        def chips() -> None:
            """Os sete dias: o aberto, os que têm mensagem e os vazios."""
            agendamento = atual(type_key)
            aberto = dia_aberto()
            with ui.row().classes('items-center gap-1 no-wrap'):
                for dia, rotulo in enumerate(WEEKDAY_LABELS):
                    com_texto = dia in agendamento.weekdays
                    if dia == aberto:
                        estilo = 'color=primary'
                    elif com_texto:
                        estilo = 'outline color=primary'
                    else:
                        estilo = 'outline color=grey-6'
                    botao = ui.button(
                        rotulo, on_click=lambda _=None, d=dia: abrir_dia(d),
                    ).props(f'dense no-caps unelevated {estilo}').mark(
                        f'dia-{type_key}-{dia}'
                    )
                    if com_texto:
                        botao.props('icon-right=check')
                    botao.tooltip(
                        f'{DAY_NAMES[dia].capitalize()}: '
                        + ('com mensagem' if com_texto else 'sem mensagem')
                    )

        @ui.refreshable
        def rodape() -> None:
            agendamento = atual(type_key)
            tipo = agendamento.type
            dia = dia_aberto()
            previa = agendamento.compose(dia)
            if previa:
                with ui.expansion(
                    f'Ver como a de {DAY_NAMES[dia]} chega no grupo',
                    icon='visibility',
                ).classes('w-full').props('dense'):
                    ui.label(previa).classes('text-sm whitespace-pre-wrap p-2')
                    ui.label(
                        f'A primeira linha é sempre "{tipo.header}", em negrito.'
                    ).classes('muted text-xs p-2')

            faltas = agendamento.issues
            if faltas:
                with ui.row().classes('items-start gap-2 no-wrap'):
                    ui.icon('info').classes('text-amber-600 text-sm')
                    ui.label('Falta ' + '; '.join(faltas) + '.').classes(
                        'text-amber-800 text-sm'
                    )
            else:
                dias = weekdays_label(agendamento.weekdays)
                if agendamento.enabled:
                    proximo = _next_run_label(agendamento)
                    with ui.row().classes('items-center gap-2 no-wrap'):
                        ui.icon('schedule').classes('text-green-700 text-sm')
                        ui.label(
                            f'Com mensagem: {dias}. '
                            + (f'Próximo envio {proximo}.' if proximo else '')
                        ).classes('text-green-800 text-sm')
                else:
                    ui.label(
                        f'Com mensagem: {dias}. Pronto para ativar: o envio '
                        'automático está desligado.'
                    ).classes('muted text-sm')

            if agendamento.last_sent_at:
                ui.label(
                    f'Último envio em {_moment_label(agendamento.last_sent_at)}.'
                ).classes('muted text-xs')
            if agendamento.last_error:
                with ui.row().classes('items-start gap-2 no-wrap'):
                    ui.icon('error_outline').classes('text-red-500 text-sm')
                    ui.label(
                        f'Falhou em {_moment_label(agendamento.last_error_at)}: '
                        f'{agendamento.last_error}'
                    ).classes('text-red-700 text-xs')

            with ui.row().classes('w-full items-center justify-end gap-2'):
                if estado['enviando'] == type_key:
                    ui.spinner(size='sm')
                ui.button(
                    f'Enviar a de {DAY_NAMES[dia]} agora', icon='send',
                    on_click=confirmar_envio,
                ).props('outline color=green no-caps').mark(
                    f'enviar-agora-{type_key}'
                ).tooltip('Manda neste momento a mensagem do dia aberto.')

        @ui.refreshable
        def secao() -> None:
            agendamento = atual(type_key)
            tipo = agendamento.type
            dia = dia_aberto()
            with ui.card().classes('simple-card w-full p-5 gap-3'):
                with ui.row().classes(
                    'w-full items-start justify-between no-wrap gap-3'
                ):
                    with ui.row().classes('items-start gap-2 no-wrap'):
                        ui.icon(tipo.icon, size='28px').classes(f'text-{tipo.color}')
                        with ui.column().classes('gap-0'):
                            ui.label(tipo.label).classes('text-xl font-bold')
                            ui.label(tipo.purpose).classes('muted text-sm')
                    ui.switch(
                        'Envio automático', value=agendamento.enabled,
                        on_change=lambda event: mudar_ativo(event.value),
                    ).props('dense').mark(f'ativo-{type_key}')

                with ui.row().classes('w-full items-center gap-3 no-wrap'):
                    ui.input(
                        'Horário', value=agendamento.time_of_day,
                        on_change=lambda event: mudar_horario(event.value),
                    ).props(
                        'outlined dense mask=##:## debounce=600 style="width: 7.5rem"'
                    ).mark(f'horario-{type_key}')
                    ui.icon('groups').classes('text-slate-500')
                    ui.label(f'Destino: {agendamento.destination_label}').classes(
                        'text-sm'
                    )
                    ui.button(
                        'Escolher grupo', icon='edit', on_click=abrir_grupos,
                    ).props('flat dense no-caps color=primary').mark(
                        f'escolher-grupo-{type_key}'
                    )
                    ui.space()
                    ui.switch(
                        'Repetir toda semana', value=agendamento.repeat_weekly,
                        on_change=lambda event: mudar_repeticao(event.value),
                    ).props('dense').mark(f'repetir-{type_key}').tooltip(
                        'Desligado, cada mensagem sai uma vez e deixa a fila. '
                        'Ligado, o texto de cada dia volta toda semana.'
                    )

                with ui.row().classes('w-full items-center gap-3 no-wrap'):
                    ui.label('Mensagem de').classes('text-sm')
                    chips()

                ui.textarea(
                    f'Mensagem de {DAY_NAMES[dia]}',
                    value=agendamento.message_for(dia),
                    on_change=lambda event, d=dia: mudar_mensagem(d, event.value),
                ).props('rows=5 outlined debounce=400').classes('w-full').mark(
                    f'mensagem-{type_key}'
                )
                rodapes[type_key] = rodape
                rodape()

        # O diálogo de grupos é montado uma vez por seção, fora do refreshable:
        # redesenhá-lo a cada mudança deixaria um diálogo órfão na página.
        abrir_grupos = build_group_picker(
            whatsapp, on_pick=escolher_grupo, mark=f'grupos-{type_key}',
        )
        secoes[type_key] = secao
        secao()

    with ui.card().classes('simple-card w-full p-5 gap-3'):
        with ui.row().classes('w-full items-start justify-between no-wrap gap-3'):
            with ui.column().classes('gap-0'):
                ui.label('Agendar mensagens').classes('text-2xl font-bold')
                ui.label(
                    'O horário, o grupo e a mensagem de cada dia da semana, por '
                    'tipo de mensagem. Dia sem texto não envia nada. O envio '
                    'acontece com o aplicativo aberto e a conta do WhatsApp '
                    'vinculada.'
                ).classes('muted text-sm')
            ui.button(icon='refresh', on_click=recarregar).props(
                'flat color=primary'
            ).tooltip('Atualizar')
        cabecalho()

    for tipo in MESSAGE_TYPES:
        _secao(tipo.key)

    ui.label(
        'Se o aplicativo estiver fechado na hora marcada, a mensagem sai quando '
        f'ele abrir, desde que isso aconteça em até {CATCH_UP_MINUTES} minutos do '
        'horário. Passada essa janela o dia é perdido, para nada chegar ao grupo '
        'fora de hora.'
    ).classes('muted text-xs')

    ui.timer(0.1, atualizar_status, once=True)
    ui.timer(STATUS_POLL_SECONDS, atualizar_status)
    return recarregar


__all__ = ['render_message_schedules']
