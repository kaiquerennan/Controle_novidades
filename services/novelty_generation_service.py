from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
from time import monotonic
from typing import Any

from components.image_editor import ImageEditor
from models.annotation import Annotation
from models.composition import CompositionDocument, ContentBlock
from models.novelty_plan import MarkingInstruction, NoveltyPlan
from services.image_service import SessionWorkspace, render_annotations
from services.providers.base_provider import AIProvider
from services.model_routing import configured_text_model
from services.providers.exceptions import (
    ProviderError,
    sanitize_technical_detail,
    sanitized_exception_chain,
)


logger = logging.getLogger('EMC Novidade Visual IA')


def _contextual_error(
    exc: BaseException,
    *,
    provider: str,
    model: str,
    stage: str,
    message: str,
    api_key: str = '',
    image_id: str = '',
) -> ProviderError:
    if isinstance(exc, ProviderError):
        detail = exc.technical_detail or sanitized_exception_chain(exc, api_key)
        safe_user_message = sanitize_technical_detail(exc.user_message, api_key)
        return ProviderError(
            f'{message} {safe_user_message}'.strip(),
            code=exc.code,
            technical_detail=sanitize_technical_detail(detail, api_key),
            provider=exc.provider or provider,
            model=exc.model or model,
            stage=stage,
            status_http=exc.status_http,
            exception_type=exc.exception_type or (
                exc.__cause__.__class__.__name__ if exc.__cause__ else exc.__class__.__name__
            ),
            image_id=image_id or exc.image_id,
        )
    return ProviderError(
        message,
        code='internal_generation_error',
        technical_detail=sanitized_exception_chain(exc, api_key),
        provider=provider,
        model=model,
        stage=stage,
        exception_type=exc.__class__.__name__,
        image_id=image_id,
    )


def _end_point(marking: MarkingInstruction, delta: float = 0.08) -> tuple[float, float]:
    return (
        marking.x2 if marking.x2 is not None else min(1.0, marking.x + delta),
        marking.y2 if marking.y2 is not None else min(1.0, marking.y + delta),
    )


def annotation_from_instruction(marking: MarkingInstruction) -> Annotation:
    common = {
        'color': '#ef4444',
        'width': 4,
        'font_size': 26,
        'opacity': 0.92,
    }
    if marking.tipo == 'seta':
        return Annotation(
            kind='arrow', points=[_end_point(marking, 0.12), (marking.x, marking.y)],
            **common,
        )
    if marking.tipo == 'caixa':
        return Annotation(
            kind='rectangle', points=[(marking.x, marking.y), _end_point(marking)],
            **common,
        )
    if marking.tipo == 'circulo':
        return Annotation(
            kind='circle', points=[(marking.x, marking.y), _end_point(marking)],
            **common,
        )
    if marking.tipo == 'numero':
        return Annotation(
            kind='marker', points=[(marking.x, marking.y)], number=marking.ordem,
            **common,
        )
    return Annotation(
        kind='text', points=[(marking.x, marking.y)],
        text=marking.texto or marking.alvo,
        text_background='#ffffff',
        **common,
    )


def apply_novelty_plan(
    plan: NoveltyPlan,
    images: list[dict[str, Any]],
    workspace: SessionWorkspace,
    *,
    provider_name: str = '',
    planning_model: str = '',
) -> CompositionDocument:
    by_id = {str(item['id']): item for item in images}
    planned_marks = {value.imagem_id: value.marcacoes for value in plan.imagens}
    for image_id, item in by_id.items():
        editor = ImageEditor()
        for instruction in planned_marks.get(image_id, []):
            editor.document.add(annotation_from_instruction(instruction))
        try:
            final_path = render_annotations(
                item['treated'],
                workspace.export_dir / 'geradas' / image_id / 'imagem_final.png',
                editor.document.items,
            )
        except Exception as exc:
            raise _contextual_error(
                exc,
                provider=provider_name,
                model=planning_model,
                stage='aplicacao_marcacoes',
                message='Não foi possível aplicar as marcações da imagem.',
                image_id=image_id,
            ) from exc
        item.update(
            editor=editor,
            annotated=final_path,
            final=final_path,
            selected_id=None,
            status='Pronta para visualização',
            error='',
        )

    blocks: list[ContentBlock] = []
    used_images: set[str] = set()
    for planned in plan.blocos:
        if planned.tipo != 'imagem':
            blocks.append(ContentBlock(tipo=planned.tipo, conteudo=planned.conteudo))
            continue
        valid_ids = [
            image_id for image_id in planned.imagem_ids
            if image_id in by_id and image_id not in used_images
        ]
        if not valid_ids:
            continue
        mode = planned.modo_exibicao
        if mode == 'lado_a_lado' and len(valid_ids) != 2:
            mode = 'normal'
        blocks.append(ContentBlock(
            tipo='imagem',
            imagem_id=valid_ids[0],
            imagem_secundaria_id=valid_ids[1] if len(valid_ids) > 1 else None,
            legenda=planned.legenda,
            modo_exibicao=mode,
            confianca=planned.confianca,
            motivo=planned.motivo,
        ))
        used_images.update(valid_ids)
    try:
        return CompositionDocument(blocks=blocks, source='manual')
    except Exception as exc:
        raise _contextual_error(
            exc,
            provider=provider_name,
            model=planning_model,
            stage='criacao_composition_document',
            message='Não foi possível montar a estrutura da novidade.',
        ) from exc


