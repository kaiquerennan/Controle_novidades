from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
import logging
import math
import re
import time
import unicodedata
from typing import Any, Sequence

from services.launched_novelty_query_service import NOVELTY_TABLE
from services.novelty_history_service import PUBLIC_URL_TEMPLATE
from services.occurrence_database_service import (
    OccurrenceDatabase,
    OccurrenceDatabaseError,
)
from services.occurrence_query_service import OccurrenceSummary
from services.text_sanitizer import html_to_text


logger = logging.getLogger('EMC Novidade Visual IA')


# Por que este servico existe: a view marca a ocorrencia como ja publicada pelo
# TITULO_NOVIDADE, que depende do vinculo OCO_NUMERO na tabela de novidades. Hoje
# 1796 das 6767 novidades (26%) nao tem esse vinculo, ou seja: a ausencia de
# titulo NAO prova que o assunto ainda nao virou materia. A unica pista que resta
# e o texto -- e e isso que aqui se compara.

# O acervo e pequeno e quase nao muda, entao ele e carregado inteiro e guardado
# por alguns minutos. Comparar no Python, e nao com LIKE no banco, resolve de uma
# vez o acento e a ordem das palavras, que o LIKE trataria mal.
INDEX_TTL_SECONDS = 600
_INDEX_CACHE: dict[str, Any] = {'valor': None, 'em': 0.0}

# Quanto do texto da ocorrencia entra na comparacao. A solicitacao costuma ser
# longa e cheia de prosa; o que identifica o assunto esta no inicio dela, no
# caminho do menu e no titulo quando ele existe.
SOLICITATION_LIMIT = 500
MAX_TERMS = 18

# Limiares medidos contra o acervo real, sobre as 960 ocorrencias de mudanca dos
# ultimos seis meses: com estes valores o aviso aparece em 14% delas (1,7 avisos
# em media) e reencontra 4 dos 6 casos conhecidos de materia republicada sem
# vinculo, sem acusar nenhuma das 4 ocorrencias conferidas como ineditas. Mexer
# neles troca uma coisa pela outra: afrouxar acha mais repeticao e tambem mais
# falso alarme, que e o que faz um aviso deixar de ser lido.
MIN_SCORE = 0.40
MIN_MATCHED_TERMS = 2
# Piso do peso do titulo. Sem ele, os titulos antigos de duas ou tres palavras
# ("Configuracao de impressora fiscal:") casavam com quase tudo: o escore divide
# pelo peso do titulo, e um titulo minusculo inflava o resultado.
TITLE_WEIGHT_FLOOR = 46.0
MAX_RESULTS = 5

_INDEX_QUERY = (
    'SELECT ITN_CODIGO, OCO_NUMERO, ITN_DATA_ALTERACAO, ITN_TITULO'
    f' FROM {NOVELTY_TABLE}'
)

_WORD = re.compile(r'[a-z0-9]+')

# Palavras que aparecem em qualquer pedido ou em qualquer novidade: elas nao
# distinguem um assunto do outro. O resto da pontuacao e automatico (quanto mais
# raro o termo no acervo, mais ele vale), mas estas sao frequentes na prosa dos
# chamados e raras nos titulos, e por isso o calculo sozinho nao as descarta.
STOPWORDS = frozenset('''
a ao aos as com como da das de dela dele deles do dos e ela elas ele eles em entre
era essa esse esta este eu foi for isso ja la lhe mais mas me mesmo meu minha muito
na nao nas nem no nos nossa num numa o os ou para pela pelas pelo pelos por qual
quando que quem se sem ser seu sua suas seus so sobre sou tem tinha ter teu todo
toda todos todas tu tua um uma vez
sendo sido estar estao fica ficar ficou havia houve
favor gentileza segue anexo conforme abaixo acima mesma
cliente clientes usuario usuarios empresa
emc versao versoes campo campos tela telas opcao opcoes botao menu
necessario necessidade solicita solicitado solicitacao solicitacoes
alteracao alteracoes alterar alterado alterada alterados ajuste ajustar ajustado
implementar implementada implementado implementacao criar criado criada criadas
incluir incluido incluida inclusao permitir permitido permitida permite
melhoria melhorias correcao corrigido corrigida corrigidas novo nova novos novas
problema erro erros falha onde atraves apenas somente tambem
possivel possibilidade poder pode podem deve devera deveria
realizar realizado realizada feito fazer gerar gerado geracao mostrar exibir exibido
informar informado informacao informacoes dado dados
hoje atual atualmente antes depois ainda agora sistema
ocorria ocorre passa passou adicionado adicionada
'''.split())


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize('NFKD', value)
    return ''.join(char for char in normalized if not unicodedata.combining(char))


