from __future__ import annotations

from typing import Any, Callable

from nicegui import ui

from models.composition import CompositionDocument
from services.html_export_service import render_inline


def render_novelty_preview(
    document: CompositionDocument,
    images: list[dict[str, Any]],
    on_change: Callable[[], None] | None = None,
    on_edit_image: Callable[[str], None] | None = None,
) -> None:
    by_id = {str(item['id']): item for item in images}
    dragged: dict[str, str | None] = {'block_id': None}

    def start_drag(block_id: str) -> None:
        dragged['block_id'] = block_id

    def drop_image(target: int) -> None:
        block_id = dragged.get('block_id')
        dragged['block_id'] = None
        if not block_id:
            return
        current = document.index(block_id)
        if current is None:
            return
        destination = target - 1 if current < target else target
        if document.move_image_block(block_id, destination) and on_change:
            on_change()

    def drop_zone(target: int) -> None:
        if on_change is None:
            return
        zone = ui.element('div').classes(
            'w-full h-3 border border-dashed border-transparent '
            'hover:border-blue-400 hover:bg-blue-50 rounded'
        )
        zone.tooltip('Solte a imagem aqui')
        zone.on('dragover', js_handler='(event) => event.preventDefault()')
        zone.on(
            'drop',
            lambda target_index=target: drop_image(target_index),
            js_handler='(event) => { event.preventDefault(); emit(); }',
        )
    with ui.column().classes('w-full max-w-4xl mx-auto bg-white px-10 py-10 gap-0'):
        cover = document.cover
        if cover.get('status') == 'gerada' and cover.get('arquivo'):
            ui.image(str(cover['arquivo'])).classes(
                'w-full max-h-[520px] object-cover rounded-xl shadow-sm mb-8'
            )
        for order, block in enumerate(document.blocks):
            drop_zone(order)
            if block.tipo == 'titulo':
                ui.label(block.conteudo).classes('text-4xl font-bold leading-tight mb-6')
            elif block.tipo == 'subtitulo':
                ui.label(block.conteudo).classes('text-2xl font-bold mt-8 mb-3')
            elif block.tipo == 'texto':
                if block.papel == 'caminho':
                    lines = block.conteudo.splitlines()
                    with ui.column().classes('w-full gap-1 my-4'):
                        ui.label(lines[0] if lines else '').classes(
                            'text-base font-bold text-slate-800'
                        )
                        if len(lines) > 1:
                            ui.label(' / '.join(lines[1:])).classes(
                                'text-base font-bold text-slate-700'
                            )
                    continue
                classes = 'text-lg leading-8 whitespace-pre-line mb-5 text-slate-700'
                if block.papel == 'introducao':
                    classes += ' text-xl text-slate-600'
                paragraphs = __import__('re').split(r'\n\s*\n', block.conteudo)
                for paragraph in paragraphs:
                    if paragraph.strip():
                        ui.html(render_inline(
                            paragraph.strip(), block.destaques,
                        )).classes(classes)
            elif block.tipo == 'lista':
                with ui.element('ul').classes('list-disc pl-8 mb-5 text-lg leading-8 text-slate-700'):
                    for item in block.itens:
                        with ui.element('li'):
                            ui.html(render_inline(
                                str(item.get('texto') or ''),
                                list(item.get('destaques') or []),
                            ))
            elif block.tipo == 'divisor':
                ui.separator().classes('my-7')
            elif block.tipo == 'imagem':
                image_ids = block.image_ids
                container = ui.column().classes(
                    'w-full cursor-move' if on_change else 'w-full'
                )
                if on_change:
                    container.props('draggable=true')
                    container.on(
                        'dragstart',
                        lambda value=block.id: start_drag(value),
                        js_handler=(
                            '(event) => { event.dataTransfer.effectAllowed = "move"; emit(); }'
                        ),
                    )
                with container, ui.row().classes(
                    'w-full justify-center gap-5 my-6 no-wrap'
                    if block.modo_exibicao == 'lado_a_lado'
                    else 'w-full justify-center my-6'
                ):
                    for image_id in image_ids:
                        item = by_id.get(image_id)
                        if not item:
                            continue
                        path = (
                            item.get('final') or item.get('annotated')
                            or item.get('treated') or item.get('original')
                        )
                        container_classes = 'w-4/5 items-center'
                        image_classes = 'w-full max-h-[640px] object-contain'
                        if block.modo_exibicao == 'ampliado':
                            container_classes = 'w-full items-center'
                            image_classes = 'w-full max-h-[800px] object-contain'
                        elif block.modo_exibicao == 'lado_a_lado':
                            container_classes = 'w-1/2 items-center'
                            image_classes = 'w-full max-h-[560px] object-contain'
                        with ui.column().classes(container_classes):
                            ui.image(str(path)).classes(image_classes)
                            if on_edit_image:
                                ui.button(
                                    'Editar imagem', icon='edit',
                                    on_click=lambda value=image_id: on_edit_image(value),
                                ).props('outline dense').classes('mt-2')
                if block.legenda:
                    ui.label(block.legenda).classes(
                        'w-full text-center text-sm italic text-slate-500 mb-4'
                    )
        drop_zone(len(document.blocks))
