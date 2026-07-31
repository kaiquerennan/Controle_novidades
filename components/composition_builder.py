from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from nicegui import ui

from models.composition import CompositionDocument


class CompositionBuilder:
    def __init__(
        self,
        document: CompositionDocument,
        get_images: Callable[[], list[dict[str, Any]]],
        on_change: Callable[[], None],
        *,
        on_review_text: Callable[[], Any] | None = None,
        on_suggest_positions: Callable[[], Any] | None = None,
        show_preview: bool = True,
    ) -> None:
        self.document = document
        self.get_images = get_images
        self.on_change = on_change
        self.on_review_text = on_review_text
        self.on_suggest_positions = on_suggest_positions
        self.show_preview = show_preview
        self.dragged_block_id: str | None = None
        self.dragged_image_id: str | None = None
        self.insert_after: str | None = None
        self._preview: Any = None
        self._editor: Any = None
        self._available: Any = None

    def image(self, image_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.get_images() if item['id'] == image_id), None
        )

    def image_path(self, image_id: str) -> Path | None:
        item = self.image(image_id)
        if not item:
            return None
        return item['final'] or item['annotated'] or item['treated']

    def changed(self, result: bool = True) -> None:
        if not result:
            return
        self.on_change()
        self.refresh()

    def refresh(self) -> None:
        for refreshable in (self._preview, self._editor, self._available):
            if refreshable:
                refreshable.refresh()

    def apply_text(self, text: str) -> None:
        self.document.replace_main_text(text)
        self.changed()

    def add_block(self, tipo: str) -> None:
        self.document.add(tipo)
        self.changed()

    def insert_image(self, image_id: str, target: int | None = None) -> None:
        all_ids = [item['id'] for item in self.get_images()]
        if image_id not in self.document.available_image_ids(all_ids):
            return
        if target is None:
            after = self.document.index(self.insert_after or '')
            target = len(self.document.blocks) if after is None else after + 1
        self.document.add('imagem', index=target, imagem_id=image_id)
        self.insert_after = None
        self.dragged_image_id = None
        self.dragged_block_id = None
        self.changed()

    def drop(self, target: int) -> None:
        if self.dragged_block_id:
            current = self.document.index(self.dragged_block_id)
            if current is not None:
                destination = target - 1 if current < target else target
                self.document.move(self.dragged_block_id, destination)
        elif self.dragged_image_id:
            self.insert_image(self.dragged_image_id, target)
            return
        self.dragged_block_id = None
        self.dragged_image_id = None
        self.changed()

    def drop_zone(self, target: int) -> None:
        zone = ui.element('div').classes(
            'w-full h-4 border border-dashed border-slate-300 rounded hover:bg-blue-50'
        )
        zone.tooltip('Solte um bloco ou uma imagem aqui')
        zone.on('dragover', js_handler='(event) => event.preventDefault()')
        zone.on(
            'drop',
            lambda target_index=target: self.drop(target_index),
            js_handler='(event) => { event.preventDefault(); emit(); }',
        )

    def render(self) -> None:
        with ui.row().classes('w-full gap-2 items-center'):
            for label, icon, tipo in (
                ('Adicionar título', 'title', 'titulo'),
                ('Criar subtítulo', 'subtitles', 'subtitulo'),
                ('Adicionar texto', 'notes', 'texto'),
                ('Adicionar lista', 'format_list_bulleted', 'lista'),
                ('Adicionar lista', 'format_list_bulleted', 'lista'),
                ('Adicionar divisor', 'horizontal_rule', 'divisor'),
            ):
                ui.button(
                    label, icon=icon,
                    on_click=lambda value=tipo: self.add_block(value),
                ).props('outline')
            ui.button(
                'Desfazer última alteração', icon='undo',
                on_click=lambda: self.changed(self.document.undo()),
            ).props('outline')
            if self.on_review_text:
                ui.button(
                    'REVISAR TEXTO COM IA', icon='spellcheck',
                    on_click=self.on_review_text,
                ).props('outline')
            if self.on_suggest_positions:
                ui.button(
                    'SUGERIR POSICIONAMENTO COM IA', icon='auto_awesome',
                    on_click=self.on_suggest_positions,
                ).props('outline')

        @ui.refreshable
        def preview() -> None:
            ui.label('PRÉ-VISUALIZAÇÃO DA NOVIDADE').classes('font-bold text-lg mt-5')
            with ui.card().classes(
                'w-full max-w-5xl mx-auto px-10 py-9 bg-white shadow-sm '
                'border border-slate-200'
            ):
                if not self.document.blocks:
                    ui.label('A composição está vazia.').classes('muted')
                for block in self.document.blocks:
                    if block.tipo == 'titulo':
                        ui.label(block.conteudo or 'Título da novidade').classes(
                            'text-3xl font-bold leading-tight mt-2 mb-4'
                        )
                    elif block.tipo == 'subtitulo':
                        ui.label(block.conteudo or 'Subtítulo').classes(
                            'text-xl font-bold mt-7 mb-2'
                        )
                    elif block.tipo == 'lista':
                        list_value = ui.textarea(
                            'Itens, um por linha',
                            value='\n'.join(str(item.get('texto') or '') for item in block.itens),
                        ).props('rows=4').classes('w-full')
                        list_value.on(
                            'blur',
                            lambda value=block.id, control=list_value: self.changed(
                                self.document.update(
                                    value, itens=[
                                        {'texto': line.strip(), 'destaques': []}
                                        for line in str(control.value or '').splitlines()
                                        if line.strip()
                                    ],
                                )
                            ),
                        )
                    elif block.tipo == 'texto':
                        ui.label(block.conteudo or 'Texto do bloco').classes(
                            'text-base leading-7 whitespace-pre-line mb-4 '
                            + ('font-bold' if block.papel == 'caminho' else '')
                        )
                    elif block.tipo == 'lista':
                        with ui.element('ul').classes('list-disc pl-8 mb-4'):
                            for item in block.itens:
                                ui.label(str(item.get('texto') or ''))
                    elif block.tipo == 'lista':
                        with ui.element('ul').classes('list-disc pl-8 mb-4'):
                            for item in block.itens:
                                ui.label(str(item.get('texto') or ''))
                    elif block.tipo == 'divisor':
                        ui.separator().classes('my-6')
                    elif block.tipo == 'imagem':
                        paths = [self.image_path(block.imagem_id or '')]
                        if block.imagem_secundaria_id:
                            paths.append(self.image_path(block.imagem_secundaria_id))
                        row_class = (
                            'w-full justify-center gap-4 my-5 no-wrap'
                            if block.modo_exibicao == 'lado_a_lado'
                            else 'w-full justify-center my-5'
                        )
                        with ui.row().classes(row_class):
                            for path in paths:
                                if not path:
                                    continue
                                image_class = 'w-4/5 max-h-[600px] object-contain'
                                if block.modo_exibicao == 'ampliado':
                                    image_class = 'w-full max-h-[760px] object-contain'
                                elif block.modo_exibicao == 'lado_a_lado':
                                    image_class = 'w-1/2 max-h-[560px] object-contain'
                                ui.image(str(path)).classes(image_class)
                        if block.legenda:
                            ui.label(block.legenda).classes(
                                'text-sm text-center italic muted mb-3'
                            )

        if self.show_preview:
            self._preview = preview
            preview()

        @ui.refreshable
        def editor() -> None:
            ui.label('BLOCOS DA COMPOSIÇÃO').classes('font-bold text-lg mt-6')
            if not self.document.blocks:
                self.drop_zone(0)
                ui.label('Adicione um bloco para começar.').classes('muted')
                return
            for order, block in enumerate(self.document.blocks, 1):
                self.drop_zone(order - 1)
                card = ui.card().classes(
                    'w-full p-4 border border-slate-200 cursor-move'
                ).props('draggable=true')
                card.on(
                    'dragstart',
                    lambda value=block.id: self._start_block_drag(value),
                    js_handler=(
                        '(event) => { event.dataTransfer.effectAllowed = "move"; emit(); }'
                    ),
                )
                with card:
                    with ui.row().classes('w-full items-center justify-between'):
                        ui.badge(f'{order}. {block.tipo.upper()}')
                        actions = (
                            ('first_page', lambda value=block.id: self.document.move_to_start(value), 'Mover para o início'),
                            ('arrow_upward', lambda value=block.id: self.document.move_by(value, -1), 'Mover para cima'),
                            ('arrow_downward', lambda value=block.id: self.document.move_by(value, 1), 'Mover para baixo'),
                            ('last_page', lambda value=block.id: self.document.move_to_end(value), 'Mover para o final'),
                            ('delete_outline', lambda value=block.id: self.document.delete(value), 'Excluir da composição'),
                        )
                        with ui.row().classes('gap-1'):
                            for icon, action, tooltip in actions:
                                ui.button(
                                    icon=icon,
                                    on_click=lambda callback=action: self.changed(callback()),
                                ).props('flat dense').tooltip(tooltip)
                    if block.tipo in {'titulo', 'subtitulo'}:
                        field = ui.input('Conteúdo', value=block.conteudo).classes('w-full')
                        field.on(
                            'blur',
                            lambda value=block.id, control=field: self.changed(
                                self.document.update(value, conteudo=control.value or '')
                            ),
                        )
                    elif block.tipo == 'lista':
                        list_value = ui.textarea(
                            'Itens, um por linha',
                            value='\n'.join(str(item.get('texto') or '') for item in block.itens),
                        ).props('rows=4').classes('w-full')
                        list_value.on(
                            'blur',
                            lambda value=block.id, control=list_value: self.changed(
                                self.document.update(
                                    value, itens=[
                                        {'texto': line.strip(), 'destaques': []}
                                        for line in str(control.value or '').splitlines()
                                        if line.strip()
                                    ],
                                )
                            ),
                        )
                    elif block.tipo == 'texto':
                        field = ui.textarea('Conteúdo', value=block.conteudo).props(
                            'rows=4'
                        ).classes('w-full')
                        field.on(
                            'blur',
                            lambda value=block.id, control=field: self.changed(
                                self.document.update(value, conteudo=control.value or '')
                            ),
                        )
                        with ui.row().classes('gap-2'):
                            ui.button(
                                'Dividir texto', icon='call_split',
                                on_click=lambda value=block.id: self.changed(
                                    self.document.split_text(value)
                                ),
                            ).props('outline dense')
                            ui.button(
                                'Juntar com próximo texto', icon='merge',
                                on_click=lambda value=block.id: self.changed(
                                    self.document.join_with_next(value)
                                ),
                            ).props('outline dense')
                            ui.button(
                                'Inserir imagem após', icon='add_photo_alternate',
                                on_click=lambda value=block.id: self.select_insertion(value),
                            ).props('outline dense')
                    elif block.tipo == 'imagem':
                        self._render_image_controls(block)
            self.drop_zone(len(self.document.blocks))

        self._editor = editor
        editor()

        @ui.refreshable
        def available() -> None:
            ui.label('IMAGENS DISPONÍVEIS').classes('font-bold text-lg mt-6')
            after = self.document.find(self.insert_after or '')
            if after:
                ui.label(
                    f'A próxima imagem será inserida após o bloco {after.tipo}.'
                ).classes('text-sm text-blue-700')
            available_ids = self.document.available_image_ids(
                [item['id'] for item in self.get_images()]
            )
            if not available_ids:
                ui.label('Todas as imagens estão posicionadas.').classes('muted')
                return
            with ui.row().classes('w-full gap-3 no-wrap overflow-x-auto'):
                for image_id in available_ids:
                    item = self.image(image_id)
                    if not item:
                        continue
                    card = ui.card().classes('w-60 min-w-60 p-3 cursor-move').props(
                        'draggable=true'
                    )
                    card.on(
                        'dragstart',
                        lambda value=image_id: self._start_image_drag(value),
                        js_handler=(
                            '(event) => { event.dataTransfer.effectAllowed = "move"; emit(); }'
                        ),
                    )
                    with card:
                        ui.image(str(self.image_path(image_id))).classes(
                            'w-full h-28 object-contain bg-slate-100'
                        )
                        ui.label(item['filename']).classes('truncate font-medium')
                        ui.badge(
                            item['status'], color='negative' if item['error'] else 'primary'
                        )
                        ui.label(str(
                            item.get('placement_status')
                            or 'Dispon\u00edvel para posicionamento manual.'
                        )).classes(
                            'text-xs muted'
                        )
                        ui.button(
                            'Inserir na composição', icon='playlist_add',
                            on_click=lambda value=image_id: self.insert_image(value),
                        ).props('outline dense')

        self._available = available
        available()

    def _start_block_drag(self, block_id: str) -> None:
        self.dragged_block_id = block_id
        self.dragged_image_id = None

    def _start_image_drag(self, image_id: str) -> None:
        self.dragged_image_id = image_id
        self.dragged_block_id = None

    def select_insertion(self, block_id: str) -> None:
        self.insert_after = block_id
        if self._available:
            self._available.refresh()

    def _render_image_controls(self, block: Any) -> None:
        item = self.image(block.imagem_id or '')
        if item:
            ui.image(str(self.image_path(item['id']))).classes(
                'w-64 max-h-44 object-contain bg-slate-100'
            )
            ui.label(item['filename']).classes('font-medium')
        caption = ui.input('Legenda', value=block.legenda).classes('w-full')
        caption.on(
            'blur',
            lambda value=block.id, control=caption: self.changed(
                self.document.update(value, legenda=control.value or '')
            ),
        )
        ui.select(
            {'normal': 'Normal', 'ampliado': 'Ampliado', 'lado_a_lado': 'Lado a lado'},
            value=block.modo_exibicao,
            label='Modo de exibição',
            on_change=lambda event, value=block.id: self.changed(
                self.document.update(value, modo_exibicao=str(event.value))
            ),
        ).classes('w-64')
        if block.modo_exibicao == 'lado_a_lado':
            available_ids = self.document.available_image_ids(
                [item['id'] for item in self.get_images()]
            )
            if block.imagem_secundaria_id:
                available_ids.insert(0, block.imagem_secundaria_id)
            options = {
                item['id']: item['filename']
                for item in self.get_images() if item['id'] in available_ids
            }
            ui.select(
                options,
                value=block.imagem_secundaria_id,
                label='Segunda imagem',
                on_change=lambda event, value=block.id: self.changed(
                    self.document.update(
                        value,
                        imagem_secundaria_id=event.value,
                        modo_exibicao='lado_a_lado',
                    )
                ),
            ).classes('w-80')
        if block.confianca is not None:
            ui.label(f'Confiança da sugestão: {block.confianca:.0%}').classes('text-sm')
        if block.motivo:
            ui.label(block.motivo).classes('text-sm muted')
