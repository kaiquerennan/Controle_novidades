from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from urllib import error, request

from services.image_service import DATA_ROOT, PROJECT_ROOT, safe_path


logger = logging.getLogger('EMC Novidade Visual IA')

GATEWAY_DIR = PROJECT_ROOT / 'whatsapp_gateway'
SESSION_DIR = DATA_ROOT / 'whatsapp_session'
DEFAULT_PORT = 8511
DEFAULT_TIMEOUT = 20.0
STARTUP_TIMEOUT = 40.0

GROUP_JID_PATTERN = re.compile(r'\d+(-\d+)?@g\.us')

CONNECTION_LABELS = {
    'conectado': 'Conectado',
    'aguardando_qr': 'Aguardando leitura do QR',
    'conectando': 'Conectando',
    'desconectado': 'Desconectado',
}


class WhatsAppGatewayError(RuntimeError):
    def __init__(self, user_message: str, *, technical_detail: str = '') -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.technical_detail = technical_detail


@dataclass(slots=True)
class GatewayStatus:
    running: bool
    connection: str = 'desconectado'
    connected: bool = False
    phone: str = ''
    has_qr: bool = False
    last_error: str = ''

    @property
    def label(self) -> str:
        if not self.running:
            return 'Serviço do WhatsApp parado'
        return CONNECTION_LABELS.get(self.connection, self.connection)


@dataclass(slots=True, frozen=True)
class WhatsAppGroup:
    """Grupo do qual a conta vinculada participa."""

    id: str
    name: str
    participants: int = 0
    only_admins: bool = False
    can_send: bool = True
    announcement: bool = False

    @property
    def summary(self) -> str:
        partes = [f'{self.participants} participante(s)'] if self.participants else []
        if self.announcement:
            # A comunidade e o grupo de avisos costumam ter o mesmo nome; sem
            # esta marca as duas linhas ficavam indistinguiveis na tela.
            partes.append('avisos da comunidade')
        if self.only_admins:
            partes.append(
                'somente administradores enviam'
                if not self.can_send else
                'somente administradores enviam (você é admin)'
            )
        return ' · '.join(partes)


def normalize_phone(value: str, *, default_country_code: str = '55') -> str:
    """Normaliza para o formato internacional, assumindo Brasil quando omitido."""
    bruto = str(value or '').strip()
    digitos = re.sub(r'\D+', '', bruto)
    if not digitos:
        raise WhatsAppGatewayError('Informe um numero de WhatsApp com DDD.')
    if bruto.startswith('+') or digitos.startswith(default_country_code):
        normalizado = digitos
    else:
        normalizado = f'{default_country_code}{digitos}'
    if not re.fullmatch(r'\d{12,15}', normalizado):
        raise WhatsAppGatewayError(
            'Informe um destinatario valido, com codigo do pais e DDD.'
        )
    return normalizado


def _delivery_label(resposta: dict) -> str:
    """Rotulo de entrega para o log.

    O gateway so devolve ``confirmado`` quando o servidor do WhatsApp deu o
    sinal dentro da espera. Em grupo o recibo pode demorar minutos, entao a
    ausencia de confirmacao nao e falha: uma recusa viraria erro na resposta.
    """
    return 'confirmada' if resposta.get('confirmado') else 'sem confirmacao'


def mask_phone(value: str) -> str:
    digitos = re.sub(r'\D+', '', str(value or ''))
    if len(digitos) <= 6:
        return '*' * len(digitos)
    return f'{digitos[:4]}{"*" * (len(digitos) - 6)}{digitos[-2:]}'


