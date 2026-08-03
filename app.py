from __future__ import annotations

from hashlib import sha256
import json
import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

def _ensure_project_venv() -> None:
    project_root = Path(__file__).resolve().parent
    expected_prefix = (project_root / '.venv').resolve()
    if Path(sys.prefix).resolve() == expected_prefix:
        return
    expected_python = expected_prefix / 'Scripts' / 'python.exe'
    if not expected_python.is_file():
        raise SystemExit(
            f'O ambiente virtual do projeto não foi encontrado em {expected_prefix}. '
            'Crie a .venv e execute instalar_dependencias.bat.'
        )
    os.execve(
        str(expected_python),
        [str(expected_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        os.environ.copy(),
    )


if __name__ in {'__main__', '__mp_main__'}:
    _ensure_project_venv()


from nicegui import app as ui_app, events, run, ui
from PIL import Image

from components.image_editor import ImageEditor
from components.novelty_preview import render_novelty_preview
from components.occurrence_menu import render_occurrence_menu
from components.novelty_history_panel import render_novelty_history
from core.config import (
    API_KEY_ENVIRONMENTS,
    IMAGE_MODELS,
    PROVIDER_LABELS,
    TEXT_MODELS,
)
from core.config.environment import ENV_PATH
from models.annotation import Annotation
from models.composition import CompositionDocument
from services.image_service import (
    PROJECT_ROOT,
    SessionWorkspace,
    build_composition_exports,
    safe_path,
)
from services.editorial_orchestration_service import (
    EditorialOrchestrator,
    WRITER_ORGANIZER_PROMPT,
)
from services.html_export_service import build_novelty_html
from services.plain_text_composition import apply_plain_text, composition_to_plain_text
from components.whatsapp_panel import render_whatsapp_panel
from services.whatsapp_gateway_service import (
    WhatsAppGatewayError,
    WhatsAppGatewayService,
    mask_phone,
)
from services.browser_session_service import resolve_storage_secret, session_state_path
from services.generation_control_service import GENERATION_REGISTRY
from services.occurrence_query_service import OccurrenceSummary
from services.providers import ProviderError, ProviderFactory
from services.providers.exceptions import sanitize_technical_detail
from services.screen_state_service import ScreenStateStore

APP_NAME = 'EMC Central de Novidades'
PORT = 8510
LOG_DIR = safe_path(PROJECT_ROOT / 'logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.FileHandler(LOG_DIR / 'aplicativo.log', encoding='utf-8')],
)
logger = logging.getLogger(APP_NAME)


def _now_iso() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def _cover_file_from_composition(composition_payload: dict[str, Any]) -> str:
    cover = dict(composition_payload.get('cover') or {})
    return str(cover.get('arquivo') or '')


def copy_as_png(source: Path, destination: Path) -> Path:
    source = safe_path(source)
    destination = safe_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.convert('RGB').save(destination, 'PNG')
    return destination


def _serialized_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            'id': str(item['id']),
            'filename': str(item.get('filename') or ''),
            'original': str(item['original']),
            'treated': str(item.get('treated') or item['original']),
            'annotated': str(item['annotated']) if item.get('annotated') else '',
            'final': str(item['final']) if item.get('final') else '',
            'size': list(item.get('size') or (0, 0)),
            'status': str(item.get('status') or ''),
            'error': str(item.get('error') or ''),
            'annotations': item['editor'].document.to_list(),
        }
        for item in images
    ]


