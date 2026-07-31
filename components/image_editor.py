from dataclasses import replace

from models.annotation import Annotation, AnnotationDocument
from models.anonymization_area import AnonymizationArea


class ImageEditor:
    """Controla rascunhos, selecao e historico; a interface fornece os cliques."""

    TWO_POINT_KINDS = {'arrow', 'line', 'rectangle', 'circle', 'highlight', 'arrow_text'}

    def __init__(self) -> None:
        self.document = AnnotationDocument()
        self.draft: Annotation | None = None
        self.first_point: tuple[float, float] | None = None

    def click(
        self, x: float, y: float, *, kind: str, color: str, width: int,
        font_size: int, text_background: str, opacity: float, text: str,
        number: int, border_color: str = '', fill_color: str = '#fef08a',
        fill_enabled: bool = False, fill_opacity: float = 0.25,
        arrow_head_size: int = 18, arrow_head_style: str = 'filled',
        double_arrow: bool = False, bold: bool = False,
        text_background_enabled: bool = True,
    ) -> str:
        point = (min(1.0, max(0.0, x)), min(1.0, max(0.0, y)))
        common = dict(
            kind=kind, color=color, width=width, font_size=font_size,
            text_background=text_background, opacity=opacity, text=text,
            number=number, border_color=border_color or color,
            fill_color=fill_color, fill_enabled=fill_enabled,
            fill_opacity=fill_opacity, arrow_head_size=arrow_head_size,
            arrow_head_style=arrow_head_style, double_arrow=double_arrow,
            bold=bold, text_background_enabled=text_background_enabled,
        )
        if kind in self.TWO_POINT_KINDS and self.first_point is None:
            self.first_point = point
            self.draft = None
            return 'Clique no ponto final.'
        points = [self.first_point, point] if self.first_point else [point]
        self.draft = Annotation(points=points, **common)
        self.first_point = None
        return 'Previa pronta. Adicione a marcacao ou cancele.'

    def confirm(self) -> bool:
        if not self.draft:
            return False
        self.document.add(self.draft)
        self.draft = None
        return True

    def cancel(self) -> None:
        self.first_point = None
        self.draft = None

    def selected(self, annotation_id: str) -> Annotation | None:
        return next((item for item in self.document.items if item.id == annotation_id), None)

    def update_selected(self, annotation_id: str, **changes: object) -> bool:
        current = self.selected(annotation_id)
        if not current:
            return False
        return self.document.update(annotation_id, replace(current, **changes))

    def move_selected(self, annotation_id: str, dx: float, dy: float) -> bool:
        current = self.selected(annotation_id)
        if not current:
            return False
        points = [
            (min(1.0, max(0.0, x + dx)), min(1.0, max(0.0, y + dy)))
            for x, y in current.points
        ]
        return self.document.update(annotation_id, replace(current, points=points))

    def preview_items(self) -> list[Annotation]:
        return [*self.document.items, *([self.draft] if self.draft else [])]


class AnonymizationAreaEditor:
    """Controla rascunho local de areas manuais de anonimizacao."""

    def __init__(self) -> None:
        self.first_point: tuple[float, float] | None = None
        self.draft: AnonymizationArea | None = None

    def click(
        self, x: float, y: float, *, data_type: str, replacement_text: str, note: str = '',
    ) -> str:
        point = (min(1.0, max(0.0, x)), min(1.0, max(0.0, y)))
        if self.first_point is None:
            self.first_point = point
            self.draft = None
            return 'Clique no ponto final da area de anonimizacao.'
        self.draft = AnonymizationArea(
            points=[self.first_point, point], data_type=data_type,
            replacement_text=replacement_text, note=note,
        )
        self.first_point = None
        return 'Area de anonimizacao pronta para adicionar.'

    def confirm(self) -> AnonymizationArea | None:
        if not self.draft:
            return None
        area = self.draft
        self.draft = None
        return area

    def cancel(self) -> None:
        self.first_point = None
        self.draft = None