class WhatsAppGatewayService:
    """Cliente do gateway Node que conversa com o WhatsApp via Baileys.

    O Baileys e uma biblioteca Node, entao o vinculo da conta e o envio ficam em
    um processo separado, ouvindo apenas em 127.0.0.1. Esta classe sobe esse
    processo quando necessario e fala com ele por HTTP.
    """

    def __init__(self, port: int = DEFAULT_PORT, token: str = '') -> None:
        self.port = int(port)
        self.token = token or os.getenv('EMC_WHATSAPP_TOKEN', '')
        self.base_url = f'http://127.0.0.1:{self.port}'

    def is_listening(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            return sock.connect_ex(('127.0.0.1', self.port)) == 0

    def dependencies_installed(self) -> bool:
        return (GATEWAY_DIR / 'node_modules').is_dir()

    def has_linked_account(self) -> bool:
        """Se já houve leitura do QR: o Baileys grava creds.json ao vincular."""
        return (SESSION_DIR / 'creds.json').is_file()

    def start(self) -> None:
        """Sobe o gateway se ele nao estiver no ar."""
        if self.is_listening():
            return
        if not (GATEWAY_DIR / 'server.js').is_file():
            raise WhatsAppGatewayError(
                'O servico do WhatsApp nao foi encontrado em whatsapp_gateway.'
            )
        if not self.dependencies_installed():
            raise WhatsAppGatewayError(
                'As dependencias do WhatsApp nao estao instaladas. Execute '
                'instalar_whatsapp.bat.'
            )
        ambiente = os.environ.copy()
        ambiente['EMC_WHATSAPP_PORT'] = str(self.port)
        ambiente['EMC_WHATSAPP_AUTH_DIR'] = str(safe_path(SESSION_DIR))
        if self.token:
            ambiente['EMC_WHATSAPP_TOKEN'] = self.token
        criacao = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            subprocess.Popen(
                ['node', 'server.js'],
                cwd=str(GATEWAY_DIR),
                env=ambiente,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=criacao,
            )
        except FileNotFoundError as exc:
            raise WhatsAppGatewayError(
                'Node.js nao foi encontrado. Instale o Node.js para usar o WhatsApp.',
                technical_detail=str(exc),
            ) from exc
        limite = time.perf_counter() + STARTUP_TIMEOUT
        while time.perf_counter() < limite:
            if self.is_listening():
                logger.info('[WHATSAPP] Gateway iniciado na porta %s.', self.port)
                return
            time.sleep(0.5)
        raise WhatsAppGatewayError('O servico do WhatsApp nao respondeu a tempo.')

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        dados = json.dumps(payload).encode('utf-8') if payload is not None else None
        cabecalhos = {'Content-Type': 'application/json'}
        if self.token:
            cabecalhos['X-EMC-Token'] = self.token
        requisicao = request.Request(
            f'{self.base_url}{path}', data=dados, headers=cabecalhos, method=method,
        )
        try:
            with request.urlopen(requisicao, timeout=DEFAULT_TIMEOUT) as resposta:
                bruto = resposta.read().decode('utf-8')
                return json.loads(bruto) if bruto else {}
        except error.HTTPError as exc:
            corpo = exc.read().decode('utf-8', errors='replace')
            try:
                mensagem = str(json.loads(corpo).get('erro') or '').strip()
            except json.JSONDecodeError:
                mensagem = ''
            if not mensagem:
                # Resposta fora do padrao do gateway: a pagina de erro do Express e
                # HTML e nao diz nada ao usuario. Ela fica so no log, e a tela recebe
                # a orientacao que resolve o caso comum -- servico desatualizado.
                logger.warning(
                    '[WHATSAPP] Resposta %s em %s sem corpo JSON: %s',
                    exc.code, path, corpo[:200].replace('\n', ' '),
                )
                mensagem = (
                    f'O servico do WhatsApp respondeu {exc.code} em {path}. '
                    'Se o aplicativo foi atualizado, reinicie o servico na aba WhatsApp.'
                )
            raise WhatsAppGatewayError(mensagem, technical_detail=f'HTTP {exc.code}') from exc
        except (error.URLError, TimeoutError, socket.timeout) as exc:
            raise WhatsAppGatewayError(
                'Nao foi possivel falar com o servico do WhatsApp.',
                technical_detail=str(exc),
            ) from exc

    def status(self) -> GatewayStatus:
        if not self.is_listening():
            return GatewayStatus(running=False)
        try:
            dados = self._request('GET', '/status')
        except WhatsAppGatewayError as exc:
            return GatewayStatus(running=True, last_error=exc.user_message)
        return GatewayStatus(
            running=True,
            connection=str(dados.get('conexao') or 'desconectado'),
            connected=bool(dados.get('conectado')),
            phone=str(dados.get('telefone') or ''),
            has_qr=bool(dados.get('tem_qr')),
            last_error=str(dados.get('ultimo_erro') or ''),
        )

    def qr_code(self) -> str:
        """Data URL do QR atual, ou vazio quando nao ha QR pendente."""
        try:
            return str(self._request('GET', '/qr').get('qr') or '')
        except WhatsAppGatewayError:
            return ''

    def send(self, *, recipient: str, message: str) -> str:
        numero = normalize_phone(recipient)
        texto = str(message or '').strip()
        if not texto:
            raise WhatsAppGatewayError('A mensagem nao pode ficar vazia.')
        resposta = self._request('POST', '/send', {'to': numero, 'message': texto})
        identificador = str(resposta.get('id') or '')
        logger.info(
            '[WHATSAPP] Mensagem enviada; destinatario=%s id=%s entrega=%s',
            mask_phone(numero), identificador or 'nao informado',
            _delivery_label(resposta),
        )
        return identificador

    def list_groups(self, *, refresh: bool = False) -> list[WhatsAppGroup]:
        """Grupos dos quais a conta vinculada participa, em ordem alfabética.

        O gateway guarda a lista por um minuto; ``refresh`` força uma releitura.
        """
        caminho = '/groups?atualizar=1' if refresh else '/groups'
        dados = self._request('GET', caminho)
        grupos: list[WhatsAppGroup] = []
        for item in dados.get('grupos') or []:
            identificador = str(item.get('id') or '')
            if not identificador:
                continue
            grupos.append(WhatsAppGroup(
                id=identificador,
                name=str(item.get('nome') or identificador),
                participants=int(item.get('participantes') or 0),
                only_admins=bool(item.get('somente_admin')),
                can_send=bool(item.get('posso_enviar', True)),
                announcement=bool(item.get('avisos')),
            ))
        return grupos

    def send_to_group(self, *, group_id: str, message: str) -> str:
        identificador = str(group_id or '').strip()
        if not GROUP_JID_PATTERN.fullmatch(identificador):
            raise WhatsAppGatewayError('Selecione um grupo da lista para enviar.')
        texto = str(message or '').strip()
        if not texto:
            raise WhatsAppGatewayError('A mensagem nao pode ficar vazia.')
        resposta = self._request(
            'POST', '/send', {'group_id': identificador, 'message': texto},
        )
        registro = str(resposta.get('id') or '')
        logger.info(
            '[WHATSAPP] Mensagem enviada para grupo; grupo=%s id=%s entrega=%s',
            identificador, registro or 'nao informado',
            _delivery_label(resposta),
        )
        return registro

    def logout(self) -> None:
        self._request('POST', '/logout')
        logger.info('[WHATSAPP] Conta desconectada.')


__all__ = [
    'CONNECTION_LABELS',
    'DEFAULT_PORT',
    'GATEWAY_DIR',
    'GROUP_JID_PATTERN',
    'GatewayStatus',
    'SESSION_DIR',
    'WhatsAppGatewayError',
    'WhatsAppGatewayService',
    'WhatsAppGroup',
    'mask_phone',
    'normalize_phone',
]
