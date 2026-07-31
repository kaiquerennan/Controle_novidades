from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


BLOCK_TYPES = {'titulo', 'subtitulo', 'texto', 'lista', 'imagem', 'divisor'}
DISPLAY_MODES = {'normal', 'ampliado', 'lado_a_lado'}


def _block_id() -> str:
    return f'bloco_{uuid4().hex[:12]}'


@dataclass(slots=True)
class ContentBlock:
    tipo: str
    id: str = field(default_factory=_block_id)
    conteudo: str = ''
    itens: list[dict[str, Any]] = field(default_factory=list)
    destaques: list[str] = field(default_factory=list)
    secao_id: str = ''
    rotina: str = ''
    caminho: str = ''
    imagem_id: str | None = None
    imagem_secundaria_id: str | None = None
    legenda: str = ''
    modo_exibicao: str = 'normal'
    confianca: float | None = None
    motivo: str = ''
    papel: str = ''

    def __post_init__(self) -> None:
        if self.tipo not in BLOCK_TYPES:
            raise ValueError(f'Tipo de bloco inválido: {self.tipo}')
        if self.tipo == 'imagem' and not self.imagem_id:
            raise ValueError('Blocos de imagem precisam de imagem_id.')
        if self.modo_exibicao not in DISPLAY_MODES:
            raise ValueError(f'Modo de exibição inválido: {self.modo_exibicao}')
        if self.tipo != 'imagem':
            self.imagem_id = None
            self.imagem_secundaria_id = None
            self.legenda = ''
            self.modo_exibicao = 'normal'
            self.confianca = None
            self.motivo = ''
        if self.tipo not in {'texto', 'lista'}:
            self.papel = ''
        if self.tipo != 'lista':
            self.itens = []
        else:
            self.itens = [
                {
                    'texto': str(item.get('texto') or ''),
                    'destaques': [str(value) for value in item.get('destaques') or []],
                }
                for item in self.itens if str(item.get('texto') or '').strip()
            ]
        if self.modo_exibicao != 'lado_a_lado':
            self.imagem_secundaria_id = None
        if self.confianca is not None:
            self.confianca = min(1.0, max(0.0, float(self.confianca)))

    @property
    def image_ids(self) -> list[str]:
        return [
            image_id for image_id in (self.imagem_id, self.imagem_secundaria_id)
            if image_id
        ]

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {'id': self.id, 'tipo': self.tipo}
        if self.tipo in {'titulo', 'subtitulo', 'texto'}:
            value['conteudo'] = self.conteudo
            if self.papel:
                value['papel'] = self.papel
            if self.destaques:
                value['destaques'] = list(self.destaques)
        elif self.tipo == 'lista':
            value['itens'] = deepcopy(self.itens)
            if self.papel:
                value['papel'] = self.papel
        elif self.tipo == 'imagem':
            value.update(
                imagem_id=self.imagem_id,
                legenda=self.legenda,
                modo_exibicao=self.modo_exibicao,
            )
            if self.imagem_secundaria_id:
                value['imagem_secundaria_id'] = self.imagem_secundaria_id
            if self.confianca is not None:
                value['confianca'] = self.confianca
            if self.motivo:
                value['motivo'] = self.motivo
        if self.secao_id:
            value['secao_id'] = self.secao_id
        if self.rotina:
            value['rotina'] = self.rotina
        if self.caminho:
            value['caminho'] = self.caminho
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> 'ContentBlock':
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: item for key, item in value.items() if key in allowed})