async def generate_automatic_novelty(
    provider: AIProvider,
    *,
    api_key: str,
    text_model: str,
    image_model: str,
    raw_text: str,
    images: list[dict[str, Any]],
    workspace: SessionWorkspace,
    anonymization_prompt: str,
    novelty_prompt: str,
    run_sync: Callable[..., Awaitable[Any]],
    on_progress: Callable[[str, int, int], None] | None = None,
) -> CompositionDocument:
    provider_name = getattr(provider, 'name', provider.__class__.__name__.lower())
    planning_model = configured_text_model(provider_name, text_model)
    generation_started = monotonic()
    logger.info(
        'Início da geração; provider=%s image_model=%s planning_model=%s images=%d',
        provider_name, image_model, planning_model, len(images),
    )
    successful: list[dict[str, Any]] = []
    if on_progress:
        on_progress('organizando_texto', 1, 7)
    last_image_error: ProviderError | None = None
    last_image_cause: BaseException | None = None
    for position, item in enumerate(images, 1):
        if on_progress:
            on_progress('anonimizando_imagens', position, len(images))
        item.update(status='Processando', error='')
        image_started = monotonic()
        logger.info(
            'Início da anonimização; provider=%s model=%s image=%s position=%d',
            provider_name, image_model, item['id'], position,
        )
        try:
            await run_sync(
                provider.treat_image,
                api_key=api_key,
                model=image_model,
                prompt=anonymization_prompt,
                source=item['original'],
                destination=item['treated'],
            )
            item['status'] = 'Tratada'
            successful.append(item)
            logger.info(
                'Fim da anonimização; provider=%s model=%s image=%s duration=%.3fs',
                provider_name, image_model, item['id'], monotonic() - image_started,
            )
        except Exception as exc:
            contextual = _contextual_error(
                exc,
                provider=provider_name,
                model=image_model,
                stage='anonimizacao',
                message=f'Não foi possível anonimizar a imagem {position}.',
                api_key=api_key,
                image_id=str(item['id']),
            )
            last_image_error = contextual
            last_image_cause = exc
            item.update(status='Erro', error=contextual.user_message)
            logger.error(
                'Falha na anonimização; provider=%s model=%s image=%s duration=%.3fs '
                'status_http=%s exception=%s detail=%s',
                provider_name, image_model, item['id'], monotonic() - image_started,
                contextual.status_http, contextual.exception_type,
                contextual.technical_detail,
            )
    if images and not successful:
        if last_image_error is not None:
            raise last_image_error from last_image_cause
        raise ProviderError(
            'Nenhuma imagem pôde ser tratada.',
            code='all_images_failed',
            provider=provider_name,
            model=image_model,
            stage='anonimizacao',
        )
    if on_progress:
        on_progress('analisando_imagens', 3, 7)
    analysis_started = monotonic()
    logger.info(
        'Início da análise multimodal; provider=%s model=%s images=%d',
        provider_name, planning_model, len(successful),
    )
    try:
        plan = await run_sync(
            provider.generate_novelty_plan,
            api_key=api_key,
            model=planning_model,
            raw_text=raw_text,
            images=[(item['id'], item['treated']) for item in successful],
            prompt=novelty_prompt,
        )
    except Exception as exc:
        contextual = _contextual_error(
            exc,
            provider=provider_name,
            model=planning_model,
            stage='analise_multimodal_resposta_estruturada',
            message=(
                f'Não foi possível analisar o texto e as imagens com '
                f'{provider_name.title()}.'
            ),
            api_key=api_key,
        )
        logger.error(
            'Falha na análise multimodal; provider=%s model=%s duration=%.3fs '
            'status_http=%s exception=%s detail=%s',
            provider_name, planning_model, monotonic() - analysis_started,
            contextual.status_http, contextual.exception_type,
            contextual.technical_detail,
        )
        raise contextual from exc
    logger.info(
        'Fim da análise multimodal; provider=%s model=%s duration=%.3fs',
        provider_name, planning_model, monotonic() - analysis_started,
    )

    if on_progress:
        on_progress('criando_titulo_subtitulos', 2, 7)
        on_progress('criando_marcacoes', 5, 7)
        on_progress('posicionando_imagens', 6, 7)
        on_progress('montando_publicacao', 7, 7)
    composition_started = monotonic()
    logger.info('Início da montagem; provider=%s model=%s', provider_name, planning_model)
    try:
        document = apply_novelty_plan(
            plan,
            successful,
            workspace,
            provider_name=provider_name,
            planning_model=planning_model,
        )
    except ProviderError as exc:
        logger.error(
            'Falha na montagem; provider=%s model=%s duration=%.3fs stage=%s '
            'exception=%s image=%s detail=%s',
            provider_name, planning_model, monotonic() - composition_started,
            exc.stage, exc.exception_type, exc.image_id, exc.technical_detail,
        )
        raise
    logger.info(
        'Fim da montagem; provider=%s model=%s duration=%.3fs',
        provider_name, planning_model, monotonic() - composition_started,
    )
    missing_ids = [
        str(item['id']) for item in successful
        if str(item['id']) not in document.used_image_ids()
    ]
    if missing_ids:
        for image_id in missing_ids:
            document.add('imagem', imagem_id=image_id)
        document.fallback_image_ids = list(missing_ids)
        document._undo.clear()
        logger.warning(
            'Local placement fallback; provider=%s model=%s images=%s',
            provider_name, planning_model, ','.join(missing_ids),
        )
    if not document.title or not any(
        block.tipo == 'texto' and block.conteudo.strip() for block in document.blocks
    ):
        raise ProviderError(
            'A IA não retornou título e texto completos. Gere novamente.',
            code='incomplete_novelty_text',
            provider=provider_name,
            model=planning_model,
            stage='criacao_composition_document',
            exception_type='IncompleteNoveltyText',
        )
    logger.info(
        'Fim da geração; provider=%s image_model=%s planning_model=%s duration=%.3fs',
        provider_name, image_model, planning_model, monotonic() - generation_started,
    )
    return document
