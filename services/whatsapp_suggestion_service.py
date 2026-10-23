from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
import re
from typing import Any

from services.launched_novelty_query_service import LaunchedNoveltyQueryService
from services.novelty_history_service import LaunchedNovelty
from services.novelty_similarity_service import (
    NoveltySimilarityService,
    SimilarNovelty,
)
from services.occurrence_database_service import (
    OccurrenceDatabase,
    OccurrenceDatabaseError,
)
from services.occurrence_dismissal_service import OccurrenceDismissalService
from services.suggestion_dispatch_service import SuggestionDispatchService
from services.occurrence_query_service import (
    OCCURRENCE_KINDS,
    OccurrenceQueryService,
    OccurrenceSummary,
    parse_filter_date,
)


logger = logging.getLogger('EMC Novidade Visual IA')


# A tela sugere o que vale comunicar, vindo de duas pontas: a novidade que ja
# esta publicada no portal (pronta para enviar, com link) e a ocorrencia que
# ainda nao virou materia (precisa ser escrita antes). As duas entram na mesma
# fila ordenada; o rotulo de cada card diz em que estado ela esta.
ORIGIN_PUBLISHED = 'publicada'
ORIGIN_PENDING = 'pendente'
ORIGIN_ANY = 'todas'
ORIGIN_LABELS = {
    ORIGIN_ANY: 'Todas',
    ORIGIN_PUBLISHED: 'Prontas para enviar',
    ORIGIN_PENDING: 'Ainda não lançadas',
}

DEFAULT_MONTHS = 6
# Quantas candidatas sao avaliadas antes de cortar a lista. O corte existe por
# causa da conferencia de semelhanca: ela custa alguns milissegundos por
# ocorrencia, o que seria segundos se rodasse nas quase mil do periodo.
CANDIDATE_LIMIT = 40
# Por procedencia, nao no total: as duas listas aparecem lado a lado na tela, e
# uma fila unica deixaria as pendentes fora da vista -- a publicada ja esta
# pronta para enviar e por isso pontua mais alto quase sempre.
MAX_SUGGESTIONS = 15

# Texto curto demais nao rende mensagem; longo demais nao cabe no WhatsApp sem
# cortar. Os dois extremos pesam no escore, nao eliminam.
MIN_TEXT_LENGTH = 200
SHORT_TEXT_LENGTH = 60
# Tamanho do contexto da ocorrencia: descricao, complemento e observacoes somados.
# Medido nas 960 ocorrencias de mudanca do semestre -- abaixo da faixa pobre a
# materia sai sem substancia. A conta e por soma de tamanhos, e nao pelo texto
# bruto montado, porque montar o texto das mil ocorrencias levava cinco segundos.
RICH_CONTEXT = 4000
POOR_CONTEXT = 1200
DELIVERY_LENGTH = 25
# Observacao com menos que isso e "ok", "segue anexo" e afins: nao e registro.
RECORD_LENGTH = 40

# Secoes que descrevem o que foi efetivamente entregue no chamado.
DELIVERY_SECTIONS = ('ATENDIMENTO', 'SOLUCAO')
# Valores que a equipe deixa no lugar de um atendimento em branco.
EMPTY_DELIVERY = frozenset({'#erro#', 'erro', 'n/a', 'na', '-', '.', ''})

SUBJECT_LENGTH = 120
EXCERPT_LENGTH = 320
# Linha de rotulo, do tipo "CAMINHO:" ou "SOLICITAÇÃO:". A segunda alternativa
# existe porque no cadastro de novidades os mesmos rotulos aparecem em caixa
# mista ("Caminho: Utilitários / ..."); o resto da linha, ali, e o menu.
_LABEL_LINE = re.compile(
    r'^[A-ZÀ-Ú][A-ZÀ-Ú0-9 ./_-]{2,40}:'
    r'|^(?i:caminho|solicita[çc][ãa]o|justificativa|atendimento)\s*:'
)

