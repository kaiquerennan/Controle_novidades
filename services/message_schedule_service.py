"""Agendamento das mensagens de divulgação no WhatsApp.

Quatro tipos de mensagem combinados com o suporte, cada um com seu horário, seu
grupo e um texto para cada dia da semana. O agendamento é uma configuração local: nada vai
para o banco, e o envio só acontece enquanto o aplicativo está aberto e a conta
do WhatsApp está vinculada -- não existe serviço de fundo que mande mensagem com
o aplicativo fechado.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import re
import threading
from typing import Any, Callable

from services.image_service import DATA_ROOT, safe_path
from services.whatsapp_gateway_service import (
    GROUP_JID_PATTERN,
    WhatsAppGatewayError,
    WhatsAppGatewayService,
)


logger = logging.getLogger('EMC Novidade Visual IA')

CONFIG_VERSION = 1
DEFAULT_PATH = DATA_ROOT / 'whatsapp_agendamentos.local.json'

TIME_PATTERN = re.compile(r'([01]\d|2[0-3]):([0-5]\d)')

# Um horário perdido não é recuperado para sempre: se o aplicativo estava
# fechado na hora marcada, o envio acontece quando ele abrir, desde que ainda
# esteja dentro desta janela. Sem o limite, abrir o aplicativo à noite dispararia
# a mensagem da manhã, fora de hora e sem servir para nada.
CATCH_UP_MINUTES = 60

# Ordem de datetime.weekday(): segunda e 0, domingo e 6.
WEEKDAY_LABELS = ('seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom')
BUSINESS_DAYS = (0, 1, 2, 3, 4)
EVERY_DAY = (0, 1, 2, 3, 4, 5, 6)

# Intervalo de verificacao do despachante. O minuto e a menor unidade que a tela
# deixa escolher, entao conferir com essa folga nunca atrasa um envio.
CHECK_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class MessageType:
    """Um dos tipos de mensagem que o suporte divulga no WhatsApp."""

    key: str
    label: str
    header: str
    purpose: str
    icon: str
    color: str


# Os quatro tipos e o que cada um comunica, como ficou combinado. A novidade de
# sistema não entra aqui: ela já tem fluxo próprio, das ocorrências até o envio
# com o link do portal.
MESSAGE_TYPES: tuple[MessageType, ...] = (
    MessageType(
        key='voce_sabia',
        label='Você sabia?',
        header='VOCÊ SABIA?',
        purpose=(
            'Para apresentar ou relembrar funcionalidades que já existem nos '
            'sistemas.'
        ),
        icon='lightbulb',
        color='amber-8',
    ),
    MessageType(
        key='noticias',
        label='Notícias',
        header='NOTÍCIAS',
        purpose=(
            'Para divulgar notícias sobre legislação, tecnologia, mercado, '
            'obrigações fiscais e outros assuntos relevantes.'
        ),
        icon='newspaper',
        color='blue-8',
    ),
    MessageType(
        key='alerta_instabilidade',
        label='Alerta de instabilidade',
        header='ALERTA DE INSTABILIDADE',
        purpose=(
            'Para comunicar problemas ou indisponibilidades em serviços '
            'externos, como SEFAZ, bancos e APIs.'
        ),
        icon='warning',
        color='red-7',
    ),
    MessageType(
        key='comunicado',
        label='Comunicado',
        header='COMUNICADO',
        purpose=(
            'Para orientações gerais, mudanças de procedimentos, prazos e '
            'manutenções programadas.'
        ),
        icon='campaign',
        color='deep-purple-7',
    ),
)
MESSAGE_TYPES_BY_KEY = {tipo.key: tipo for tipo in MESSAGE_TYPES}


def parse_time_of_day(value: str) -> tuple[int, int] | None:
    """(hora, minuto) de um HH:MM válido, ou None."""
    casamento = TIME_PATTERN.fullmatch(str(value or '').strip())
    if casamento is None:
        return None
    return int(casamento.group(1)), int(casamento.group(2))


def normalize_weekdays(values: Any) -> tuple[int, ...]:
    dias: set[int] = set()
    for item in values or ():
        try:
            dia = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= dia <= 6:
            dias.add(dia)
    return tuple(sorted(dias))


def weekdays_label(dias: tuple[int, ...]) -> str:
    if not dias:
        return 'nenhum dia escolhido'
    if dias == EVERY_DAY:
        return 'todos os dias'
    if dias == BUSINESS_DAYS:
        return 'de segunda a sexta'
    return ', '.join(WEEKDAY_LABELS[dia] for dia in dias)


def normalize_day_messages(values: Any) -> dict[int, str]:
    """Texto por dia da semana, só com os dias que têm algo escrito."""
    mensagens: dict[int, str] = {}
    if not isinstance(values, dict):
        return mensagens
    for chave, texto in values.items():
        try:
            dia = int(chave)
        except (TypeError, ValueError):
            continue
        if 0 <= dia <= 6 and str(texto or '').strip():
            mensagens[dia] = str(texto)
    return dict(sorted(mensagens.items()))


@dataclass(slots=True)
class ScheduledMessage:
    """O agendamento de um tipo de mensagem.

    Cada dia da semana tem o seu texto, e é o texto que marca o dia: dia sem
    mensagem não envia nada. O horário e o grupo valem para a seção inteira.
    """

    type_key: str
    enabled: bool = False
    time_of_day: str = '09:00'
    messages: dict[int, str] = field(default_factory=dict)
    repeat_weekly: bool = False
    group_id: str = ''
    group_name: str = ''
    last_sent_at: str = ''
    last_sent_date: str = ''
    last_error: str = ''
    last_error_at: str = ''

    @property
    def type(self) -> MessageType:
        return MESSAGE_TYPES_BY_KEY[self.type_key]

    @property
    def weekdays(self) -> tuple[int, ...]:
        """Os dias que têm mensagem escrita."""
        return tuple(
            dia for dia in sorted(self.messages)
            if str(self.messages[dia] or '').strip()
        )

    def message_for(self, weekday: int) -> str:
        return str(self.messages.get(weekday) or '')

    @property
    def issues(self) -> list[str]:
        """O que falta para este agendamento poder enviar."""
        faltas: list[str] = []
        if parse_time_of_day(self.time_of_day) is None:
            faltas.append('informe o horário no formato HH:MM')
        if not GROUP_JID_PATTERN.fullmatch(str(self.group_id or '')):
            faltas.append('escolha o grupo ou a comunidade de destino')
        if not self.weekdays:
            faltas.append('escreva a mensagem de ao menos um dia')
        return faltas

    @property
    def ready(self) -> bool:
        return not self.issues

    @property
    def destination_label(self) -> str:
        return self.group_name or self.group_id or 'sem destino'

    def compose(self, weekday: int) -> str:
        """A mensagem do dia como ela chega no grupo, com o marcador do tipo.

        O marcador abre a mensagem para quem lê saber de que se trata antes da
        primeira linha, e é ele que diferencia um comunicado de um alerta na
        rolagem do grupo.
        """
        corpo = self.message_for(weekday).strip()
        if not corpo:
            return ''
        return f'*{self.type.header}*\n\n{corpo}'

    def is_due(self, moment: datetime) -> bool:
        """Se este agendamento deve enviar agora.

        Vence quando o dia de hoje tem mensagem, o horário já passou, nada foi
        enviado hoje e o atraso ainda cabe na janela de recuperação.
        """
        if not self.enabled or not self.ready:
            return False
        if moment.weekday() not in self.weekdays:
            return False
        if self.last_sent_date == moment.date().isoformat():
            return False
        horario = parse_time_of_day(self.time_of_day)
        if horario is None:
            return False
        marcado = moment.replace(
            hour=horario[0], minute=horario[1], second=0, microsecond=0,
        )
        if moment < marcado:
            return False
        return moment - marcado <= timedelta(minutes=CATCH_UP_MINUTES)

    def next_run(self, moment: datetime) -> datetime | None:
        """Próximo disparo previsto, para a tela dizer quando vai sair."""
        horario = parse_time_of_day(self.time_of_day)
        dias = self.weekdays
        if horario is None or not dias:
            return None
        for adiante in range(8):
            dia = (moment + timedelta(days=adiante)).replace(
                hour=horario[0], minute=horario[1], second=0, microsecond=0,
            )
            if dia.weekday() not in dias:
                continue
            if dia < moment:
                continue
            if dia.date().isoformat() == self.last_sent_date:
                continue
            return dia
        return None

    def to_payload(self) -> dict[str, Any]:
        return {
            'tipo': self.type_key,
            'ativo': bool(self.enabled),
            'horario': str(self.time_of_day or ''),
            'mensagens': {
                str(dia): texto
                for dia, texto in normalize_day_messages(self.messages).items()
            },
            'repetir': bool(self.repeat_weekly),
            'grupo_id': str(self.group_id or ''),
            'grupo_nome': str(self.group_name or ''),
            'enviado_em': str(self.last_sent_at or ''),
            'enviado_dia': str(self.last_sent_date or ''),
            'ultimo_erro': str(self.last_error or ''),
            'ultimo_erro_em': str(self.last_error_at or ''),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ScheduledMessage | None:
        chave = str(payload.get('tipo') or '').strip()
        if chave not in MESSAGE_TYPES_BY_KEY:
            return None
        mensagens = normalize_day_messages(payload.get('mensagens'))
        repetir = bool(payload.get('repetir'))
        texto_unico = str(payload.get('mensagem') or '')
        if not mensagens and texto_unico.strip():
            # Formato anterior: um texto só, repetido nos dias marcados.
            mensagens = {
                dia: texto_unico for dia in normalize_weekdays(payload.get('dias'))
            }
            repetir = True
        return cls(
            type_key=chave,
            enabled=bool(payload.get('ativo')),
            time_of_day=str(payload.get('horario') or '09:00'),
            messages=mensagens,
            repeat_weekly=repetir,
            group_id=str(payload.get('grupo_id') or ''),
            group_name=str(payload.get('grupo_nome') or ''),
            last_sent_at=str(payload.get('enviado_em') or ''),
            last_sent_date=str(payload.get('enviado_dia') or ''),
            last_error=str(payload.get('ultimo_erro') or ''),
            last_error_at=str(payload.get('ultimo_erro_em') or ''),
        )


def _default_schedule(type_key: str) -> ScheduledMessage:
    return ScheduledMessage(type_key=type_key)


class MessageScheduleService:
    """Leitura e gravação dos quatro agendamentos.

    O arquivo é a única fonte: a tela e o despachante leem dele a cada vez, em
    vez de guardar uma cópia na memória. São quatro registros pequenos, e assim
    duas abas abertas nunca mostram horários diferentes.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = safe_path(path or DEFAULT_PATH)
        self._lock = threading.Lock()

    def load(self) -> dict[str, ScheduledMessage]:
        """Os quatro agendamentos, sempre completos e na ordem dos tipos."""
        gravados = self._read_file()
        return {
            tipo.key: gravados.get(tipo.key) or _default_schedule(tipo.key)
            for tipo in MESSAGE_TYPES
        }

    def get(self, type_key: str) -> ScheduledMessage:
        return self.load()[type_key]

    def _read_file(self) -> dict[str, ScheduledMessage]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8-sig'))
        except (OSError, json.JSONDecodeError):
            logger.warning(
                '[AGENDAMENTO] Arquivo de agendamentos ilegível; usando os padrões.'
            )
            return {}
        if not isinstance(payload, dict) or payload.get('version') != CONFIG_VERSION:
            return {}
        agendamentos: dict[str, ScheduledMessage] = {}
        for item in payload.get('agendamentos') or []:
            if not isinstance(item, dict):
                continue
            agendamento = ScheduledMessage.from_payload(item)
            if agendamento is not None:
                agendamentos[agendamento.type_key] = agendamento
        return agendamentos

    def change(
        self, type_key: str, mutate: Callable[[ScheduledMessage], Any],
    ) -> ScheduledMessage:
        """Aplica uma alteração sobre o que está gravado agora.

        A tela nunca regrava o agendamento inteiro com a cópia que tem em mãos:
        o despachante pode ter consumido a mensagem de um dia nesse meio tempo, e
        a cópia antiga a traria de volta para ser enviada de novo. Cada mudança
        da tela é um campo, aplicado sobre a leitura mais recente do arquivo.
        """
        with self._lock:
            agendamentos = self.load()
            agendamento = agendamentos[type_key]
            mutate(agendamento)
            agendamento.messages = normalize_day_messages(agendamento.messages)
            self._write(agendamentos)
            return agendamento

    def due(self, moment: datetime | None = None) -> list[ScheduledMessage]:
        agora = moment or datetime.now()
        return [
            agendamento for agendamento in self.load().values()
            if agendamento.is_due(agora)
        ]

    def claim(
        self, type_key: str, moment: datetime | None = None,
    ) -> ScheduledMessage | None:
        """Reserva o envio do dia e devolve o que enviar, ou None.

        A marca de envio é gravada antes de a mensagem sair, não depois: se duas
        abas estiverem abertas, ou se a resposta do gateway demorar, só uma
        reserva vence e a mensagem não sai duas vezes no mesmo dia. O preço é
        que uma falha de rede consome o dia -- e é por isso que o erro fica
        registrado, o texto do dia é mantido e a tela oferece o envio manual.
        """
        agora = moment or datetime.now()
        with self._lock:
            agendamentos = self.load()
            agendamento = agendamentos.get(type_key)
            if agendamento is None or not agendamento.is_due(agora):
                return None
            agendamento.last_sent_at = agora.isoformat(timespec='seconds')
            agendamento.last_sent_date = agora.date().isoformat()
            agendamento.last_error = ''
            agendamento.last_error_at = ''
            self._write(agendamentos)
            return agendamento

    def record_error(self, type_key: str, message: str) -> None:
        agora = datetime.now()
        with self._lock:
            agendamentos = self.load()
            agendamento = agendamentos.get(type_key)
            if agendamento is None:
                return
            agendamento.last_error = str(message or '').strip()
            agendamento.last_error_at = agora.isoformat(timespec='seconds')
            self._write(agendamentos)

    def record_sent(
        self, type_key: str, weekday: int, moment: datetime | None = None,
    ) -> None:
        """Fecha um envio que deu certo, automático ou manual.

        Sem a repetição semanal, a mensagem enviada sai da fila: o texto de uma
        segunda é daquela segunda, e mantê-lo faria o mesmo aviso voltar ao
        grupo na semana seguinte sem ninguém pedir. O envio do dia só é dado
        por feito quando a mensagem enviada era a de hoje -- adiantar a de
        quarta numa segunda não cancela a de segunda.
        """
        agora = moment or datetime.now()
        with self._lock:
            agendamentos = self.load()
            agendamento = agendamentos.get(type_key)
            if agendamento is None:
                return
            agendamento.last_sent_at = agora.isoformat(timespec='seconds')
            if weekday == agora.weekday():
                agendamento.last_sent_date = agora.date().isoformat()
            agendamento.last_error = ''
            agendamento.last_error_at = ''
            if not agendamento.repeat_weekly:
                agendamento.messages.pop(weekday, None)
            self._write(agendamentos)

    def _write(self, agendamentos: dict[str, ScheduledMessage]) -> None:
        payload: dict[str, Any] = {
            'version': CONFIG_VERSION,
            'agendamentos': [
                agendamentos[tipo.key].to_payload()
                for tipo in MESSAGE_TYPES
                if tipo.key in agendamentos
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporario = safe_path(self.path.with_suffix('.tmp'))
        temporario.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        temporario.replace(self.path)


class MessageScheduleDispatcher:
    """Envia o que venceu, uma vez por ciclo de verificação.

    Roda em um timer do aplicativo, não da página: um timer por navegador aberto
    faria a mesma mensagem sair duas vezes. A reserva em ``claim()`` ainda
    protege o caso de duas instâncias do aplicativo sobre a mesma pasta de
    dados.
    """

    def __init__(
        self,
        *,
        schedules: MessageScheduleService | None = None,
        gateway: WhatsAppGatewayService | None = None,
    ) -> None:
        self.schedules = schedules or MessageScheduleService()
        self.gateway = gateway or WhatsAppGatewayService()

    def run_once(self, moment: datetime | None = None) -> list[str]:
        """Despacha os agendamentos vencidos; devolve os tipos que saíram."""
        agora = moment or datetime.now()
        pendentes = self.schedules.due(agora)
        if not pendentes:
            return []
        # O app já sobe o gateway ao iniciar, mas ele pode ter caído depois; sem
        # ele no ar a mensagem venceria em silêncio. A conexão leva alguns
        # segundos; o próximo ciclo envia.
        try:
            self.gateway.start_if_linked()
        except WhatsAppGatewayError as exc:
            logger.warning(
                '[AGENDAMENTO] Não foi possível iniciar o WhatsApp: %s',
                exc.user_message,
            )
            return []
        situacao = self.gateway.status()
        if not situacao.connected:
            # Sem conta vinculada não há como enviar, e consumir o dia aqui
            # perderia a mensagem por um problema que se resolve sozinho assim
            # que o WhatsApp reconectar, ainda dentro da janela.
            logger.info(
                '[AGENDAMENTO] %s mensagem(ns) vencida(s), mas o WhatsApp está %s.',
                len(pendentes), situacao.label,
            )
            return []
        enviados: list[str] = []
        hoje = agora.weekday()
        for pendente in pendentes:
            reservado = self.schedules.claim(pendente.type_key, agora)
            if reservado is None:
                continue
            try:
                identificador = self.gateway.send_to_group(
                    group_id=reservado.group_id, message=reservado.compose(hoje),
                )
            except WhatsAppGatewayError as exc:
                self.schedules.record_error(reservado.type_key, exc.user_message)
                logger.warning(
                    '[AGENDAMENTO] Falha ao enviar %s para %s: %s',
                    reservado.type.label, reservado.group_id, exc.user_message,
                )
                continue
            self.schedules.record_sent(reservado.type_key, hoje, agora)
            enviados.append(reservado.type_key)
            logger.info(
                '[AGENDAMENTO] %s enviada; grupo=%s horario=%s id=%s',
                reservado.type.label, reservado.group_id, reservado.time_of_day,
                identificador or 'nao informado',
            )
        return enviados

    async def tick(self) -> list[str]:
        """Versão para o timer: o HTTP do gateway sai do laço de eventos."""
        try:
            return await asyncio.to_thread(self.run_once)
        except Exception:  # pragma: no cover - o timer nao pode morrer
            logger.exception('[AGENDAMENTO] Verificação dos agendamentos falhou.')
            return []


__all__ = [
    'BUSINESS_DAYS',
    'CATCH_UP_MINUTES',
    'CHECK_INTERVAL_SECONDS',
    'CONFIG_VERSION',
    'DEFAULT_PATH',
    'EVERY_DAY',
    'MESSAGE_TYPES',
    'MESSAGE_TYPES_BY_KEY',
    'MessageScheduleDispatcher',
    'MessageScheduleService',
    'MessageType',
    'ScheduledMessage',
    'WEEKDAY_LABELS',
    'normalize_day_messages',
    'normalize_weekdays',
    'parse_time_of_day',
    'weekdays_label',
]
