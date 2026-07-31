from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class Annotation:
    """Anotacao cujas coordenadas sao fracoes da imagem (0 a 1)."""

    kind: str
    points: list[tuple[float, float]]
    color: str = '#ef4444'
    width: int = 4
    font_size: int = 28
    text_background: str = '#ffffff'
    opacity: float = 1.0
    text: str = ''
    number: int = 1
    border_color: str = ''
    fill_color: str = '#fef08a'
    fill_enabled: bool = False
    fill_opacity: float = 0.25
    arrow_head_size: int = 18
    arrow_head_style: str = 'filled'
    double_arrow: bool = False
    bold: bool = False
    text_background_enabled: bool = True
    id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        allowed = {
            'arrow', 'line', 'rectangle', 'circle', 'text', 'marker', 'highlight',
            'arrow_text',
        }
        if self.kind not in allowed:
            raise ValueError(f'Tipo de anotacao invalido: {self.kind}')
        if not self.points:
            raise ValueError('A anotacao precisa de pelo menos um ponto.')
        self.points = [
            (min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y))))
            for x, y in self.points
        ]
        self.width = min(30, max(1, int(self.width)))
        self.font_size = min(160, max(8, int(self.font_size)))
        self.opacity = min(1.0, max(0.05, float(self.opacity)))
        self.number = min(999, max(1, int(self.number)))
        self.border_color = self.border_color or self.color
        self.fill_opacity = min(1.0, max(0.0, float(self.fill_opacity)))
        self.arrow_head_size = min(120, max(6, int(self.arrow_head_size)))
        if self.arrow_head_style not in {'filled', 'open'}:
            self.arrow_head_style = 'filled'
        self.fill_enabled = bool(self.fill_enabled)
        self.double_arrow = bool(self.double_arrow)
        self.bold = bool(self.bold)
        self.text_background_enabled = bool(self.text_background_enabled)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> 'Annotation':
        allowed_fields = set(cls.__dataclass_fields__)
        payload = {key: item for key, item in value.items() if key in allowed_fields}
        if 'fill_color' not in value:
            payload['fill_color'] = str(payload.get('color') or '#fef08a')
        return cls(**payload)


class AnnotationDocument:
    """Documento com historico independente para desfazer e refazer."""

    def __init__(self) -> None:
        self.items: list[Annotation] = []
        self._undo: list[list[dict[str, Any]]] = []
        self._redo: list[list[dict[str, Any]]] = []

    def _snapshot(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.items]

    def _restore(self, snapshot: list[dict[str, Any]]) -> None:
        self.items = [Annotation.from_dict(item) for item in snapshot]

    def _before_change(self) -> None:
        self._undo.append(self._snapshot())
        self._redo.clear()

    def add(self, item: Annotation) -> None:
        self._before_change()
        self.items.append(item)

    def update(self, annotation_id: str, replacement: Annotation) -> bool:
        for index, item in enumerate(self.items):
            if item.id == annotation_id:
                self._before_change()
                replacement.id = annotation_id
                self.items[index] = replacement
                return True
        return False

    def delete(self, annotation_id: str) -> bool:
        if not any(item.id == annotation_id for item in self.items):
            return False
        self._before_change()
        self.items = [item for item in self.items if item.id != annotation_id]
        return True

    def clear(self) -> None:
        if self.items:
            self._before_change()
            self.items.clear()

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

    def to_list(self) -> list[dict[str, Any]]:
        return self._snapshot()