def tokenize(text: str) -> list[str]:
    """Palavras comparaveis do texto, sem acento, sem caixa e sem as vazias.

    O numero curto fica de fora porque quase sempre e quantidade ou codigo de
    tela; de tres digitos para cima ele costuma ser o que nomeia o assunto
    (CFOP 5919, registro F600).
    """
    tokens: list[str] = []
    for word in _WORD.findall(_strip_accents(str(text or '')).lower()):
        if word in STOPWORDS:
            continue
        if word.isdigit():
            if len(word) >= 3:
                tokens.append(word)
        elif len(word) >= 4:
            tokens.append(word)
    return tokens


@dataclass(slots=True)
class SimilarNovelty:
    """Novidade publicada cujo titulo se parece com o assunto da ocorrencia."""

    code: int
    title: str
    created_at: datetime | None = None
    occurrence_number: int = 0
    score: float = 0.0
    matched_terms: tuple[str, ...] = ()

    @property
    def score_label(self) -> str:
        return f'{round(self.score * 100)}%'

    @property
    def date_label(self) -> str:
        return self.created_at.strftime('%d/%m/%Y') if self.created_at else 'sem data'

    @property
    def link_label(self) -> str:
        """A novidade sem vinculo e justamente a que a tela nao consegue ver."""
        if self.occurrence_number:
            return f'vinculada à OC #{self.occurrence_number}'
        return 'sem vínculo com ocorrência'

    @property
    def public_url(self) -> str:
        return PUBLIC_URL_TEMPLATE.format(code=self.code) if self.code else ''

    @property
    def terms_label(self) -> str:
        return ', '.join(self.matched_terms)


@dataclass(slots=True)
class _Entry:
    code: int
    title: str
    created_at: datetime | None
    occurrence_number: int
    tokens: frozenset[str]
    weight: float


@dataclass(slots=True)
class _Index:
    """Acervo de titulos preparado para comparacao."""

    entries: list[_Entry] = field(default_factory=list)
    frequency: Counter = field(default_factory=Counter)
    # Guardado a parte porque o peso dos titulos e calculado antes de a lista de
    # entradas estar montada, e e desse total que a raridade do termo depende.
    documents: int = 0

    @property
    def size(self) -> int:
        return self.documents

    def idf(self, token: str) -> float:
        """Peso do termo: quanto mais raro no acervo, mais ele identifica o assunto."""
        found = self.frequency.get(token, 0)
        if not found or self.documents < 1:
            return 0.0
        return math.log(self.documents / found)