# Faixas do escore em palavras. Medidas no semestre: as publicadas do mes com
# texto ficam na casa dos 80, e o que cai abaixo de 65 tem sempre algo faltando
# -- texto curto, pouco contexto ou data antiga.
HIGH_PRIORITY = 78
MEDIUM_PRIORITY = 65


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _first_line(text: str, limit: int = SUBJECT_LENGTH) -> str:
    for linha in str(text or '').splitlines():
        limpa = linha.strip()
        if len(limpa) < 3:
            continue
        return limpa[:limit] + ('...' if len(limpa) > limit else '')
    return ''


def _preview(text: str, limit: int = EXCERPT_LENGTH) -> str:
    """Comeco do texto, em uma linha so.

    Boa parte das novidades do cadastro comeca pelo caminho do menu
    ("CAMINHO: GESTAO / CADASTROS / ..."), que nao resume nada: essas linhas
    saem. As demais sao emendadas, porque a conversao do HTML quebra a frase em
    varias linhas e mostrar so a primeira cortaria a frase no meio.
    """
    partes = [
        linha.strip()
        for linha in str(text or '').splitlines()
        if linha.strip() and not _LABEL_LINE.match(linha.strip())
    ]
    resumo = re.sub(r'\s+', ' ', ' '.join(partes)).strip()
    if not resumo:
        return _first_line(text, limit)
    return resumo[:limit] + ('...' if len(resumo) > limit else '')


@dataclass(slots=True)
class Suggestion:
    """Uma linha da tela de sugestoes, com a razao de estar ali."""

    origin: str
    title: str
    score: int = 0
    reasons: tuple[str, ...] = ()
    created_at: datetime | None = None
    system: str = ''
    module: str = ''
    kind: str = ''
    occurrence_number: int = 0
    code: int = 0
    excerpt: str = ''
    novelty: LaunchedNovelty | None = None
    occurrence: OccurrenceSummary | None = None
    # Data em que alguem marcou "já enviei"; vazia enquanto nao foi divulgada.
    sent_at: str = ''
    # Novidades parecidas ja publicadas. Nas pendentes e o alerta de materia
    # repetida; nas publicadas nao se aplica.
    similar: tuple[SimilarNovelty, ...] = ()

    @property
    def published(self) -> bool:
        return self.origin == ORIGIN_PUBLISHED

    @property
    def ready_to_send(self) -> bool:
        """So a novidade publicada tem endereco para divulgar."""
        return self.published and bool(self.public_url)

    @property
    def public_url(self) -> str:
        return self.novelty.public_url if self.novelty is not None else ''

    @property
    def whatsapp_message(self) -> str:
        """Mensagem inicial da caixa de envio.

        A publicada leva titulo e link, como na tela de novidades lançadas. A
        pendente leva so o assunto: nao existe pagina para apontar ainda, e o
        texto fica para quem for escrever a mensagem.
        """
        if self.novelty is not None:
            return self.novelty.whatsapp_message
        return self.title

    @property
    def date_label(self) -> str:
        return self.created_at.strftime('%d/%m/%Y') if self.created_at else 'sem data'

    @property
    def kind_label(self) -> str:
        return OCCURRENCE_KINDS.get(self.kind, '')

    @property
    def identifier(self) -> str:
        """Chave estavel do card; as duas procedencias nao se cruzam."""
        return f'itn-{self.code}' if self.published else f'oco-{self.occurrence_number}'

    @property
    def reasons_label(self) -> str:
        return ' · '.join(self.reasons)

    @property
    def priority_label(self) -> str:
        """O escore em palavras: o numero cru nao diz nada a quem le a tela."""
        if self.score >= HIGH_PRIORITY:
            return 'alta'
        return 'média' if self.score >= MEDIUM_PRIORITY else 'baixa'


@dataclass(slots=True)
class SuggestionBoard:
    """As duas filas da tela, cada uma com a sua ordem.

    Elas ficam separadas de proposito: a novidade publicada quase sempre pontua
    mais que a ocorrencia pendente, e numa fila unica as pendentes cairiam todas
    para o fim -- justamente as que precisam de alguem para escrever.
    """

    published: list[Suggestion]
    pending: list[Suggestion]
    warning: str = ''

    @property
    def total(self) -> int:
        return len(self.published) + len(self.pending)

    def all(self) -> list[Suggestion]:
        """As duas filas em uma, pela ordem do escore."""
        return WhatsAppSuggestionService._rank([*self.published, *self.pending])


