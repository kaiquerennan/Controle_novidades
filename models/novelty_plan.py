from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MarkingInstruction(BaseModel):
    model_config = ConfigDict(extra='forbid')

    tipo: Literal['seta', 'caixa', 'circulo', 'numero', 'texto', 'destaque']
    alvo: str
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    x2: float | None = Field(default=None, ge=0, le=1)
    y2: float | None = Field(default=None, ge=0, le=1)
    texto: str = ''
    ordem: int = Field(default=1, ge=1, le=99)


class PlannedImage(BaseModel):
    model_config = ConfigDict(extra='forbid')

    imagem_id: str
    marcacoes: list[MarkingInstruction] = Field(default_factory=list)


class AnalyzedImage(PlannedImage):
    descricao: str = ''
    texto_visivel: list[str] = Field(default_factory=list)
    elementos: list[str] = Field(default_factory=list)


class StructuredListItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    texto: str
    destaques: list[str] = Field(default_factory=list)


class PlannedBlock(BaseModel):
    model_config = ConfigDict(extra='forbid')

    tipo: Literal['titulo', 'subtitulo', 'texto', 'lista', 'imagem', 'divisor']
    conteudo: str = ''
    itens: list[StructuredListItem] = Field(default_factory=list)
    destaques: list[str] = Field(default_factory=list)
    secao_id: str = ''
    rotina: str = ''
    caminho: str = ''
    bloco_id: str = ''
    imagem_ids: list[str] = Field(default_factory=list, max_length=2)
    legenda: str = ''
    modo_exibicao: Literal['normal', 'ampliado', 'lado_a_lado'] = 'normal'
    apos_bloco: str = ''
    confianca: float | None = Field(default=None, ge=0, le=1)
    motivo: str = ''
    papel: str = ''

    @model_validator(mode='after')
    def validate_block(self) -> 'PlannedBlock':
        if self.tipo == 'imagem' and not self.imagem_ids:
            raise ValueError('Bloco de imagem sem imagem_ids.')
        if self.tipo == 'lista' and not self.itens:
            raise ValueError('Bloco de lista sem itens.')
        if self.tipo != 'imagem':
            self.imagem_ids = []
            self.legenda = ''
            self.modo_exibicao = 'normal'
            self.apos_bloco = ''
            self.confianca = None
            self.motivo = ''
        elif self.modo_exibicao == 'lado_a_lado' and len(self.imagem_ids) != 2:
            raise ValueError('Modo lado a lado exige exatamente duas imagens.')
        if self.tipo != 'lista':
            self.itens = []
        return self


class NoveltyPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')

    blocos: list[PlannedBlock] = Field(min_length=1)
    imagens: list[PlannedImage] = Field(default_factory=list)


class OpenAINoveltyPlan(NoveltyPlan):
    imagens: list[AnalyzedImage] = Field(default_factory=list)


class FidelityReview(BaseModel):
    model_config = ConfigDict(extra='forbid')

    aprovada: bool = False
    avisos: list[str] = Field(default_factory=list)
    correcoes_necessarias: list[str] = Field(default_factory=list)
    blocos_para_corrigir: list[str] = Field(default_factory=list)
    secoes_para_corrigir: list[str] = Field(default_factory=list)
    imagens_nao_utilizadas: list[str] = Field(default_factory=list)
    informacoes_possivelmente_inventadas: list[str] = Field(default_factory=list)
    nomes_tecnicos_alterados: list[str] = Field(default_factory=list)