def _restored_images(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    restored: list[dict[str, Any]] = []
    for value in values:
        try:
            original = safe_path(Path(str(value['original'])))
            if not original.is_file():
                continue
            with Image.open(original) as opened:
                detected_size = opened.size
            editor = ImageEditor()
            editor.document.items = [
                Annotation.from_dict(item) for item in value.get('annotations') or []
            ]
            paths: dict[str, Path | None] = {}
            for key in ('treated', 'annotated', 'final'):
                raw_path = str(value.get(key) or '')
                candidate = safe_path(Path(raw_path)) if raw_path else None
                paths[key] = candidate if candidate and candidate.is_file() else None
            restored.append({
                'id': str(value['id']),
                'filename': str(value.get('filename') or original.name),
                'original': original,
                'treated': paths['treated'] or original,
                'annotated': paths['annotated'],
                'final': paths['final'],
                'size': tuple(value.get('size') or detected_size),
                'status': str(value.get('status') or 'Pronta'),
                'error': str(value.get('error') or ''),
                'editor': editor,
                'selected_id': None,
            })
        except (KeyError, OSError, TypeError, ValueError):
            continue
    return restored

@ui.page('/')
def index() -> None:
    # Cada navegador tem a propria sessao: o id fica em um cookie assinado, e o
    # estado da tela vai para um arquivo separado. Sem isso, dois usuarios no
    # mesmo servidor compartilhariam texto, imagens e configuracoes.
    stored_session = str(ui_app.storage.user.get('session_id') or '')
    try:
        workspace = SessionWorkspace.open(stored_session)
    except ValueError:
        workspace = SessionWorkspace.create()
    ui_app.storage.user['session_id'] = workspace.session_id
    state_store = ScreenStateStore(path=session_state_path(workspace.session_id))
    saved = state_store.load() or {}
    if str(saved.get('session_id') or '') != workspace.session_id:
        saved = {}
    try:
        composition = CompositionDocument.from_dict(dict(saved.get('composition') or {}))
    except (TypeError, ValueError):
        composition = CompositionDocument()
        saved = {}
    editorial_orchestrator = EditorialOrchestrator()
    whatsapp_gateway = WhatsAppGatewayService()
    saved_settings = dict(saved.get('settings') or {})
    saved_generation = dict(saved.get('generation') or {})
    if saved_generation.get('status') == 'completed' and saved_generation.get('composition'):
        try:
            composition = CompositionDocument.from_dict(
                dict(saved_generation.get('composition') or {})
            )
        except (TypeError, ValueError):
            logger.warning(
                '[PROCESSAMENTO] Composição persistida da geração não pôde ser restaurada; sessao=%s',
                workspace.session_id,
            )
    state: dict[str, Any] = {
        'images': _restored_images(list(saved.get('images') or [])),
        'generated': bool(saved.get('generated') or saved_generation.get('status') == 'completed'),
        'approved': bool(saved.get('approved')),
        'exports': {},
        'generation_error': '',
        'generation_error_details': {},
        'annotation_image_id': None,
        'placement_suggestions': [],
        'generation_in_progress': False,
        'active_generation_id': str(saved_generation.get('generation_id') or ''),
        'active_idempotency_key': str(saved_generation.get('idempotency_key') or ''),
        'current_step': str(saved.get('current_step') or 'Adicionar conteúdo'),
        'generation': saved_generation,
        'whatsapp_sending': False,
        'html_override': str(saved.get('html_override') or ''),
        'editing_text': False,
    }
    controls: dict[str, Any] = {}
    generation_lock = GENERATION_REGISTRY.lock_for(workspace.session_id)

    def generation_fingerprint() -> str:
        image_metadata = []
        for item in state['images']:
            path = Path(item['original'])
            try:
                stat = path.stat()
                signature = [str(path), stat.st_size, stat.st_mtime_ns]
            except OSError:
                signature = [str(path), 0, 0]
            image_metadata.append([str(item['id']), *signature])
        payload = {
            'provider': str(controls['provider'].value or ''),
            'text_model': str(controls['text_model'].value or ''),
            'image_model': str(controls['image_model'].value or ''),
            'raw_text': str(controls['raw_text'].value or '').strip(),
            'cover': bool(controls['cover'].value),
            'images': image_metadata,
        }
        serialized = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        return sha256(serialized.encode('utf-8')).hexdigest()

    def persist_screen_state(*, step: str | None = None) -> None:
        if step:
            state['current_step'] = step
        if not controls:
            return
        state_store.save({
            'session_id': workspace.session_id,
            'raw_text': str(controls['raw_text'].value or ''),
            'images': _serialized_images(state['images']),
            'composition': composition.to_dict(),
            'generated': bool(state['generated']),
            'approved': bool(state['approved']),
            'current_step': state['current_step'],
            'generation': dict(state.get('generation') or {}),
            'html_override': str(state.get('html_override') or ''),
            'settings': {
                'provider': str(controls['provider'].value or 'gemini'),
                'text_model': str(controls['text_model'].value or ''),
                'image_model': str(controls['image_model'].value or ''),
                'cover_requested': bool(controls['cover'].value),
            },
        })

    def update_generation_state(**fields: Any) -> dict[str, Any]:
        generation = dict(state.get('generation') or {})
        generation.update(fields)
        state['generation'] = generation
        if generation.get('generation_id'):
            state['active_generation_id'] = str(generation.get('generation_id') or '')
        if generation.get('idempotency_key'):
            state['active_idempotency_key'] = str(generation.get('idempotency_key') or '')
        return generation

    def persist_generation_state(**fields: Any) -> dict[str, Any]:
        generation = update_generation_state(**fields)
        persist_screen_state()
        return generation

    def persisted_generation_status() -> str:
        return str((state.get('generation') or {}).get('status') or '')

    def active_generation_matches(generation_id: str | None = None) -> bool:
        active = GENERATION_REGISTRY.active_generation(workspace.session_id)
        if not active:
            return False
        if generation_id:
            return active.generation_id == generation_id
        return True

    def safe_visual_update(description: str, action: Any) -> bool:
        try:
            action()
            return True
        except RuntimeError as ui_exc:
            logger.info(
                '[PROCESSAMENTO] Atualização visual ignorada; sessao=%s acao=%s motivo=%s',
                workspace.session_id, description, ui_exc,
            )
            return False

    def clear_current_novelty() -> None:
        state_store.clear()
        workspace.clear()
        new_novelty_dialog.close()
        ui.navigate.reload()

    with ui.dialog() as new_novelty_dialog, ui.card():
        ui.label('Iniciar uma nova novidade?').classes('text-xl font-bold')
        ui.label(
            'O texto, as imagens, as marcações e a matéria atual serão removidos.'
        )
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('Cancelar', on_click=new_novelty_dialog.close).props('flat')
            ui.button(
                'Limpar e iniciar', icon='delete_sweep', on_click=clear_current_novelty
            ).props('color=negative')

    ui.add_head_html('''
        <style>
          body { background: #f5f7fb; color: #172033; }
          .simple-card { border: 1px solid #e4e9f2; border-radius: 18px; background: white; }
          .publication { border: 1px solid #e4e9f2; border-radius: 18px; overflow: hidden; }
          .muted { color: #64748b; }
          .q-stepper { box-shadow: none !important; border: 0 !important; }
          @keyframes mascot-float {
            0%, 100% { transform: translate3d(0, 0, 0); }
            50% { transform: translate3d(0, -10px, 0); }
          }
          .mascot-float {
            animation: mascot-float 3.8s ease-in-out infinite;
            will-change: transform;
          }
        </style>
    ''')

    current_view: dict[str, str] = {'name': 'ocorrencias'}
    drawer_items: dict[str, Any] = {}
    MENU_ENTRIES = (
        ('ocorrencias', 'Ocorrências de mudança', 'fact_check'),
        ('novidades', 'Novidades lançadas', 'auto_stories'),
        ('whatsapp', 'WhatsApp', 'chat'),
        ('configuracoes', 'Configurações', 'settings'),
    )

    with ui.left_drawer(value=True).props('bordered width=260').classes(
        'bg-white p-2'
    ) as navigation_drawer:
        ui.label('NAVEGAÇÃO').classes('text-xs font-bold muted px-3 pt-2 tracking-wide')
        with ui.list().props('padding').classes('w-full'):
            for view_name, menu_label, menu_icon in MENU_ENTRIES:
                def _navigate(name: str = view_name) -> None:
                    show_view(name)

                menu_item = ui.item(on_click=_navigate).props('clickable').classes('rounded-lg')
                with menu_item:
                    with ui.item_section().props('avatar'):
                        ui.icon(menu_icon)
                    with ui.item_section():
                        ui.item_label(menu_label)
                drawer_items[view_name] = menu_item

    with ui.header().classes('bg-slate-900 text-white px-6 py-4'):
        with ui.row().classes('w-full items-center justify-between'):
            with ui.row().classes('items-center gap-3 no-wrap'):
                ui.button(
                    icon='menu', on_click=navigation_drawer.toggle
                ).props('flat round color=white')
                with ui.column().classes('gap-0'):
                    ui.label(APP_NAME).classes('text-xl font-bold')
                    ui.label(
                        'Crie uma novidade pronta a partir de imagens e texto bruto.'
                    ).classes('text-sm text-slate-300')
            ui.button(
                'Nova Novidade', icon='note_add', on_click=new_novelty_dialog.open
            ).props('outline color=white')

    with ui.column().classes('w-full px-6 py-5 gap-4'):

        def show_view(name: str) -> None:
            """Alterna entre as áreas do menu lateral, escondendo as demais."""
            current_view['name'] = name
            occurrence_panel.set_visibility(name == 'ocorrencias')
            novelty_panel.set_visibility(name == 'novidades')
            whatsapp_panel.set_visibility(name == 'whatsapp')
            settings_panel.set_visibility(name == 'configuracoes')
            for element in creation_elements:
                element.set_visibility(name == 'criar')
            for view, item in drawer_items.items():
                item.props(f'active={str(view == name).lower()}')

        def show_creation(visible: bool) -> None:
            show_view('criar' if visible else 'ocorrencias')

        def reset_novelty_draft() -> None:
            """Zera imagens e matéria da sessão anterior.

            Os arquivos em disco não são apagados: a exclusão definitiva continua
            sendo do botão "Nova Novidade", que limpa a área da sessão.
            """
            state['images'] = []
            composition.blocks = []
            composition.source = 'manual'
            composition.fallback_image_ids = []
            composition.cover = {}
            composition.review = {}
            composition._undo.clear()
            composition._redo.clear()
            state.update(
                generated=False,
                approved=False,
                exports={},
                generation={},
                generation_error='',
                generation_error_details={},
                generation_in_progress=False,
                active_generation_id='',
                active_idempotency_key='',
                placement_suggestions=[],
                annotation_image_id=None,
                html_override='',
            )
            thumbnails.refresh()
            resume_row.set_visibility(False)

        def launch_from_occurrence(occurrence: OccurrenceSummary) -> None:
            """Carrega o contexto da ocorrência no fluxo de criação já existente."""
            texto = occurrence.as_raw_text().strip()
            if not texto:
                ui.notify(
                    f'A ocorrência #{occurrence.number} não tem contexto registrado.',
                    type='warning',
                )
                return
            # Cada novidade começa do zero: sem imagens nem matéria da anterior.
            reset_novelty_draft()
            controls['raw_text'].value = texto
            controls['cover'].value = False
            persist_screen_state(step='Adicionar conteúdo')
            stepper.set_value('Adicionar conteúdo')
            show_creation(True)
            ui.notify(
                f'Ocorrência #{occurrence.number} carregada. Adicione as imagens e gere a novidade.',
                type='positive',
            )

        def back_to_occurrences() -> None:
            show_creation(False)

        with ui.column().classes('w-full gap-4') as occurrence_panel:
            with ui.row().classes('w-full justify-end') as resume_row:
                ui.button(
                    'Continuar novidade em andamento',
                    icon='play_arrow',
                    on_click=lambda: show_creation(True),
                ).props('flat color=primary')
            render_occurrence_menu(launch_from_occurrence)
        # A sessão anterior fica acessível por um botão, sem roubar a tela inicial.
        resume_row.set_visibility(
            bool(str(saved.get('raw_text') or '').strip())
            or bool(state['images'])
            or bool(state['generated'])
        )

        with ui.column().classes('w-full gap-4') as novelty_panel:
            render_novelty_history()

        with ui.column().classes('w-full gap-4') as whatsapp_panel:
            render_whatsapp_panel(whatsapp_gateway)

        with ui.column().classes('w-full gap-4') as settings_panel:
            with ui.card().classes('simple-card w-full p-5 gap-3'):
                ui.label('Configurações').classes('text-2xl font-bold')
                ui.label(
                    'Provedor de IA, chave de acesso e modelos usados na geração.'
                ).classes('muted text-sm')
                restored_provider = str(saved_settings.get('provider') or 'gemini')
                if restored_provider not in PROVIDER_LABELS:
                    restored_provider = 'gemini'
                provider_name = ui.select(
                    PROVIDER_LABELS, value=restored_provider, label='Provedor'
                ).classes('w-52')
                session_keys = {
                    name: os.getenv(environment, '')
                    for name, environment in API_KEY_ENVIRONMENTS.items()
                }
                variavel = API_KEY_ENVIRONMENTS[restored_provider]
                chave_origem = (
                    f'Chave carregada de {ENV_PATH.name} ({variavel}). '
                    'Editar aqui vale apenas para esta sessão.'
                    if session_keys[restored_provider]
                    else f'Sem chave no {ENV_PATH.name}. Preencha {variavel} nesse arquivo '
                    'para não digitar toda vez, ou informe abaixo apenas para esta sessão.'
                )
                api_key = ui.input(
                    'Chave da API',
                    value=session_keys[restored_provider],
                    password=True,
                    password_toggle_button=True,
                ).props('autocomplete=off').classes('w-full max-w-2xl')
                ui.label(chave_origem).classes('muted text-xs')
                model_values = {
                    name: {'text': TEXT_MODELS[name], 'image': IMAGE_MODELS[name]}
                    for name in PROVIDER_LABELS
                }
                model_values[restored_provider] = {
                    'text': str(
                        saved_settings.get('text_model')
                        or TEXT_MODELS[restored_provider]
                    ),
                    'image': str(
                        saved_settings.get('image_model')
                        or (
                            saved_settings.get('model')
                            if 'image' in str(saved_settings.get('model') or '').lower()
                            else ''
                        )
                        or IMAGE_MODELS[restored_provider]
                    ),
                }
                with ui.row().classes('w-full gap-3 no-wrap'):
                    text_model = ui.input(
                        'Modelo de Texto', value=model_values[restored_provider]['text'],
                    ).props('debounce=500').classes('w-96')
                    image_model = ui.input(
                        'Modelo de Imagem', value=model_values[restored_provider]['image'],
                    ).props('debounce=500').classes('w-96')
                selected_provider = {'name': restored_provider}

                def change_provider(event: events.ValueChangeEventArguments) -> None:
                    previous = selected_provider['name']
                    session_keys[previous] = api_key.value or ''
                    model_values[previous] = {
                        'text': str(text_model.value or TEXT_MODELS[previous]),
                        'image': str(image_model.value or IMAGE_MODELS[previous]),
                    }
                    current = str(event.value)
                    selected_provider['name'] = current
                    api_key.value = session_keys[current]
                    text_model.value = model_values[current]['text']
                    image_model.value = model_values[current]['image']
                    api_key.update()
                    text_model.update()
                    image_model.update()
                    persist_screen_state()

                provider_name.on_value_change(change_provider)

        with ui.row().classes('w-full items-center justify-between') as creation_header:
            ui.label('Criar novidade').classes('text-3xl font-bold')
            ui.button(
                'Voltar para as ocorrências', icon='arrow_back', on_click=back_to_occurrences
            ).props('flat color=primary')
        creation_subtitle = ui.label(
            'Revise o conteúdo da ocorrência e complemente com as imagens. A aplicação '
            'organiza o texto, posiciona as imagens e monta a publicação.'
        ).classes('muted')

        generation_status = persisted_generation_status()
        generation_id = str((state.get('generation') or {}).get('generation_id') or '')
        if generation_status == 'completed':
            state['generated'] = True
            state['current_step'] = 'Visualizar'
        elif generation_status == 'processing' and not active_generation_matches(generation_id):
            state['generation_in_progress'] = False
            state['generation_error'] = (
                'A geração anterior foi interrompida antes de concluir. '
                'Você pode iniciar uma nova tentativa com segurança.'
            )
            update_generation_state(
                status='interrupted',
                finished_at=_now_iso(),
                error=state['generation_error'],
            )

        initial_step = state['current_step'] if state['current_step'] in {
            'Adicionar conteúdo', 'Processamento', 'Visualizar', 'Aprovar e exportar'
        } else 'Adicionar conteúdo'
        with ui.stepper(value=initial_step).props('flat animated').classes(
            'w-full bg-transparent'
        ) as stepper:
            with ui.step('Adicionar conteúdo', icon='add_photo_alternate'):
                with ui.card().classes('simple-card w-full p-6 gap-5'):
                    ui.label('1. Adicionar conteúdo').classes('text-2xl font-bold')
                    ui.label(
                        'Selecione as imagens e cole a descrição da novidade, mesmo que esteja '
                        'incompleta, repetida ou sem formatação. A IA irá organizar todo o conteúdo.'
                    ).classes('muted')

                    async def handle_upload(event: events.UploadEventArguments) -> None:
                        try:
                            upload = event.file
                            original, size = workspace.save_original(
                                upload.name, await upload.read()
                            )
                            image_id = f'imagem_{uuid4().hex[:12]}'
                            state['images'].append({
                                'id': image_id,
                                'filename': Path(upload.name).name,
                                'original': original,
                                'treated': original,
                                'annotated': None,
                                'final': None,
                                'size': size,
                                'status': 'Pronta',
                                'error': '',
                                'editor': ImageEditor(),
                                'selected_id': None,
                            })
                            state.update(generated=False, approved=False, exports={})
                            thumbnails.refresh()
                            persist_screen_state()
                            ui.notify('Imagem adicionada.', type='positive')
                        except Exception as exc:
                            ui.notify(str(exc), type='negative')
                            logger.warning('Upload rejeitado na sessão %s: %s', workspace.session_id, exc)

                    ui.upload(
                        on_upload=handle_upload,
                        auto_upload=True,
                        label='Selecionar uma ou várias imagens',
                    ).props(
                        'multiple accept=.png,.jpg,.jpeg,.webp max-file-size=15728640 color=primary'
                    ).classes('w-full')

                    @ui.refreshable
                    def thumbnails() -> None:
                        if not state['images']:
                            ui.label('Nenhuma imagem selecionada.').classes('muted')
                            return
                        with ui.row().classes('w-full gap-3 no-wrap overflow-x-auto'):
                            for order, item in enumerate(state['images'], 1):
                                with ui.column().classes('w-40 min-w-40 gap-1'):
                                    ui.image(str(item['original'])).classes(
                                        'w-full h-24 object-contain bg-slate-100 rounded-lg'
                                    )
                                    ui.label(f'{order}. {item["filename"]}').classes(
                                        'text-sm truncate'
                                    )
                                    with ui.row().classes('gap-0'):
                                        ui.button(
                                            icon='arrow_back',
                                            on_click=lambda index=order - 1: move_image(index, -1),
                                        ).props('flat dense').tooltip('Mover para a esquerda')
                                        ui.button(
                                            icon='arrow_forward',
                                            on_click=lambda index=order - 1: move_image(index, 1),
                                        ).props('flat dense').tooltip('Mover para a direita')
                                        ui.button(
                                            icon='delete_outline',
                                            on_click=lambda image_id=item['id']: remove_image(image_id),
                                        ).props('flat dense color=negative').tooltip('Remover imagem')

                    def move_image(index: int, offset: int) -> None:
                        target = index + offset
                        if index < 0 or target < 0 or target >= len(state['images']):
                            return
                        state['images'][index], state['images'][target] = (
                            state['images'][target], state['images'][index]
                        )
                        state.update(generated=False, approved=False, exports={})
                        thumbnails.refresh()
                        persist_screen_state()

                    def remove_image(image_id: str) -> None:
                        state['images'] = [
                            item for item in state['images'] if item['id'] != image_id
                        ]
                        state.update(generated=False, approved=False, exports={})
                        thumbnails.refresh()
                        persist_screen_state()

                    thumbnails()
                    raw_text = ui.textarea(
                        'Texto bruto da novidade',
                        value=str(saved.get('raw_text') or ''),
                        placeholder='Cole aqui a descrição, anotações ou rascunho da novidade.',
                    ).props('rows=10 debounce=500').classes('w-full text-base')
                    cover_requested = ui.checkbox(
                        'Gerar imagem de capa ilustrativa',
                        value=bool(saved_settings.get('cover_requested', False)),
                    )

                    ui.label(
                        'O provedor de IA, a chave e os modelos ficam em Configurações, '
                        'no menu lateral.'
                    ).classes('text-sm muted')

                    controls.update(
                        raw_text=raw_text, provider=provider_name,
                        text_model=text_model, image_model=image_model,
                        cover=cover_requested,
                    )
                    raw_text.on_value_change(lambda _: persist_screen_state())
                    text_model.on_value_change(lambda _: persist_screen_state())
                    image_model.on_value_change(lambda _: persist_screen_state())
                    cover_requested.on_value_change(lambda _: persist_screen_state())
                    if persisted_generation_status() in {'completed', 'failed', 'interrupted', 'processing'}:
                        persist_screen_state()

                    async def continue_to_generation() -> None:
                        generation = dict(state.get('generation') or {})
                        generation_id = str(generation.get('generation_id') or '')
                        if (
                            generation.get('status') == 'processing'
                            and active_generation_matches(generation_id)
                        ):
                            safe_visual_update(
                                'notificacao_geracao_em_andamento',
                                lambda: ui.notify(
                                    'Já existe uma geração em andamento nesta sessão.',
                                    type='warning',
                                ),
                            )
                            return
                        if not (raw_text.value or '').strip():
                            ui.notify('Cole o texto bruto da novidade.', type='warning')
                            return
                        persist_screen_state(step='Processamento')
                        safe_visual_update('avancar_para_processamento', stepper.next)
                        await generate_novelty()

                    with ui.stepper_navigation():
                        generation_button = ui.button(
                            'Gerar Novidade', icon='auto_awesome',
                            on_click=continue_to_generation,
                        ).classes('text-base')

            with ui.step('Processamento', icon='auto_awesome'):
                with ui.card().classes('simple-card w-full p-6 gap-5'):
                    mascot_path = safe_path(PROJECT_ROOT / 'Mascote.png')
                    with ui.row().classes(
                        'w-full flex-col md:flex-row flex-nowrap items-stretch gap-6'
                    ):
                        processing_content = ui.column().classes(
                            'w-full min-w-0 flex-1 gap-5'
                        )
                        with processing_content:
                            ui.label('2. Processamento').classes('text-2xl font-bold')
                            ui.label(
                                'A aplicação analisará o conteúdo, organizará a matéria e revisará '
                                'as informações antes de preparar a visualização.'
                            ).classes('muted')
                            progress = ui.linear_progress(
                                value=float((state.get('generation') or {}).get('percentual') or 0)
                            ).classes('w-full')
                            progress.set_visibility(
                                persisted_generation_status() == 'processing'
                            )
                            progress_label = ui.label(
                                str((state.get('generation') or {}).get('mensagem') or '')
                            ).classes('text-sm muted')
                            with ui.column().classes('gap-1 text-sm muted'):
                                for stage_label in (
                                    'Analisando o conte\u00fado',
                                    'Organizando a mat\u00e9ria',
                                    'Revisando as informa\u00e7\u00f5es',
                                    'Preparando a visualiza\u00e7\u00e3o',
                                ):
                                    ui.label(f'- {stage_label}')
                                cover_stage = ui.label('- Gerando a imagem de capa')
                                cover_stage.bind_visibility_from(cover_requested, 'value')

                        if mascot_path.is_file():
                            with ui.column().classes(
                                'w-full md:w-80 lg:w-96 shrink-0 items-center justify-center self-stretch'
                            ):
                                ui.image(mascot_path).props('fit=contain no-spinner').classes(
                                     'mascot-float w-56 sm:w-64 md:w-80 lg:w-96 '
                                     'max-w-full h-auto object-contain'
                                )
                        else:
                            logger.warning(
                                '[PROCESSAMENTO] Mascote não encontrado em %s; '
                                'a tela será exibida sem a imagem.',
                                mascot_path,
                            )

                    @ui.refreshable
                    def generation_error_panel() -> None:
                        if not state['generation_error']:
                            return
                        ui.label(state['generation_error']).classes('text-sm text-red-700')
                        details = state.get('generation_error_details') or {}
                        if details:
                            with ui.expansion(
                                'Ver detalhes técnicos', icon='info_outline'
                            ).props('dense').classes('w-full text-sm'):
                                for label, value in details.items():
                                    ui.label(f'{label}: {value}').classes(
                                        'text-xs font-mono break-all'
                                    )

                    async def generate_novelty() -> None:
                        generation = dict(state.get('generation') or {})
                        current_generation_id = str(generation.get('generation_id') or '')
                        if (
                            generation.get('status') == 'processing'
                            and active_generation_matches(current_generation_id)
                        ):
                            safe_visual_update(
                                'notificacao_geracao_em_andamento',
                                lambda: ui.notify(
                                    'Já existe uma geração em andamento nesta sessão.',
                                    type='warning',
                                ),
                            )
                            return
                        generation_id = uuid4().hex
                        idempotency_key = generation_fingerprint()
                        if not GENERATION_REGISTRY.try_begin(
                            workspace.session_id, generation_id, idempotency_key,
                        ):
                            active = GENERATION_REGISTRY.active_generation(workspace.session_id)
                            if active:
                                persist_generation_state(
                                    generation_id=active.generation_id,
                                    idempotency_key=active.idempotency_key,
                                    status='processing',
                                    etapa_atual=str(generation.get('etapa_atual') or 'pending'),
                                    percentual=float(generation.get('percentual') or 0),
                                    mensagem=str(
                                        generation.get('mensagem')
                                        or 'Geração em andamento...'
                                    ),
                                )
                            safe_visual_update(
                                'notificacao_geracao_em_andamento',
                                lambda: ui.notify(
                                    'Já existe uma geração em andamento nesta sessão.',
                                    type='warning',
                                ),
                            )
                            return
                        persist_generation_state(
                            generation_id=generation_id,
                            idempotency_key=idempotency_key,
                            status='pending',
                            etapa_atual='pending',
                            percentual=0,
                            mensagem='Geração aguardando início...',
                            started_at=_now_iso(),
                            finished_at='',
                            error='',
                            generated=False,
                            document={},
                            composition={},
                            cover_file='',
                        )
                        state['generation_in_progress'] = True
                        state['active_generation_id'] = generation_id
                        state['active_idempotency_key'] = idempotency_key
                        state.update(
                            generation_error='',
                            generation_error_details={},
                            generated=False,
                            approved=False,
                            exports={},
                        )
                        processing_started = time.perf_counter()
                        persist_generation_state(
                            generation_id=generation_id,
                            idempotency_key=idempotency_key,
                            status='processing',
                            etapa_atual='pending',
                            percentual=0,
                            mensagem='Iniciando geração...',
                            started_at=_now_iso(),
                            finished_at='',
                            error='',
                            generated=False,
                            document={},
                            composition={},
                            cover_file='',
                        )
                        logger.info(
                            '[PROCESSAMENTO] Interface iniciada; sessao=%s', workspace.session_id,
                        )
                        safe_visual_update('desabilitar_botao_gerar', generation_button.disable)
                        safe_visual_update('desabilitar_botao_tentar_novamente', retry_button.disable)
                        safe_visual_update('desabilitar_botao_visualizar', visualize_button.disable)
                        safe_visual_update(
                            'ocultar_botao_tentar_novamente',
                            lambda: retry_button.set_visibility(False),
                        )
                        safe_visual_update(
                            'exibir_barra_progresso',
                            lambda: progress.set_visibility(True),
                        )
                        safe_visual_update('ativar_polling_geracao', generation_poll.activate)
                        safe_visual_update('atualizar_painel_erro_inicial', generation_error_panel.refresh)
                        active_stage = 'geracao_automatica'
                        try:
                            provider = ProviderFactory.create(provider_name.value)
                            def update_generation_progress(stage: str) -> None:
                                labels = {
                                    'analisando_conteudo': 'Analisando o conteúdo...',
                                    'organizacao': 'Organizando a matéria...',
                                    'revisao': 'Revisando as informações...',
                                    'correcao': 'Aplicando correções de fidelidade...',
                                    'segunda_revisao': 'Revisando as correções...',
                                    'capa': 'Gerando a imagem de capa...',
                                    'preparacao_visualizacao': 'Preparando a visualização...',
                                }
                                with_cover = {
                                    'analisando_conteudo': 0.05,
                                    'organizacao': 0.20,
                                    'revisao': 0.45,
                                    'correcao': 0.58,
                                    'segunda_revisao': 0.68,
                                    'capa': 0.78,
                                    'preparacao_visualizacao': 0.95,
                                }
                                without_cover = {
                                    'analisando_conteudo': 0.05,
                                    'organizacao': 0.20,
                                    'revisao': 0.55,
                                    'correcao': 0.70,
                                    'segunda_revisao': 0.85,
                                    'preparacao_visualizacao': 0.95,
                                }
                                values = with_cover if cover_requested.value else without_cover
                                value = float(values.get(stage, progress.value or 0))
                                message = labels.get(stage, 'Processando...')
                                persist_generation_state(
                                    status='processing',
                                    etapa_atual=stage,
                                    percentual=value,
                                    mensagem=message,
                                )

                                def apply_progress() -> None:
                                    progress_label.set_text(message)
                                    progress.value = value
                                    progress.update()

                                safe_visual_update(f'progresso_{stage}', apply_progress)
                            async with generation_lock:
                                generated_document = await editorial_orchestrator.generate(
                                    provider,
                                    api_key=api_key.value or '',
                                    text_model=text_model.value or '',
                                    image_model=image_model.value or '',
                                    raw_text=(raw_text.value or '').strip(),
                                    images=state['images'],
                                    workspace=workspace,
                                    cover_requested=bool(cover_requested.value),
                                    run_sync=run.io_bound,
                                    on_progress=update_generation_progress,
                                    generation_id=generation_id,
                                    idempotency_key=idempotency_key,
                                )
                            orchestration_finished = time.perf_counter()
                            logger.info(
                                '[PROCESSAMENTO] Orquestração recebida pela interface em %.2f s',
                                orchestration_finished - processing_started,
                            )
                            composition.blocks = generated_document.blocks
                            composition.source = generated_document.source
                            composition.fallback_image_ids = list(
                                generated_document.fallback_image_ids
                            )
                            composition.cover = dict(generated_document.cover)
                            composition.review = dict(generated_document.review)
                            composition._undo.clear()
                            composition._redo.clear()
                            active_stage = 'montagem_pre_visualizacao'
                            state['generated'] = True
                            state['approved'] = False
                            state['exports'] = {}
                            composition_payload = composition.to_dict()
                            update_generation_state(
                                status='completed',
                                etapa_atual='completed',
                                percentual=1,
                                mensagem='Novidade gerada com sucesso.',
                                finished_at=_now_iso(),
                                error='',
                                generated=True,
                                document=composition_payload,
                                composition=composition_payload,
                                cover_file=_cover_file_from_composition(composition_payload),
                                dados_visualizacao={'composition': composition_payload},
                            )
                            persist_screen_state(step='Visualizar')
                            persisted_at = time.perf_counter()
                            logger.info(
                                '[PROCESSAMENTO] Gravação de estado concluída em %.2f s',
                                persisted_at - orchestration_finished,
                            )

                            def apply_completed_ui() -> None:
                                progress.value = 1
                                progress.update()
                                preview.refresh()
                                # A aba HTML também nasce vazia antes da geração
                                # e precisa ser remontada aqui.
                                html_editor.refresh()
                                visualize_button.enable()
                                retry_button.set_visibility(False)
                                generation_error_panel.refresh()

                            safe_visual_update('finalizacao_visual', apply_completed_ui)
                            safe_visual_update(
                                'notificacao_sucesso',
                                lambda: ui.notify('Novidade gerada com sucesso.', type='positive'),
                            )
                            if composition.fallback_image_ids:
                                safe_visual_update(
                                    'notificacao_imagens_fallback',
                                    lambda: ui.notify(
                                        'Algumas imagens foram adicionadas ao final da publicação.',
                                        type='warning',
                                    ),
                                )
                            # Os avisos da revisão não viram notificação: eles já
                            # aparecem por inteiro no painel da pré-visualização, e
                            # um toast por aviso cobria a tela toda.
                            if composition.cover.get('mensagem'):
                                safe_visual_update(
                                    'notificacao_aviso_capa',
                                    lambda: ui.notify(str(composition.cover['mensagem']), type='warning'),
                                )
                            logger.info(
                                '[PROCESSAMENTO] Visualização/UI concluída em %.2f s; total=%.2f s',
                                time.perf_counter() - persisted_at,
                                time.perf_counter() - processing_started,
                            )
                        except ProviderError as exc:
                            safe_user_message = sanitize_technical_detail(
                                exc.user_message, api_key.value or ''
                            )
                            state['generation_error'] = safe_user_message
                            state['generation_error_details'] = {
                                label: sanitize_technical_detail(
                                    value, api_key.value or ''
                                )
                                for label, value in exc.sanitized_details().items()
                            }
                            persist_generation_state(
                                status='failed',
                                etapa_atual=active_stage,
                                finished_at=_now_iso(),
                                error=safe_user_message,
                                generated=False,
                            )
                            safe_visual_update('atualizar_painel_erro_provider', generation_error_panel.refresh)
                            safe_visual_update(
                                'exibir_botao_tentar_novamente_provider',
                                lambda: retry_button.set_visibility(True),
                            )
                            safe_visual_update(
                                'notificacao_erro_provider',
                                lambda: ui.notify(safe_user_message, type='negative', timeout=9000),
                            )
                            logger.error(
                                'Falha na geração da sessão %s [%s] stage=%s provider=%s '
                                'model=%s status_http=%s exception=%s image=%s detail=%s',
                                workspace.session_id, exc.code, exc.stage, exc.provider,
                                exc.model, exc.status_http, exc.exception_type, exc.image_id,
                                sanitize_technical_detail(
                                    exc.technical_detail or exc.user_message,
                                    api_key.value or '',
                                ),
                            )
                        except Exception as exc:
                            state['generation_error'] = (
                                'Não foi possível montar a pré-visualização.'
                                if active_stage == 'montagem_pre_visualizacao'
                                else 'Não foi possível gerar a novidade.'
                            )
                            state['generation_error_details'] = {
                                'Provedor': str(provider_name.value or 'não informado'),
                                'Modelo de Texto': str(text_model.value or 'não informado'),
                                'Modelo de Imagem': str(image_model.value or 'não informado'),
                                'Etapa': active_stage,
                                'Status HTTP': 'não aplicável',
                                'Tipo da exceção': exc.__class__.__name__,
                                'Mensagem técnica': sanitize_technical_detail(
                                    exc, api_key.value or ''
                                ),
                                'Imagem': 'não aplicável',
                            }
                            persist_generation_state(
                                status='failed',
                                etapa_atual=active_stage,
                                finished_at=_now_iso(),
                                error=state['generation_error'],
                                generated=False,
                            )
                            safe_visual_update('atualizar_painel_erro_inesperado', generation_error_panel.refresh)
                            safe_visual_update(
                                'exibir_botao_tentar_novamente_inesperado',
                                lambda: retry_button.set_visibility(True),
                            )
                            safe_visual_update(
                                'notificacao_erro_inesperado',
                                lambda: ui.notify(state['generation_error'], type='negative'),
                            )
                            logger.exception('Falha inesperada na geração da sessão %s', workspace.session_id)
                        finally:
                            state['generation_in_progress'] = False
                            state['active_generation_id'] = ''
                            state['active_idempotency_key'] = ''
                            GENERATION_REGISTRY.finish(workspace.session_id, generation_id)
                            safe_visual_update('habilitar_botao_gerar', generation_button.enable)
                            safe_visual_update('habilitar_botao_tentar_novamente', retry_button.enable)
                            logger.info(
                                '[PROCESSAMENTO] Interface encerrada em %.2f s; sucesso=%s',
                                time.perf_counter() - processing_started, state['generated'],
                            )

                            def clear_progress_ui() -> None:
                                if persisted_generation_status() != 'processing':
                                    progress.set_visibility(False)
                                progress_label.set_text('')

                            safe_visual_update('limpeza_visual', clear_progress_ui)

                    with processing_content:
                        with ui.row().classes('gap-3'):
                            visualize_button = ui.button(
                                'Visualizar Novidade',
                                icon='visibility',
                                on_click=stepper.next,
                            ).classes('text-base')
                            if state['generated']:
                                visualize_button.enable()
                            else:
                                visualize_button.disable()
                            retry_button = ui.button(
                                'Tentar novamente', icon='refresh', on_click=generate_novelty,
                            ).props('outline color=primary')
                            retry_button.set_visibility(bool(state['generation_error']))
                        generation_error_panel()
                        with ui.stepper_navigation():
                            ui.button('Voltar', on_click=stepper.previous).props('flat')

            with ui.step('Visualizar', icon='visibility'):
                def content_changed() -> None:
                    state.update(approved=False, exports={})
                    persist_screen_state()
                    preview.refresh()
                    # O HTML acompanha a matéria, exceto quando foi editado à mão.
                    html_editor.refresh()

                def generated_html() -> str:
                    """HTML atual: o editado manualmente ou o derivado da composi\u00e7\u00e3o."""
                    if str(state.get('html_override') or '').strip():
                        return str(state['html_override'])
                    payload = composition.to_dict()
                    payload['texto_novidade'] = composition.text
                    payload['imagens'] = [
                        {
                            'id': str(item['id']),
                            'arquivo_final': Path(
                                item.get('final') or item.get('treated') or item['original']
                            ).name,
                        }
                        for item in state['images'] if not item['error']
                    ]
                    return build_novelty_html(payload)

                def has_content() -> bool:
                    """Basta existir matéria: edição manual também vale, não só a geração."""
                    return bool(composition.blocks) or bool(state.get('html_override'))

                @ui.refreshable
                def html_editor() -> None:
                    if not has_content():
                        ui.label('Gere a novidade para ver o HTML.').classes('p-10 muted')
                        return
                    editado = bool(str(state.get('html_override') or '').strip())
                    with ui.row().classes('w-full items-center gap-2'):
                        ui.label(
                            'HTML editado manualmente: \u00e9 ele que ser\u00e1 exportado.'
                            if editado
                            else 'HTML gerado a partir da mat\u00e9ria.'
                        ).classes('text-sm muted')
                        if editado:
                            ui.badge('editado').props('color=orange outline')
                    codigo = ui.textarea(value=generated_html()).props(
                        'rows=24 outlined input-class=font-mono'
                    ).classes('w-full text-xs')

                    def aplicar_html() -> None:
                        state['html_override'] = str(codigo.value or '')
                        state.update(approved=False, exports={})
                        persist_screen_state()
                        html_editor.refresh()
                        ui.notify(
                            'HTML aplicado. A exporta\u00e7\u00e3o passa a usar este c\u00f3digo.',
                            type='positive',
                        )

                    def descartar_html() -> None:
                        state['html_override'] = ''
                        state.update(approved=False, exports={})
                        persist_screen_state()
                        html_editor.refresh()
                        ui.notify('HTML voltou a ser gerado pela mat\u00e9ria.', type='positive')

                    with ui.row().classes('w-full justify-end gap-2'):
                        if editado:
                            ui.button(
                                'Descartar edi\u00e7\u00e3o', icon='restart_alt', on_click=descartar_html
                            ).props('flat')
                        ui.button(
                            'Aplicar HTML', icon='save', on_click=aplicar_html
                        ).props('color=primary')

                def start_editing() -> None:
                    state['editing_text'] = True
                    preview.refresh()

                def cancel_editing() -> None:
                    state['editing_text'] = False
                    preview.refresh()

                with ui.tabs().classes('w-full') as editor_tabs:
                    preview_tab = ui.tab('Pr\u00e9-visualiza\u00e7\u00e3o')
                    editor_html_tab = ui.tab('HTML')
                with ui.tab_panels(editor_tabs, value=preview_tab).classes('w-full'):
                    with ui.tab_panel(preview_tab).classes('p-0'):
                        with ui.card().classes('simple-card publication w-full p-0'):
                            @ui.refreshable
                            def preview() -> None:
                                if not state['generated']:
                                    ui.label(
                                        'Gere a novidade para visualizar o resultado.'
                                    ).classes('p-10 muted')
                                    return
                                if state.get('editing_text'):
                                    render_text_edition()
                                    return
                                with ui.row().classes('w-full justify-end px-6 pt-4'):
                                    ui.button(
                                        'Editar texto', icon='edit', on_click=start_editing
                                    ).props('outline color=primary')
                                render_novelty_preview(
                                    composition, state['images'], content_changed,
                                )

                            def render_text_edition() -> None:
                                """Edi\u00e7\u00e3o do texto no lugar da mat\u00e9ria, na mesma aba."""
                                with ui.column().classes('w-full max-w-4xl mx-auto p-6 gap-3'):
                                    ui.label('Editar texto da novidade').classes(
                                        'text-xl font-bold'
                                    )
                                    ui.label(
                                        'A primeira linha \u00e9 o t\u00edtulo, par\u00e1grafos s\u00e3o '
                                        'separados por linha em branco, listas usam "- " '
                                        'no in\u00edcio da linha e **negrito** entre asteriscos '
                                        'duplos. As imagens s\u00e3o preservadas.'
                                    ).classes('text-sm muted')
                                    corpo = ui.textarea(
                                        value=composition_to_plain_text(composition)
                                    ).props('rows=24 outlined').classes('w-full text-base')

                                    def salvar() -> None:
                                        apply_plain_text(composition, str(corpo.value or ''))
                                        state['editing_text'] = False
                                        content_changed()
                                        ui.notify('Texto aplicado \u00e0 mat\u00e9ria.', type='positive')

                                    with ui.row().classes('w-full justify-end gap-2'):
                                        ui.button(
                                            'Cancelar', on_click=cancel_editing
                                        ).props('flat')
                                        ui.button(
                                            'Salvar texto', icon='save', on_click=salvar
                                        ).props('color=primary')

                            preview()
                    with ui.tab_panel(editor_html_tab):
                        html_editor()

                def undo_organization() -> None:
                    content_changed() if composition.undo() else None

                def redo_organization() -> None:
                    content_changed() if composition.redo() else None

                with ui.row().classes('w-full justify-center gap-2 mt-4'):
                    ui.label('Arraste as imagens na matéria para alterar sua posição.').classes(
                        'w-full text-center text-sm muted'
                    )
                    ui.button(
                        'Desfazer organização', icon='undo', on_click=undo_organization
                    ).props('outline')
                    ui.button(
                        'Refazer organização', icon='redo', on_click=redo_organization
                    ).props('outline')

                def generate_again() -> None:
                    state['approved'] = False
                    persist_screen_state(step='Processamento')
                    stepper.previous()


                def open_whatsapp_flow() -> None:
                    """Tela de envio: resumo editável e número do destinatário."""
                    if not state['generated']:
                        ui.notify('Gere a novidade primeiro.', type='warning')
                        return
                    situacao = whatsapp_gateway.status()
                    if not situacao.connected:
                        ui.notify(
                            f'WhatsApp não está pronto: {situacao.label}. '
                            'Vincule a conta no menu WhatsApp.',
                            type='warning',
                        )
                        show_view('whatsapp')
                        return

                    titulo = composition.title or 'Novidade EMC'
                    with ui.dialog() as dialog, ui.card().classes('w-full max-w-2xl p-6 gap-4'):
                        ui.label('Enviar novidade pelo WhatsApp').classes('text-2xl font-bold')
                        ui.label(f'Conta vinculada: {situacao.phone or "não informada"}').classes(
                            'text-sm muted'
                        )
                        numero = ui.input(
                            'Número do destinatário', placeholder='31999999999',
                        ).props('outlined').classes('w-full').mark('whatsapp-numero')
                        ui.label(
                            'Sem o código do país, o número é tratado como brasileiro.'
                        ).classes('text-xs muted')
                        mensagem = ui.textarea(
                            'Mensagem', value=titulo,
                        ).props('rows=8 outlined').classes('w-full').mark('whatsapp-mensagem')
                        resultado = ui.label('').classes('text-sm muted')
                        enviando = ui.spinner(size='lg')
                        enviando.set_visibility(False)

                        async def enviar() -> None:
                            if state.get('whatsapp_sending'):
                                return
                            state['whatsapp_sending'] = True
                            botao_enviar.disable()
                            enviando.set_visibility(True)
                            resultado.set_text('Enviando...')
                            try:
                                identificador = await run.io_bound(
                                    lambda: whatsapp_gateway.send(
                                        recipient=str(numero.value or ''),
                                        message=str(mensagem.value or ''),
                                    )
                                )
                                ui.notify('Novidade enviada pelo WhatsApp.', type='positive')
                                logger.info(
                                    '[WHATSAPP] Novidade enviada; destinatario=%s titulo=%s id=%s',
                                    mask_phone(str(numero.value or '')), titulo,
                                    identificador or 'nao informado',
                                )
                                dialog.close()
                            except WhatsAppGatewayError as exc:
                                resultado.set_text(exc.user_message)
                                ui.notify(exc.user_message, type='negative')
                            finally:
                                state['whatsapp_sending'] = False
                                botao_enviar.enable()
                                enviando.set_visibility(False)

                        with ui.row().classes('w-full justify-end gap-2'):
                            ui.button('Cancelar', on_click=dialog.close).props('flat')
                            botao_enviar = ui.button(
                                'Enviar', icon='send', on_click=enviar,
                            ).props('color=green').mark('whatsapp-enviar')
                    dialog.open()


                def export_novelty(download: bool = True) -> None:
                    if not state['generated']:
                        ui.notify('Gere a novidade primeiro.', type='warning')
                        return
                    export_items = [
                        {**item, 'annotations': list(item['editor'].document.items)}
                        for item in state['images'] if not item['error']
                    ]
                    payload = composition.to_dict()
                    payload['texto_novidade'] = composition.text
                    state['exports'] = build_composition_exports(
                        workspace, export_items, payload, WRITER_ORGANIZER_PROMPT,
                        html_override=str(state.get('html_override') or ''),
                    )
                    persist_screen_state()
                    ui.notify('Arquivos gerados com sucesso.', type='positive')
                    if download:
                        ui.download(str(state['exports']['zip']))

                with ui.row().classes('w-full justify-center gap-2 mt-5'):
                    ui.button('Voltar', icon='arrow_back', on_click=stepper.previous).props('flat')
                    ui.button('Gerar novamente', icon='refresh', on_click=generate_again).props('outline')
                    ui.button('Enviar pelo WhatsApp', icon='send', on_click=open_whatsapp_flow).props('outline color=green')

            with ui.step('Aprovar e exportar', icon='task_alt'):
                with ui.card().classes('simple-card w-full p-8 items-center text-center'):
                    ui.icon('task_alt', size='64px', color='positive')
                    ui.label('Novidade aprovada').classes('text-3xl font-bold')
                    ui.label(
                        'O conteúdo visualizado está pronto para exportação.'
                    ).classes('muted')
                    with ui.row().classes('gap-3'):
                        ui.button('Voltar', on_click=stepper.previous).props('outline')
                        ui.button(
                            'Exportar', icon='download',
                            on_click=lambda: export_novelty(True),
                        )
                        ui.button(
                            'Enviar pelo WhatsApp', icon='send',
                            on_click=open_whatsapp_flow,
                        ).props('outline color=green')

        creation_elements = [creation_header, creation_subtitle, stepper]
        # A tela inicial é sempre o painel de ocorrências.
        show_view('ocorrencias')

    def sync_generation_from_store() -> None:
        stored = state_store.load() or {}
        if str(stored.get('session_id') or '') != workspace.session_id:
            return
        generation = dict(stored.get('generation') or {})
        if not generation:
            return
        status = str(generation.get('status') or '')
        state['generation'] = generation
        if status == 'processing':
            generation_id = str(generation.get('generation_id') or '')
            if not active_generation_matches(generation_id):
                state['generation_error'] = (
                    'A geração anterior foi interrompida antes de concluir. '
                    'Você pode iniciar uma nova tentativa com segurança.'
                )
                persist_generation_state(
                    status='interrupted',
                    finished_at=_now_iso(),
                    error=state['generation_error'],
                )
                safe_visual_update(
                    'polling_interrupted',
                    lambda: (
                        progress.set_visibility(False),
                        retry_button.set_visibility(True),
                        generation_error_panel.refresh(),
                    ),
                )
                generation_poll.deactivate()
                return
            value = float(generation.get('percentual') or 0)
            message = str(generation.get('mensagem') or 'Geração em andamento...')
            safe_visual_update(
                'polling_processing',
                lambda: (
                    progress.set_visibility(True),
                    progress_label.set_text(message),
                    setattr(progress, 'value', value),
                    progress.update(),
                    visualize_button.disable(),
                ),
            )
            return
        if status == 'completed':
            composition_payload = dict(
                generation.get('composition')
                or generation.get('document')
                or stored.get('composition')
                or {}
            )
            if composition_payload:
                try:
                    restored_composition = CompositionDocument.from_dict(composition_payload)
                    composition.blocks = restored_composition.blocks
                    composition.source = restored_composition.source
                    composition.fallback_image_ids = list(
                        restored_composition.fallback_image_ids
                    )
                    composition.cover = dict(restored_composition.cover)
                    composition.review = dict(restored_composition.review)
                    composition._undo.clear()
                    composition._redo.clear()
                except (TypeError, ValueError):
                    logger.warning(
                        '[PROCESSAMENTO] Composição concluída não pôde ser aplicada pelo polling; sessao=%s',
                        workspace.session_id,
                    )
            state['generated'] = True
            state['generation_error'] = ''
            state['generation_error_details'] = {}
            persist_screen_state(step='Visualizar')

            def apply_completed_polling() -> None:
                progress.value = 1
                progress.set_visibility(False)
                progress_label.set_text('')
                preview.refresh()
                html_editor.refresh()
                visualize_button.enable()
                retry_button.set_visibility(False)
                generation_error_panel.refresh()
                stepper.value = 'Visualizar'
                stepper.update()

            safe_visual_update('polling_completed', apply_completed_polling)
            generation_poll.deactivate()
            return
        if status in {'failed', 'interrupted'}:
            state['generation_error'] = str(generation.get('error') or '')
            state['generation_error_details'] = {}
            state['generation_in_progress'] = False
            safe_visual_update(
                f'polling_{status}',
                lambda: (
                    progress.set_visibility(False),
                    progress_label.set_text(''),
                    retry_button.set_visibility(True),
                    generation_error_panel.refresh(),
                    visualize_button.disable() if not state['generated'] else visualize_button.enable(),
                ),
            )
            generation_poll.deactivate()

    generation_poll = ui.timer(
        2.0,
        sync_generation_from_store,
        active=persisted_generation_status() == 'processing',
    )

    stepper.on_value_change(
        lambda event: persist_screen_state(step=str(event.value))
    )


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(('127.0.0.1', port)) != 0


if __name__ in {'__main__', '__mp_main__'}:
    if not port_is_free(PORT):
        logger.error('Inicialização interrompida: porta %s ocupada.', PORT)
        raise SystemExit(f'A porta {PORT} está ocupada. Nenhum processo foi encerrado.')
    ui.run(
    host='0.0.0.0',
    port=PORT,
    title=APP_NAME,
    favicon=PROJECT_ROOT / 'Logo_Emc.ico',
    reload=False,
    show=False,
    storage_secret=resolve_storage_secret(),
)