class WhatsAppSuggestionService:
    """Monta a fila de sugestoes de divulgacao.

    Nada aqui e escrito no banco: as duas consultas sao de leitura e a ordem e
    calculada em memoria. O escore nao decide nada sozinho -- cada sugestao
    carrega os motivos que a colocaram naquela posicao, para quem le a tela
    poder discordar.
    """

    def __init__(
        self,
        *,
        database: OccurrenceDatabase | None = None,
        occurrence_service: OccurrenceQueryService | None = None,
        novelty_service: LaunchedNoveltyQueryService | None = None,
        similarity_service: NoveltySimilarityService | None = None,
        dismissal_service: OccurrenceDismissalService | None = None,
        dispatch_service: SuggestionDispatchService | None = None,
    ) -> None:
        self.database = database or OccurrenceDatabase()
        self.occurrences = occurrence_service or OccurrenceQueryService(
            database=self.database
        )
        self.novelties = novelty_service or LaunchedNoveltyQueryService(
            database=self.database
        )
        self.similarity = similarity_service or NoveltySimilarityService(
            database=self.database
        )
        self.dismissals = dismissal_service or OccurrenceDismissalService()
        self.dispatch = dispatch_service or SuggestionDispatchService()

    def suggest(
        self,
        *,
        origin: str = ORIGIN_ANY,
        months: int = DEFAULT_MONTHS,
        start_date: Any = None,
        end_date: Any = None,
        system: str = '',
        module: str = '',
        search: str = '',
        only_sent: bool = False,
        limit: int = MAX_SUGGESTIONS,
    ) -> SuggestionBoard:
        inicio = parse_filter_date(start_date)
        fim = parse_filter_date(end_date)
        if inicio is None and fim is None:
            # Janela padrao: o que foi publicado ou mexido no periodo recente.
            # Divulgar o que e antigo nao costuma interessar a ninguem.
            inicio = date.today() - timedelta(days=30 * max(1, int(months)))

        teto = max(1, int(limit))
        avisos: list[str] = []
        publicadas: list[Suggestion] = []
        pendentes: list[Suggestion] = []
        if origin in (ORIGIN_ANY, ORIGIN_PUBLISHED):
            publicadas, aviso = self._published(
                start_date=inicio, end_date=fim, system=system,
                module=module, search=search,
            )
            if aviso:
                avisos.append(aviso)
        if origin in (ORIGIN_ANY, ORIGIN_PENDING):
            pendentes, aviso = self._pending(
                start_date=inicio, end_date=fim, system=system,
                module=module, search=search,
            )
            if aviso:
                avisos.append(aviso)
        # O "já enviei" é aplicado antes do corte: senão as quinze primeiras
        # seriam gastas com o que já foi divulgado, e a fila nunca andaria.
        enviadas = self.dispatch.load()
        return SuggestionBoard(
            published=self._rank(self._apply_sent(publicadas, enviadas, only_sent))[:teto],
            pending=self._rank(self._apply_sent(pendentes, enviadas, only_sent))[:teto],
            warning=' '.join(avisos),
        )

    @staticmethod
    def _apply_sent(
        suggestions: list[Suggestion],
        sent: dict[str, Any],
        only_sent: bool,
    ) -> list[Suggestion]:
        mantidas = []
        for item in suggestions:
            registro = sent.get(item.identifier)
            if only_sent:
                if registro is None:
                    continue
                item.sent_at = registro.sent_at_label
            elif registro is not None:
                continue
            mantidas.append(item)
        return mantidas

    @staticmethod
    def _rank(suggestions: list[Suggestion]) -> list[Suggestion]:
        return sorted(
            suggestions,
            key=lambda item: (
                -item.score,
                # Empate desfeito pela data, mais recente primeiro; sem data
                # por ultimo, como nas outras telas.
                item.created_at is None,
                -(item.created_at.timestamp() if item.created_at else 0),
            ),
        )

    def _published(
        self, *, start_date: date | None, end_date: date | None,
        system: str, module: str, search: str,
    ) -> tuple[list[Suggestion], str]:
        """Novidades do cadastro: prontas para enviar, basta escolher.

        O filtro de tipo nao entra aqui de proposito: ele depende do vinculo com
        a ocorrencia, e um quarto das novidades nao tem vinculo -- filtrar por
        tipo apagaria justamente essas da lista.
        """
        try:
            publicadas = self.novelties.list_novelties(
                search=search, start_date=start_date, end_date=end_date,
                system=system, module=module,
            )
        except OccurrenceDatabaseError as exc:
            logger.warning('Sugestoes: falha ao ler as novidades publicadas: %s', exc)
            return [], f'Não foi possível ler as novidades publicadas: {exc}'
        return [self._score_published(item) for item in publicadas], ''

    def _score_published(self, novelty: LaunchedNovelty) -> Suggestion:
        tamanho = len(novelty.text.strip())
        pontos = 30
        motivos: list[str] = ['já publicada, só enviar']
        if tamanho >= MIN_TEXT_LENGTH:
            pontos += 15
            motivos.append('texto pronto')
        elif tamanho < SHORT_TEXT_LENGTH:
            pontos -= 25
            motivos.append('quase sem texto')
        if novelty.kind == 'M':
            pontos += 8
            motivos.append('é funcionalidade nova')
        dias = self._days_since(novelty.created_at)
        if dias is not None and dias <= 30:
            pontos += 18
            motivos.append('publicada no último mês')
        elif dias is not None and dias <= 90:
            pontos += 9
            motivos.append('publicada há pouco')
        if novelty.public_url:
            pontos += 5
        else:
            motivos.append('sem link do portal')
        # Desempate fino pela data, para que a ordem nao fique por sorteio
        # quando varias somam o mesmo: o escore cheio e raro, o empate nao.
        if dias is not None:
            pontos += max(0, 6 - dias // 15)
        return Suggestion(
            origin=ORIGIN_PUBLISHED,
            title=novelty.display_title,
            score=self._clamp(pontos),
            reasons=tuple(motivos),
            created_at=novelty.created_at,
            system=novelty.system,
            module=novelty.module,
            kind=novelty.kind,
            occurrence_number=novelty.occurrence_number,
            code=novelty.code,
            excerpt=_preview(novelty.text),
            novelty=novelty,
        )

    def _pending(
        self, *, start_date: date | None, end_date: date | None,
        system: str, module: str, search: str,
    ) -> tuple[list[Suggestion], str]:
        """Ocorrencias que ainda nao viraram materia.

        Entram na lista porque sao o que ha de novo para contar, mas precisam ser
        escritas antes de ir para o WhatsApp. As que foram dispensadas no menu de
        ocorrencias ficam de fora: alguem ja disse que elas nao viram novidade.
        """
        try:
            ocorrencias = self.occurrences.list_changes(
                search=search, start_date=start_date, end_date=end_date,
                system=system, module=module,
            )
        except OccurrenceDatabaseError as exc:
            logger.warning('Sugestoes: falha ao ler as ocorrencias: %s', exc)
            return [], f'Não foi possível ler as ocorrências: {exc}'
        dispensadas = self.dismissals.numbers()
        avaliadas = [
            self._score_pending(item)
            for item in ocorrencias
            if item.number not in dispensadas
        ]
        avaliadas.sort(key=lambda item: -item.score)
        # A conferencia de materia repetida roda so no topo da fila: e ela que
        # custa tempo, e o que esta no fim da lista nao vai ser sugerido.
        for sugestao in avaliadas[:CANDIDATE_LIMIT]:
            self._flag_similar(sugestao)
        return avaliadas, ''

    def _score_pending(self, occurrence: OccurrenceSummary) -> Suggestion:
        entrega = self._delivery(occurrence)
        contexto = self._context_size(occurrence)
        registros = sum(
            1 for item in occurrence.observations
            if len(item.text.strip()) >= RECORD_LENGTH
        )
        pontos = 18
        motivos: list[str] = ['ainda não lançada']
        if entrega:
            pontos += 22
            motivos.append('entrega já descrita')
        else:
            motivos.append('sem o atendimento preenchido')
        if occurrence.solicitation.strip():
            pontos += 8
        if registros >= 3:
            pontos += 7
            motivos.append(f'{registros} registros no histórico')
        if contexto >= RICH_CONTEXT:
            pontos += 7
            motivos.append('contexto farto')
        elif contexto < POOR_CONTEXT:
            pontos -= 20
            motivos.append('pouco contexto')
        if occurrence.kind == 'M':
            pontos += 8
            motivos.append('é funcionalidade nova')
        dias = self._days_since(occurrence.changed_at)
        if dias is not None and dias <= 30:
            pontos += 14
            motivos.append('mexida no último mês')
        elif dias is not None and dias <= 90:
            pontos += 7
        if dias is not None:
            pontos += max(0, 6 - dias // 15)
        return Suggestion(
            origin=ORIGIN_PENDING,
            title=self._subject(occurrence),
            score=self._clamp(pontos),
            reasons=tuple(motivos),
            created_at=occurrence.changed_at,
            system=occurrence.system,
            module=occurrence.module,
            kind=occurrence.kind,
            occurrence_number=occurrence.number,
            excerpt=_preview(entrega or occurrence.solicitation),
            occurrence=occurrence,
        )

    def _flag_similar(self, suggestion: Suggestion) -> None:
        """Desconta quem talvez ja tenha saido, sem tirar da lista.

        A ocorrencia some do menu quando a novidade fica vinculada a ela, e um
        quarto do cadastro nao tem esse vinculo. Entao "nao lançada" aqui quer
        dizer "sem vinculo", e nao "inedita" -- e por isso a conferencia por
        texto tambem pesa no escore.
        """
        if suggestion.occurrence is None:
            return
        parecidas = self.similarity.find_similar([suggestion.occurrence])
        if not parecidas:
            return
        suggestion.similar = tuple(parecidas)
        suggestion.score = self._clamp(suggestion.score - 15)
        suggestion.reasons = (*suggestion.reasons, 'pode já ter saído')

    @staticmethod
    def _context_size(occurrence: OccurrenceSummary) -> int:
        """Quanto material a ocorrencia tem para render uma materia."""
        return (
            len(occurrence.description)
            + len(occurrence.complement)
            + sum(len(item.text) for item in occurrence.observations)
        )

    def _delivery(self, occurrence: OccurrenceSummary) -> str:
        for chave in DELIVERY_SECTIONS:
            texto = str(occurrence.sections.get(chave) or '').strip()
            if texto.lower().strip('. ') in EMPTY_DELIVERY:
                continue
            if len(texto) > DELIVERY_LENGTH:
                return texto
        return ''

    def _subject(self, occurrence: OccurrenceSummary) -> str:
        """Assunto provisorio da ocorrencia: ela ainda nao tem titulo de materia."""
        for fonte in (
            occurrence.novelty_title,
            occurrence.solicitation,
            self._delivery(occurrence),
            occurrence.description,
            occurrence.path,
        ):
            assunto = _first_line(fonte)
            if assunto:
                return assunto
        return f'Ocorrência #{occurrence.number}'

    @staticmethod
    def _days_since(moment: datetime | None) -> int | None:
        dia = _as_date(moment)
        return (date.today() - dia).days if dia is not None else None

    @staticmethod
    def _clamp(value: int) -> int:
        return max(0, min(100, int(value)))


__all__ = [
    'CANDIDATE_LIMIT',
    'DEFAULT_MONTHS',
    'HIGH_PRIORITY',
    'MEDIUM_PRIORITY',
    'MAX_SUGGESTIONS',
    'ORIGIN_ANY',
    'ORIGIN_LABELS',
    'ORIGIN_PENDING',
    'ORIGIN_PUBLISHED',
    'Suggestion',
    'SuggestionBoard',
    'WhatsAppSuggestionService',
]
