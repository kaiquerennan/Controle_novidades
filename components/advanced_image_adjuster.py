from __future__ import annotations

from collections.abc import Awaitable
from copy import deepcopy
from typing import Any, Callable

from nicegui import events, ui

from components.image_editor import AnonymizationAreaEditor, ImageEditor
from models.annotation import Annotation
from models.anonymization_area import AnonymizationArea, DATA_TYPES
from services.image_service import SessionWorkspace, render_annotations, render_anonymization_preview


SHAPE_OPTIONS = {
    'arrow': 'Seta',
    'line': 'Linha',
    'rectangle': 'Retângulo',
    'circle': 'Círculo/Elipse',
    'highlight': 'Destaque',
    'text': 'Texto',
    'marker': 'Número',
    'arrow_text': 'Seta com texto',
}
SHAPE_ICONS = {
    'arrow': 'arrow_forward',
    'line': 'remove',
    'rectangle': 'crop_square',
    'circle': 'radio_button_unchecked',
    'highlight': 'border_color',
    'text': 'title',
    'marker': 'pin',
    'arrow_text': 'drive_file_rename_outline',
}
TEXT_KINDS = {'text', 'arrow_text'}
SHAPE_KINDS = {'rectangle', 'circle', 'highlight'}
ARROW_KINDS = {'arrow', 'arrow_text'}