def _as_datetime(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


class NoveltySimilarityService:
    """Compara o assunto da ocorrencia com os titulos ja publicados.

    O resultado e um aviso, nunca um impedimento: a comparacao e por texto e
    pode errar nas duas direcoes, entao quem decide se a materia ja existe
    continua sendo quem vai escrever.
    """

    def __init__(self, database: OccurrenceDatabase | None = None) -> None:
        self.database = database or OccurrenceDatabase()

    @property
    def ready(self) -> bool:
        """Indice ja em memoria e dentro da validade."""
        return (
            _INDEX_CACHE['valor'] is not None
            and time.monotonic() - float(_INDEX_CACHE['em']) < INDEX_TTL_SECONDS
        )

    def warm(self) -> bool:
        """Carrega o acervo antes de ser preciso, para o aviso sair instantaneo."""
        try:
            self.index()
        except OccurrenceDatabaseError as exc:
            logger.warning('Nao foi possivel preparar o acervo de novidades: %s', exc)
            return False
        return True

    def index(self, *, refresh: bool = False) -> _Index:
        if not refresh and self.ready:
            return _INDEX_CACHE['valor']
        rows = self.database.fetch_all(_INDEX_QUERY, limit=None)
        indice = self.build_index(rows)
        _INDEX_CACHE['valor'] = indice
        _INDEX_CACHE['em'] = time.monotonic()
        return indice

    @staticmethod
    def build_index(rows: Sequence[dict[str, Any]]) -> _Index:
        indice = _Index()
        pendentes: list[tuple[_Entry, frozenset[str]]] = []
        for row in rows:
            # O titulo vem com entidades HTML mesmo quando nao tem tag alguma.
            titulo = html_to_text(row.get('ITN_TITULO')).strip()
            if not titulo:
                continue
            tokens = frozenset(tokenize(titulo))
            if not tokens:
                continue
            try:
                codigo = int(row.get('ITN_CODIGO') or 0)
            except (TypeError, ValueError):
                continue
            try:
                numero = int(row.get('OCO_NUMERO') or 0)
            except (TypeError, ValueError):
                numero = 0
            entrada = _Entry(
                code=codigo,
                title=titulo,
                created_at=_as_datetime(row.get('ITN_DATA_ALTERACAO')),
                occurrence_number=numero,
                tokens=tokens,
                weight=0.0,
            )
            indice.frequency.update(tokens)
            pendentes.append((entrada, tokens))
        indice.documents = len(pendentes)
        # O peso de cada titulo so pode ser calculado depois de contar todos:
        # ele depende da raridade de cada termo no acervo inteiro.
        for entrada, tokens in pendentes:
            entrada.weight = max(
                TITLE_WEIGHT_FLOOR, sum(indice.idf(token) for token in tokens)
            )
            indice.entries.append(entrada)
        return indice

    def terms_of(self, occurrence: OccurrenceSummary, indice: _Index) -> dict[str, float]:
        """Termos que identificam o assunto da ocorrencia, com o peso de cada um.

        O termo que nao aparece em nenhum titulo publicado e descartado: ele
        nunca casaria com nada e so diluiria o escore dos que casam.
        """
        bruto = tokenize(occurrence.novelty_title)
        bruto += tokenize(occurrence.path)
        bruto += tokenize(occurrence.solicitation[:SOLICITATION_LIMIT])
        pesos = [
            (token, indice.idf(token))
            for token in dict.fromkeys(bruto)
            if indice.frequency.get(token, 0)
        ]
        pesos.sort(key=lambda par: -par[1])
        return dict(pesos[:MAX_TERMS])

    def find_similar(
        self,
        occurrences: Sequence[OccurrenceSummary],
        *,
        limit: int = MAX_RESULTS,
    ) -> list[SimilarNovelty]:
        """Novidades publicadas parecidas com o assunto das ocorrencias dadas.

        Uma falha de banco devolve lista vazia de proposito: este servico existe
        para avisar, e um aviso que nao pode ser calculado nao deve impedir o
        lancamento.
        """
        validas = [item for item in occurrences if item is not None]
        if not validas:
            return []
        try:
            indice = self.index()
        except OccurrenceDatabaseError as exc:
            logger.warning('Conferencia de novidades parecidas indisponivel: %s', exc)
            return []
        if not indice.entries:
            return []

        melhores: dict[int, SimilarNovelty] = {}
        for occurrence in validas:
            for achado in self._compare(occurrence, indice):
                anterior = melhores.get(achado.code)
                if anterior is None or achado.score > anterior.score:
                    melhores[achado.code] = achado
        return sorted(melhores.values(), key=lambda item: -item.score)[:limit]

    def _compare(
        self, occurrence: OccurrenceSummary, indice: _Index
    ) -> list[SimilarNovelty]:
        pesos = self.terms_of(occurrence, indice)
        if not pesos:
            return []
        total = sum(pesos.values())
        if total <= 0:
            return []
        achados: list[SimilarNovelty] = []
        for entrada in indice.entries:
            casados = [token for token in pesos if token in entrada.tokens]
            if len(casados) < MIN_MATCHED_TERMS:
                continue
            massa = sum(pesos[token] for token in casados)
            # Dice ponderado: mede o quanto os dois textos se explicam um ao
            # outro. Olhar so para a ocorrencia premiaria o titulo generico, e
            # olhar so para o titulo premiaria a ocorrencia longa.
            escore = 2 * massa / (total + entrada.weight)
            if escore < MIN_SCORE:
                continue
            casados.sort(key=lambda token: -pesos[token])
            achados.append(
                SimilarNovelty(
                    code=entrada.code,
                    title=entrada.title,
                    created_at=entrada.created_at,
                    occurrence_number=entrada.occurrence_number,
                    score=escore,
                    matched_terms=tuple(casados),
                )
            )
        return achados


__all__ = [
    'MAX_RESULTS',
    'MIN_MATCHED_TERMS',
    'MIN_SCORE',
    'NoveltySimilarityService',
    'SimilarNovelty',
    'STOPWORDS',
    'TITLE_WEIGHT_FLOOR',
    'tokenize',
]