class CompositionDocument:
    def __init__(
        self,
        blocks: list[ContentBlock] | None = None,
        *,
        source: str = 'manual',
        fallback_image_ids: list[str] | None = None,
        cover: dict[str, Any] | None = None,
        review: dict[str, Any] | None = None,
    ) -> None:
        self.source = source
        self.blocks = blocks or [ContentBlock(tipo='titulo'), ContentBlock(tipo='texto')]
        self.fallback_image_ids = list(fallback_image_ids or [])
        self.cover = deepcopy(cover or {
            'solicitada': False,
            'status': 'nao_gerada',
            'tema': '',
            'arquivo': '',
            'mensagem': '',
        })
        self.review = deepcopy(review or {
            'aprovada': False,
            'avisos': [],
            'correcao_automatica_realizada': False,
            'status': 'pendente',
        })
        self._undo: list[list[dict[str, Any]]] = []
        self._redo: list[list[dict[str, Any]]] = []

    def _snapshot(self) -> list[dict[str, Any]]:
        return deepcopy([block.to_dict() for block in self.blocks])

    def _before_change(self) -> None:
        self._undo.append(self._snapshot())
        self._undo = self._undo[-50:]
        self._redo.clear()

    def _restore(self, snapshot: list[dict[str, Any]]) -> None:
        self.blocks = [ContentBlock.from_dict(value) for value in snapshot]

    def find(self, block_id: str) -> ContentBlock | None:
        return next((block for block in self.blocks if block.id == block_id), None)

    def index(self, block_id: str) -> int | None:
        return next((index for index, block in enumerate(self.blocks) if block.id == block_id), None)

    @property
    def title(self) -> str:
        block = next((value for value in self.blocks if value.tipo == 'titulo'), None)
        return block.conteudo.strip() if block else ''

    @property
    def text(self) -> str:
        values: list[str] = []
        for block in self.blocks:
            if block.tipo in {'subtitulo', 'texto'} and block.conteudo.strip():
                values.append(block.conteudo.strip())
            elif block.tipo == 'lista':
                values.extend(str(item.get('texto') or '').strip() for item in block.itens)
        return '\n\n'.join(value for value in values if value)

    @property
    def introduction(self) -> str:
        marked = next(
            (block for block in self.blocks if block.tipo == 'texto' and block.papel == 'introducao'),
            None,
        )
        if marked:
            return marked.conteudo.strip()
        first = next((block for block in self.blocks if block.tipo == 'texto'), None)
        return first.conteudo.strip() if first else ''

    def add(
        self,
        tipo: str,
        *,
        index: int | None = None,
        conteudo: str = '',
        itens: list[dict[str, Any]] | None = None,
        destaques: list[str] | None = None,
        secao_id: str = '',
        rotina: str = '',
        caminho: str = '',
        imagem_id: str | None = None,
        imagem_secundaria_id: str | None = None,
        legenda: str = '',
        modo_exibicao: str = 'normal',
        papel: str = '',
    ) -> ContentBlock:
        block = ContentBlock(
            tipo=tipo,
            conteudo=conteudo,
            itens=list(itens or []),
            destaques=list(destaques or []),
            secao_id=secao_id,
            rotina=rotina,
            caminho=caminho,
            imagem_id=imagem_id,
            imagem_secundaria_id=imagem_secundaria_id,
            legenda=legenda,
            modo_exibicao=modo_exibicao,
            papel=papel,
        )
        self._before_change()
        target = len(self.blocks) if index is None else min(max(0, index), len(self.blocks))
        self.blocks.insert(target, block)
        return block

    def update(self, block_id: str, **changes: Any) -> bool:
        index = self.index(block_id)
        if index is None:
            return False
        value = self.blocks[index].to_dict()
        value.update(changes)
        value['id'] = block_id
        replacement = ContentBlock.from_dict(value)
        if replacement.to_dict() == self.blocks[index].to_dict():
            return False
        self._before_change()
        self.blocks[index] = replacement
        return True

    def delete(self, block_id: str) -> bool:
        index = self.index(block_id)
        if index is None:
            return False
        self._before_change()
        self.blocks.pop(index)
        return True

    def move(self, block_id: str, target: int) -> bool:
        index = self.index(block_id)
        if index is None:
            return False
        target = min(max(0, target), len(self.blocks) - 1)
        if target == index:
            return False
        self._before_change()
        block = self.blocks.pop(index)
        self.blocks.insert(target, block)
        return True

    def move_by(self, block_id: str, offset: int) -> bool:
        index = self.index(block_id)
        return False if index is None else self.move(block_id, index + offset)

    def move_image_block(self, block_id: str, target: int) -> bool:
        block = self.find(block_id)
        if block is None or block.tipo != 'imagem':
            return False
        before = self._snapshot()
        image_ids_before = Counter(
            image_id for item in self.blocks for image_id in item.image_ids
        )
        if not self.move(block_id, target):
            return False
        image_ids_after = Counter(
            image_id for item in self.blocks for image_id in item.image_ids
        )
        if image_ids_after != image_ids_before:
            self._restore(before)
            if self._undo:
                self._undo.pop()
            return False
        return True

    def move_to_start(self, block_id: str) -> bool:
        return self.move(block_id, 0)

    def move_to_end(self, block_id: str) -> bool:
        return self.move(block_id, len(self.blocks) - 1)

    def replace_main_text(self, content: str) -> ContentBlock:
        block = next((value for value in self.blocks if value.tipo == 'texto'), None)
        if block:
            self.update(block.id, conteudo=content)
            return self.find(block.id) or block
        return self.add('texto', conteudo=content)

    def split_text(self, block_id: str, offset: int | None = None) -> bool:
        index = self.index(block_id)
        if index is None or self.blocks[index].tipo != 'texto':
            return False
        content = self.blocks[index].conteudo
        if len(content.strip()) < 2:
            return False
        if offset is None:
            midpoint = len(content) // 2
            candidates = [position for position, char in enumerate(content) if char.isspace()]
            offset = min(candidates, key=lambda value: abs(value - midpoint)) if candidates else midpoint
        offset = min(max(1, int(offset)), len(content) - 1)
        first, second = content[:offset].strip(), content[offset:].strip()
        if not first or not second:
            return False
        self._before_change()
        source = self.blocks[index]
        self.blocks[index] = ContentBlock(
            tipo='texto', id=block_id, conteudo=first, papel=source.papel,
            secao_id=source.secao_id, rotina=source.rotina, caminho=source.caminho,
        )
        self.blocks.insert(index + 1, ContentBlock(
            tipo='texto', conteudo=second, secao_id=source.secao_id,
            rotina=source.rotina, caminho=source.caminho,
        ))
        return True

    def join_with_next(self, block_id: str) -> bool:
        index = self.index(block_id)
        if index is None or index + 1 >= len(self.blocks):
            return False
        first, second = self.blocks[index:index + 2]
        if first.tipo != 'texto' or second.tipo != 'texto':
            return False
        self._before_change()
        self.blocks[index] = ContentBlock(
            tipo='texto', id=first.id,
            conteudo='\n\n'.join(value for value in (first.conteudo, second.conteudo) if value),
            papel=first.papel or second.papel, secao_id=first.secao_id or second.secao_id,
            rotina=first.rotina or second.rotina, caminho=first.caminho or second.caminho,
            destaques=[*first.destaques, *second.destaques],
        )
        self.blocks.pop(index + 1)
        return True

    def used_image_ids(self) -> set[str]:
        return {
            image_id for block in self.blocks for image_id in block.image_ids
        }

    def available_image_ids(self, all_image_ids: list[str]) -> list[str]:
        used = self.used_image_ids()
        return [image_id for image_id in all_image_ids if image_id not in used]

    def apply_suggestions(
        self,
        suggestions: list[dict[str, Any]],
        *,
        confirmed: bool,
        minimum_confidence: float = 0.6,
    ) -> list[str]:
        if not confirmed:
            raise ValueError('A aplicação das sugestões exige confirmação explícita.')
        applicable = [
            value for value in suggestions
            if float(value.get('confianca') or 0) >= minimum_confidence
            and (value.get('imagem_id') or value.get('imagem_ids'))
            and self.index(str(value.get('apos_bloco'))) is not None
        ]
        if not applicable:
            return []
        self._before_change()
        applied: list[str] = []
        for suggestion in applicable:
            image_ids = [
                str(value) for value in (
                    suggestion.get('imagem_ids') or [suggestion.get('imagem_id')]
                ) if value
            ][:2]
            if not image_ids:
                continue
            for block in list(self.blocks):
                if any(image_id in block.image_ids for image_id in image_ids):
                    self.blocks.remove(block)
            after = self.index(str(suggestion['apos_bloco']))
            if after is None:
                continue
            mode = str(suggestion.get('modo_exibicao') or 'normal')
            if mode == 'lado_a_lado' and len(image_ids) != 2:
                mode = 'normal'
            self.blocks.insert(after + 1, ContentBlock(
                tipo='imagem',
                imagem_id=image_ids[0],
                imagem_secundaria_id=image_ids[1] if len(image_ids) > 1 else None,
                legenda=str(suggestion.get('legenda') or ''),
                modo_exibicao=mode,
                confianca=float(suggestion['confianca']),
                motivo=str(suggestion.get('motivo') or ''),
            ))
            applied.extend(image_ids)
        return applied

    def apply_text_revision(self, revised_blocks: list[ContentBlock]) -> bool:
        """Substitui somente blocos editoriais e preserva as ancoras das imagens."""
        editorial = [
            deepcopy(block) for block in revised_blocks
            if block.tipo in {'titulo', 'subtitulo', 'texto', 'lista', 'divisor'}
        ]
        if not editorial:
            return False
        old_editorial_count = sum(block.tipo != 'imagem' for block in self.blocks)
        anchored_images: list[tuple[int, ContentBlock]] = []
        seen_editorial = 0
        for block in self.blocks:
            if block.tipo == 'imagem':
                anchored_images.append((seen_editorial, deepcopy(block)))
            else:
                seen_editorial += 1
        rebuilt = editorial
        offset = 0
        for anchor, image in anchored_images:
            target = (
                round(anchor * len(editorial) / old_editorial_count)
                if old_editorial_count else len(editorial)
            )
            rebuilt.insert(min(len(rebuilt), max(0, target + offset)), image)
            offset += 1
        if [block.to_dict() for block in rebuilt] == [
            block.to_dict() for block in self.blocks
        ]:
            return False
        self._before_change()
        self.blocks = rebuilt
        return True

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        return True

    def remove_cover(self) -> bool:
        if self.cover.get('status') != 'gerada':
            return False
        self.cover = {
            **self.cover,
            'status': 'removida',
            'arquivo': '',
            'mensagem': '',
        }
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            'origem': self.source,
            'titulo': self.title,
            'imagens_fallback': list(self.fallback_image_ids),
            'capa': deepcopy(self.cover),
            'revisao': deepcopy(self.review),
            'blocos': [
                {'ordem': order, **block.to_dict()}
                for order, block in enumerate(self.blocks, 1)
            ],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> 'CompositionDocument':
        return cls(
            [ContentBlock.from_dict(block) for block in value.get('blocos', [])],
            source=str(value.get('origem') or 'manual'),
            fallback_image_ids=[
                str(image_id) for image_id in value.get('imagens_fallback', [])
            ],
            cover=dict(value.get('capa') or {}),
            review=dict(value.get('revisao') or {}),
        )