class AdvancedImageAdjuster:
    def __init__(
        self, images: list[dict[str, Any]], workspace: SessionWorkspace,
        on_change: Callable[[], None], selected_id: str | None = None,
        on_anonymize: Callable[[dict[str, Any]], Awaitable[bool]] | None = None,
        can_anonymize: Callable[[], bool] | None = None,
    ) -> None:
        self.images = images
        self.workspace = workspace
        self.on_change = on_change
        self.on_anonymize = on_anonymize
        self.can_anonymize = can_anonymize or (lambda: False)
        valid_ids = {item['id'] for item in images}
        self.selected_id = selected_id if selected_id in valid_ids else (
            images[0]['id'] if images else None
        )
        self.selected_annotation_id: str | None = None
        self.selected_area_id: str | None = None
        self.panel: Any = None
        self.annotation_panel: Any = None
        self.anonymization_panel: Any = None
        self.area_editor = AnonymizationAreaEditor()
        self._shape_buttons: dict[str, Any] = {}
        self._saved_snapshots = {
            item['id']: deepcopy(item['editor'].document.to_list()) for item in images
        }

    def current(self) -> dict[str, Any] | None:
        return next((item for item in self.images if item['id'] == self.selected_id), None)

    def _areas(self, item: dict[str, Any]) -> list[AnonymizationArea]:
        raw = item.setdefault('anonymization_areas', [])
        areas: list[AnonymizationArea] = []
        changed = False
        for value in raw:
            if isinstance(value, AnonymizationArea):
                areas.append(value)
            else:
                try:
                    areas.append(AnonymizationArea.from_dict(dict(value)))
                    changed = True
                except (TypeError, ValueError):
                    changed = True
        if changed:
            item['anonymization_areas'] = areas
        return areas

    def save(self) -> None:
        item = self.current()
        if not item:
            return
        editor: ImageEditor = item['editor']
        editor.confirm()
        final_path = render_annotations(
            item['treated'],
            self.workspace.export_dir / 'geradas' / item['id'] / 'imagem_final.png',
            editor.document.items,
        )
        item.update(annotated=final_path, final=final_path)
        self._saved_snapshots[item['id']] = deepcopy(editor.document.to_list())
        self.on_change()
        if self.panel:
            self.panel.refresh()
        if self.annotation_panel:
            self.annotation_panel.refresh()
        ui.notify(
            'Marcações salvas em imagem_final.png. imagem_tratada.png permanece sem marcações.',
            type='positive',
        )

    def cancel_changes(self) -> None:
        item = self.current()
        if not item:
            return
        snapshot = self._saved_snapshots.get(item['id'], [])
        item['editor'].document.items = [Annotation.from_dict(value) for value in snapshot]
        item['editor'].cancel()
        self.area_editor.cancel()
        self.selected_annotation_id = None
        if self.panel:
            self.panel.refresh()
        if self.annotation_panel:
            self.annotation_panel.refresh()
        ui.notify('Alterações não salvas canceladas.', type='info')

    def render(self) -> None:
        if not self.images:
            ui.label('Nenhuma imagem disponível para ajuste.').classes('text-slate-500')
            return
        options = {item['id']: item['filename'] for item in self.images}
        ui.select(
            options, value=self.selected_id, label='Imagem em edição',
            on_change=lambda event: self.select(str(event.value)),
        ).classes('w-96')
        ui.label(
            'Escolha uma forma, marque a imagem e salve. A imagem original será preservada.'
        ).classes('text-sm text-slate-600')

        selected_tool = {'value': 'arrow'}
        ui.label('Forma da marcação').classes('text-sm font-medium text-slate-700')
        with ui.row().classes('gap-2 flex-wrap items-center'):
            for key, label in SHAPE_OPTIONS.items():
                button = ui.button(
                    label, icon=SHAPE_ICONS[key],
                    on_click=lambda value=key: select_tool(value),
                ).props('outline')
                self._shape_buttons[key] = button
        anonymize_button = ui.button(
            'Marcar área para anonimização', icon='privacy_tip',
            on_click=lambda: select_tool('anonymize'),
        ).props('outline color=warning').classes('mt-2')

        controls: dict[str, Any] = {}
        groups: dict[str, Any] = {}

        def apply_button_state() -> None:
            active = selected_tool['value']
            for key, button in self._shape_buttons.items():
                if key == active:
                    button.props(remove='outline')
                    button.props('color=primary unelevated')
                else:
                    button.props(remove='unelevated color=primary')
                    button.props('outline')
            if active == 'anonymize':
                anonymize_button.props(remove='outline')
                anonymize_button.props('unelevated color=warning')
            else:
                anonymize_button.props(remove='unelevated')
                anonymize_button.props('outline color=warning')

        def select_tool(value: str) -> None:
            selected_tool['value'] = value
            item = self.current()
            if item:
                item['editor'].cancel()
            self.area_editor.cancel()
            apply_button_state()
            update_visibility()
            if self.panel:
                self.panel.refresh()

        with ui.column().classes('w-full gap-3'):
            with ui.row().classes('w-full gap-3 items-end flex-wrap') as general_group:
                controls['color'] = ui.color_input('Cor', value='#ef4444').classes('w-36')
                controls['width'] = ui.number('Espessura', value=4, min=1, max=30).classes('w-32')
                controls['opacity'] = ui.number('Transparência', value=92, min=5, max=100).classes('w-36')
            groups['general'] = general_group

            with ui.row().classes('w-full gap-3 items-end flex-wrap') as arrow_group:
                controls['arrow_head_size'] = ui.number('Tamanho da ponta', value=18, min=6, max=120).classes('w-40')
                controls['arrow_head_style'] = ui.select(
                    {'filled': 'Ponta preenchida', 'open': 'Ponta aberta'},
                    value='filled', label='Estilo da ponta',
                ).classes('w-48')
                controls['double_arrow'] = ui.checkbox('Seta dupla', value=False)
            groups['arrow'] = arrow_group

            with ui.row().classes('w-full gap-3 items-end flex-wrap') as fill_group:
                controls['border_color'] = ui.color_input('Cor da borda', value='#ef4444').classes('w-36')
                controls['fill_enabled'] = ui.checkbox('Preenchimento', value=False)
                controls['fill_color'] = ui.color_input('Cor do preenchimento', value='#fef08a').classes('w-48')
                controls['fill_opacity'] = ui.number('Transparência do preenchimento', value=25, min=0, max=100).classes('w-56')
            groups['fill'] = fill_group

            with ui.row().classes('w-full gap-3 items-end flex-wrap') as text_group:
                controls['text'] = ui.input('Conteúdo').classes('min-w-80 grow')
                controls['font_size'] = ui.number('Tamanho da fonte', value=28, min=8, max=160).classes('w-44')
                controls['bold'] = ui.checkbox('Negrito', value=False)
                controls['text_background_enabled'] = ui.checkbox('Fundo', value=True)
                controls['text_background'] = ui.color_input('Cor do fundo', value='#ffffff').classes('w-36')
            groups['text'] = text_group

            with ui.row().classes('w-full gap-3 items-end flex-wrap') as marker_group:
                controls['number'] = ui.number('Número', value=1, min=1, max=999).classes('w-28')
            groups['marker'] = marker_group

            with ui.row().classes('w-full gap-3 items-end flex-wrap') as anonymization_group:
                controls['area_type'] = ui.select(
                    {value: value for value in DATA_TYPES}, value='Cliente', label='Tipo do dado',
                ).classes('w-56')
                controls['replacement_text'] = ui.input('Texto de substituição').classes('min-w-72 grow')
                controls['area_note'] = ui.input('Observação opcional').classes('min-w-72 grow')
            groups['anonymization'] = anonymization_group

        def update_visibility() -> None:
            kind = selected_tool['value']
            groups['general'].set_visibility(kind != 'anonymize')
            groups['arrow'].set_visibility(kind in ARROW_KINDS)
            groups['fill'].set_visibility(kind in SHAPE_KINDS)
            groups['text'].set_visibility(kind in TEXT_KINDS)
            groups['marker'].set_visibility(kind == 'marker')
            groups['anonymization'].set_visibility(kind == 'anonymize')

        def opacity_value(name: str, default: float) -> float:
            raw = controls[name].value
            if raw is None:
                return default
            return min(1.0, max(0.0, float(raw) / 100))

        def values() -> dict[str, Any]:
            kind = selected_tool['value']
            color = str(controls['color'].value or '#ef4444')
            border_color = str(controls['border_color'].value or color)
            fill_enabled = bool(controls['fill_enabled'].value or kind == 'highlight')
            return {
                'kind': kind, 'color': color, 'width': int(controls['width'].value or 4),
                'font_size': int(controls['font_size'].value or 28),
                'text_background': str(controls['text_background'].value or '#ffffff'),
                'opacity': opacity_value('opacity', 0.92), 'text': str(controls['text'].value or ''),
                'number': int(controls['number'].value or 1), 'border_color': border_color,
                'fill_color': str(controls['fill_color'].value or '#fef08a'),
                'fill_enabled': fill_enabled, 'fill_opacity': opacity_value('fill_opacity', 0.25),
                'arrow_head_size': int(controls['arrow_head_size'].value or 18),
                'arrow_head_style': str(controls['arrow_head_style'].value or 'filled'),
                'double_arrow': bool(controls['double_arrow'].value),
                'bold': bool(controls['bold'].value),
                'text_background_enabled': bool(controls['text_background_enabled'].value),
            }

        def add_area(area: AnonymizationArea) -> None:
            item = self.current()
            if not item:
                return
            self._areas(item).append(area)
            self.selected_area_id = area.id
            self.on_change()
            if self.panel:
                self.panel.refresh()
            if self.anonymization_panel:
                self.anonymization_panel.refresh()
            ui.notify('Área de anonimização marcada. Nenhuma IA foi chamada.', type='positive')

        def add_draft() -> None:
            item = self.current()
            if item and item['editor'].confirm():
                if self.panel:
                    self.panel.refresh()
                if self.annotation_panel:
                    self.annotation_panel.refresh()
                ui.notify('Marcação adicionada. Clique em Salvar marcações.', type='positive')

        def cancel_draft() -> None:
            item = self.current()
            if item:
                item['editor'].cancel()
                self.area_editor.cancel()
                if self.panel:
                    self.panel.refresh()

        def undo() -> None:
            item = self.current()
            if item and item['editor'].document.undo():
                self.selected_annotation_id = None
                self.panel.refresh()
                self.annotation_panel.refresh()

        def redo() -> None:
            item = self.current()
            if item and item['editor'].document.redo():
                self.selected_annotation_id = None
                self.panel.refresh()
                self.annotation_panel.refresh()

        def clear() -> None:
            item = self.current()
            if item:
                item['editor'].document.clear()
                item['editor'].cancel()
                self.selected_annotation_id = None
                self.panel.refresh()
                self.annotation_panel.refresh()

        with ui.row().classes('gap-2 flex-wrap'):
            ui.button('Adicionar marcação', icon='add', on_click=add_draft)
            ui.button('Cancelar rascunho', icon='close', on_click=cancel_draft).props('outline')
            ui.button('Desfazer', icon='undo', on_click=undo).props('outline')
            ui.button('Refazer', icon='redo', on_click=redo).props('outline')
            ui.button('Remover todas', icon='layers_clear', on_click=clear).props('outline color=negative')
            ui.button('Salvar marcações', icon='save', on_click=self.save)
            ui.button('Cancelar alterações', icon='cancel', on_click=self.cancel_changes).props('outline')

        @ui.refreshable
        def panel() -> None:
            item = self.current()
            if not item:
                return
            editor: ImageEditor = item['editor']
            preview = self.workspace.temp_dir / item['id'] / 'ajuste_avancado.png'
            render_anonymization_preview(
                item['treated'], preview, editor.preview_items(), self._areas(item), self.area_editor.draft,
            )

            def click(event: events.MouseEventArguments) -> None:
                width, height = item['size']
                x = float(event.image_x) / width
                y = float(event.image_y) / height
                if selected_tool['value'] == 'anonymize':
                    message = self.area_editor.click(
                        x, y,
                        data_type=str(controls['area_type'].value or 'Texto personalizado'),
                        replacement_text=str(controls['replacement_text'].value or ''),
                        note=str(controls['area_note'].value or ''),
                    )
                    area = self.area_editor.confirm()
                    if area:
                        add_area(area)
                    else:
                        panel.refresh()
                        ui.notify(message, type='info')
                    return
                message = editor.click(x, y, **values())
                panel.refresh()
                ui.notify(message, type='info')

            preview_image = ui.interactive_image(
                str(preview), on_mouse=click, events=['mousedown'],
            ).classes('w-full max-h-[680px] object-contain bg-slate-100')
            preview_image.force_reload()
            ui.label(
                'Seta, linha, retângulo, círculo/elipse, destaque, seta com texto e áreas de anonimização exigem dois cliques. Texto e número exigem um clique.'
            ).classes('text-sm text-slate-500')

        @ui.refreshable
        def annotation_panel() -> None:
            item = self.current()
            if not item:
                return
            editor: ImageEditor = item['editor']
            annotations = editor.document.items
            if not annotations:
                ui.label('Nenhuma marcação adicionada.').classes('text-sm text-slate-500')
                return
            annotation_options = {
                annotation.id: f'{index}. {SHAPE_OPTIONS.get(annotation.kind, annotation.kind)}'
                for index, annotation in enumerate(annotations, 1)
            }
            selected = self.selected_annotation_id
            if selected not in annotation_options:
                selected = annotations[0].id
                self.selected_annotation_id = selected
            annotation = editor.selected(selected)
            if not annotation:
                return

            def choose(event: events.ValueChangeEventArguments) -> None:
                self.selected_annotation_id = str(event.value)
                annotation_panel.refresh()

            ui.select(annotation_options, value=selected, label='Marcação existente', on_change=choose).classes('w-96')
            with ui.row().classes('w-full gap-3 items-end'):
                edit_color = ui.color_input('Cor da marcação', value=annotation.color).classes('w-40')
                edit_text = ui.input('Texto', value=annotation.text).classes('grow')
                edit_number = ui.number('Número', value=annotation.number, min=1, max=999).classes('w-28')

            def update_annotation() -> None:
                if editor.update_selected(
                    selected, color=str(edit_color.value), border_color=str(edit_color.value),
                    text=str(edit_text.value or ''), number=int(edit_number.value or 1),
                ):
                    panel.refresh()
                    annotation_panel.refresh()

            def move(dx: float, dy: float) -> None:
                if editor.move_selected(selected, dx, dy):
                    panel.refresh()
                    annotation_panel.refresh()

            def delete() -> None:
                if editor.document.delete(selected):
                    self.selected_annotation_id = None
                    panel.refresh()
                    annotation_panel.refresh()

            with ui.row().classes('gap-2 flex-wrap'):
                ui.button('Aplicar texto e cor', icon='edit', on_click=update_annotation).props('outline')
                ui.button(icon='arrow_upward', on_click=lambda: move(0, -0.01)).props('outline').tooltip('Mover para cima')
                ui.button(icon='arrow_downward', on_click=lambda: move(0, 0.01)).props('outline').tooltip('Mover para baixo')
                ui.button(icon='arrow_back', on_click=lambda: move(-0.01, 0)).props('outline').tooltip('Mover para a esquerda')
                ui.button(icon='arrow_forward', on_click=lambda: move(0.01, 0)).props('outline').tooltip('Mover para a direita')
                ui.button('Excluir marcação', icon='delete', on_click=delete).props('outline color=negative')

        @ui.refreshable
        def anonymization_panel() -> None:
            item = self.current()
            if not item:
                return
            areas = self._areas(item)
            ui.label('Áreas para anonimização').classes('text-sm font-medium text-slate-700')
            if not areas:
                ui.label('Nenhuma área marcada.').classes('text-sm text-slate-500')
            else:
                area_options = {
                    area.id: f'{index}. {area.data_type}'
                    for index, area in enumerate(areas, 1)
                }
                selected = self.selected_area_id if self.selected_area_id in area_options else areas[0].id
                self.selected_area_id = selected
                area = next((value for value in areas if value.id == selected), None)
                if area:
                    def choose_area(event: events.ValueChangeEventArguments) -> None:
                        self.selected_area_id = str(event.value)
                        anonymization_panel.refresh()

                    ui.select(area_options, value=selected, label='Área marcada', on_change=choose_area).classes('w-96')
                    with ui.row().classes('w-full gap-3 items-end flex-wrap'):
                        edit_type = ui.select({value: value for value in DATA_TYPES}, value=area.data_type, label='Tipo do dado').classes('w-56')
                        edit_replacement = ui.input('Texto de substituição', value=area.replacement_text).classes('min-w-72 grow')
                        edit_note = ui.input('Observação opcional', value=area.note).classes('min-w-72 grow')

                    def update_area() -> None:
                        area.data_type = str(edit_type.value or 'Texto personalizado')
                        area.replacement_text = str(edit_replacement.value or '').strip()
                        area.note = str(edit_note.value or '').strip()
                        self.on_change()
                        panel.refresh()
                        anonymization_panel.refresh()

                    def delete_area() -> None:
                        item['anonymization_areas'] = [value for value in areas if value.id != selected]
                        self.selected_area_id = None
                        self.on_change()
                        panel.refresh()
                        anonymization_panel.refresh()

                    with ui.row().classes('gap-2 flex-wrap'):
                        ui.button('Visualizar', icon='visibility', on_click=panel.refresh).props('outline')
                        ui.button('Aplicar dados da área', icon='edit', on_click=update_area).props('outline')
                        ui.button('Excluir área', icon='delete', on_click=delete_area).props('outline color=negative')

            async def run_anonymization() -> None:
                if self.on_anonymize and self.current():
                    await self.on_anonymize(self.current())

            process_button = ui.button(
                'Anonimizar áreas marcadas com IA', icon='auto_fix_high', on_click=run_anonymization,
            )
            if not areas or not self.on_anonymize or not self.can_anonymize():
                process_button.disable()

        self.panel = panel
        self.annotation_panel = annotation_panel
        self.anonymization_panel = anonymization_panel
        apply_button_state()
        update_visibility()
        panel()
        annotation_panel()
        anonymization_panel()

    def select(self, image_id: str) -> None:
        self.selected_id = image_id
        self.selected_annotation_id = None
        self.selected_area_id = None
        self.area_editor.cancel()
        item = self.current()
        if item:
            item['editor'].cancel()
        if self.panel:
            self.panel.refresh()
        if self.annotation_panel:
            self.annotation_panel.refresh()
        if self.anonymization_panel:
            self.anonymization_panel.refresh()
