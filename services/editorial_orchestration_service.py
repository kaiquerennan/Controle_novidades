from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from hashlib import sha256
import json
import logging
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from PIL import Image

from models.composition import CompositionDocument, ContentBlock
from models.editorial_source import EditorialUnit
from models.novelty_plan import FidelityReview, NoveltyPlan, PlannedBlock
from services.image_service import SessionWorkspace, safe_path
from services.api_usage_service import GenerationApiAudit
from services.image_analysis_cache_service import (
    ImageAnalysisCache,
    StructuredImageDescription,
)
from services.editorial_prompts import (
    COVER_PROMPT,
    IMAGE_DESCRIPTION_INSTRUCTION,
    LOCALIZED_CORRECTION_PROMPT,
    REVIEWER_PROMPT,
    WRITER_ORGANIZER_PROMPT,
)
from services.editorial_structure_service import (
    extract_editorial_units,
    structured_source_prompt,
)
from services.model_routing import (
    configured_image_model,
    configured_text_model,
    is_model_availability_error,
    text_model_unavailable_error,
)
from services.system_path_formatter import (
    PATH_LABEL,
    format_system_navigation,
    missing_system_paths,
)
from services.text_sanitizer import markdown_highlights, strip_markdown
from services.providers.base_provider import AIProvider
from services.providers.exceptions import (
    ProviderError,
    sanitize_technical_detail,
    sanitized_exception_chain,
)


logger = logging.getLogger('EMC Novidade Visual IA')

PLAN_MAX_OUTPUT_TOKENS = 10500
CORRECTION_MAX_OUTPUT_TOKENS = 6800
REVIEW_MAX_OUTPUT_TOKENS = 6500


# O modelo às vezes entra em repetição e devolve JSON truncado, e o provedor
# responde 503 em picos de demanda. Nos dois casos a chamada seguinte costuma
# funcionar, então vale repetir antes de perder a geração inteira.
AI_MAX_ATTEMPTS = 3
AI_RETRY_DELAY_SECONDS = 2.0
RETRYABLE_PROVIDER_CODES = frozenset({
    'invalid_schema_response',
    'provider_unavailable',
    'gemini_empty_novelty_plan',
})


def _review_dict(review: FidelityReview) -> dict[str, Any]:
    return {
        **review.model_dump(),
        'status': 'concluida',
        'correcao_automatica_realizada': False,
    }


def _compact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item for key, item in value.items()
        if item not in ('', None, [], {})
    }


def _compact_block(block: ContentBlock | dict[str, Any]) -> dict[str, Any]:
    value = block.to_dict() if isinstance(block, ContentBlock) else dict(block)
    return _compact_mapping(value)


def _compact_document(
    document: CompositionDocument, blocks: list[ContentBlock] | None = None,
) -> dict[str, Any]:
    selected = list(blocks if blocks is not None else document.blocks)
    return {
        'origem': document.source,
        'titulo': document.title,
        'imagens_fallback': list(document.fallback_image_ids),
        'blocos': [
            {'ordem': document.blocks.index(block) + 1, **_compact_block(block)}
            for block in selected
        ],
    }


def _compact_unit(unit: EditorialUnit) -> dict[str, Any]:
    return _compact_mapping(unit.to_dict())


def _block_metadata(planned: Any) -> dict[str, str]:
    return {
        'secao_id': str(getattr(planned, 'secao_id', '') or ''),
        'rotina': str(getattr(planned, 'rotina', '') or ''),
        'caminho': str(getattr(planned, 'caminho', '') or ''),
    }


def _section_text(blocks: list[ContentBlock]) -> str:
    values: list[str] = []
    for block in blocks:
        values.append(block.conteudo)
        values.extend(str(item.get('texto') or '') for item in block.itens)
    return '\n'.join(values).casefold()


def document_from_editorial_plan(
    plan: NoveltyPlan,
    images: list[dict[str, Any]],
    source_units: list[EditorialUnit] | None = None,
) -> CompositionDocument:
    by_id = {str(item['id']): item for item in images}
    blocks: list[ContentBlock] = []
    used: set[str] = set()
    for planned in plan.blocos:
        metadata = _block_metadata(planned)
        block_id = str(getattr(planned, 'bloco_id', '') or '')
        if planned.tipo == 'lista':
            blocks.append(ContentBlock(
                tipo='lista', id=block_id or ContentBlock(tipo='lista', itens=[]).id,
                itens=[item.model_dump() for item in planned.itens],
                papel=planned.papel or 'lista', **metadata,
            ))
            continue
        if planned.tipo != 'imagem':
            raw_content = planned.conteudo
            highlights = [
                *markdown_highlights(raw_content),
                *[str(value) for value in planned.destaques],
            ]
            content = strip_markdown(format_system_navigation(raw_content))
            if planned.tipo != 'texto':
                blocks.append(ContentBlock(
                    tipo=planned.tipo, id=block_id or ContentBlock(tipo=planned.tipo).id,
                    conteudo=content, destaques=list(dict.fromkeys(highlights)),
                    papel=planned.papel, **metadata,
                ))
                continue
            lines = content.splitlines()
            pending: list[str] = []
            pending_list: list[dict[str, Any]] = []
            text_role = planned.papel

            def flush_pending() -> None:
                nonlocal text_role
                value = '\n'.join(pending).strip()
                pending.clear()
                if value:
                    blocks.append(ContentBlock(
                        tipo='texto', conteudo=value, papel=text_role,
                        destaques=list(dict.fromkeys(highlights)), **metadata,
                    ))
                    text_role = ''

            def flush_list() -> None:
                nonlocal text_role
                if pending_list:
                    blocks.append(ContentBlock(
                        tipo='lista', itens=list(pending_list), papel='lista', **metadata,
                    ))
                    pending_list.clear()
                    text_role = ''

            index = 0
            while index < len(lines):
                line = lines[index]
                list_match = __import__('re').match(
                    r'^\s*(?:[-+*•]|\d+[.)])\s+(.+)$', line,
                )
                if list_match:
                    flush_pending()
                    item_raw = list_match.group(1)
                    pending_list.append({
                        'texto': strip_markdown(item_raw),
                        'destaques': markdown_highlights(item_raw),
                    })
                    index += 1
                    continue
                flush_list()
                if (
                    line.strip().casefold() == PATH_LABEL.casefold()
                    and index + 1 < len(lines)
                ):
                    flush_pending()
                    path = lines[index + 1].strip()
                    blocks.append(ContentBlock(
                        tipo='texto', papel='caminho', conteudo=f'{PATH_LABEL}\n{path}',
                        caminho=path, secao_id=metadata['secao_id'],
                        rotina=metadata['rotina'],
                    ))
                    index += 2
                    continue
                pending.append(line)
                index += 1
            flush_list()
            flush_pending()
            continue
        valid_ids = [
            str(image_id) for image_id in planned.imagem_ids
            if str(image_id) in by_id and str(image_id) not in used
        ][:2]
        if not valid_ids:
            continue
        mode = planned.modo_exibicao
        if mode == 'lado_a_lado' and len(valid_ids) != 2:
            mode = 'normal'
        blocks.append(ContentBlock(
            tipo='imagem', id=block_id or ContentBlock(tipo='imagem', imagem_id=valid_ids[0]).id,
            imagem_id=valid_ids[0],
            imagem_secundaria_id=valid_ids[1] if len(valid_ids) > 1 else None,
            legenda=strip_markdown(planned.legenda), modo_exibicao=mode,
            confianca=planned.confianca, motivo=planned.motivo, **metadata,
        ))
        used.update(valid_ids)

    document = CompositionDocument(blocks=blocks, source='manual')
    missing = [str(item['id']) for item in images if str(item['id']) not in used]
    for image_id in missing:
        document.blocks.append(ContentBlock(tipo='imagem', imagem_id=image_id))
    document.fallback_image_ids = missing
    document._undo.clear()
    if not document.title or not document.introduction:
        raise ProviderError(
            'Não foi possível organizar o texto da novidade. '
            'O conteúdo original e as imagens foram preservados.',
            code='incomplete_editorial_text', stage='redator_tecnico',
        )
    return document


def _append_missing_paths(
    document: CompositionDocument, paths: list[str], units: list[EditorialUnit] | None = None,
) -> None:
    """Garante que a navegação informada na origem apareça na matéria.

    Só o caminho é reposto: onde a alteração está disponível é informação que o
    usuário procura. Nenhum outro conteúdo da ocorrência volta para o texto.
    """
    for path in paths:
        document.blocks.append(ContentBlock(
            tipo='texto', papel='caminho', conteudo=f'{PATH_LABEL}\n{path}', caminho=path,
        ))


def _merge_corrected_document(
    original: CompositionDocument,
    corrected: CompositionDocument,
    review: FidelityReview,
) -> CompositionDocument:
    target_ids = set(review.blocos_para_corrigir)
    target_sections = set(review.secoes_para_corrigir)
    replacements_by_id = {block.id: block for block in corrected.blocks if block.id in target_ids}
    corrected_sections = {
        section: [block for block in corrected.blocks if block.secao_id == section]
        for section in target_sections
    }
    if not replacements_by_id and not any(corrected_sections.values()):
        return original
    result: list[ContentBlock] = []
    inserted_sections: set[str] = set()
    for block in original.blocks:
        if block.secao_id in target_sections:
            if block.secao_id not in inserted_sections:
                result.extend(corrected_sections.get(block.secao_id) or [block])
                inserted_sections.add(block.secao_id)
            continue
        result.append(replacements_by_id.get(block.id, block))
    merged = CompositionDocument(
        blocks=result, source=original.source,
        fallback_image_ids=original.fallback_image_ids,
        cover=original.cover, review=original.review,
    )
    return merged

class EditorialOrchestrator:
    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self._image_cache = ImageAnalysisCache()

    def _key(
        self,
        provider: AIProvider,
        model: str,
        raw_text: str,
        image_fingerprints: list[tuple[str, str]],
        cover_requested: bool,
    ) -> str:
        digest = sha256()
        digest.update(getattr(provider, 'name', provider.__class__.__name__).encode())
        digest.update(model.encode())
        digest.update(raw_text.encode('utf-8'))
        digest.update(str(cover_requested).encode())
        for image_id, file_hash in image_fingerprints:
            digest.update(image_id.encode())
            digest.update(file_hash.encode())
        return digest.hexdigest()

    def _prepare_images(
        self, images: list[dict[str, Any]], workspace: SessionWorkspace,
    ) -> tuple[list[dict[str, Any]], dict[str, str], list[tuple[str, str]]]:
        unique: list[dict[str, Any]] = []
        id_to_hash: dict[str, str] = {}
        fingerprints: list[tuple[str, str]] = []
        seen_files: set[str] = set()
        self._image_cache.root.mkdir(parents=True, exist_ok=True)
        self._image_cache.thumbnail_dir.mkdir(parents=True, exist_ok=True)
        for item in images:
            image_id = str(item['id'])
            source = safe_path(Path(item['original']))
            source_bytes = source.read_bytes()
            file_hash = sha256(source_bytes).hexdigest()
            id_to_hash[image_id] = file_hash
            fingerprints.append((image_id, file_hash))
            item.update(
                treated=source,
                final=source,
                annotated=None,
                status='Preservada sem alterações',
                error='',
            )
            if file_hash in seen_files:
                continue
            seen_files.add(file_hash)
            thumbnail_path = self._image_cache.thumbnail_path(file_hash)
            if not thumbnail_path.exists():
                with Image.open(source) as opened:
                    thumbnail = opened.convert('RGB')
                    thumbnail.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                    thumbnail.save(thumbnail_path, 'PNG', optimize=True)
            unique.append({
                'id': image_id,
                'source': source,
                'thumbnail': thumbnail_path,
                'file_hash': file_hash,
            })
        return unique, id_to_hash, fingerprints

    @staticmethod
    def _description_from_plan(
        plan: NoveltyPlan, image_id: str, file_hash: str,
    ) -> StructuredImageDescription:
        planned = next(
            (item for item in plan.imagens if str(item.imagem_id) == image_id), None,
        )
        block = next(
            (item for item in plan.blocos if image_id in item.imagem_ids), None,
        )
        description = str(getattr(planned, 'descricao', '') or '').strip()
        if not description and block:
            description = str(block.legenda or block.motivo or '').strip()
        if not description:
            description = 'Captura de tela vinculada à novidade, sem descrição textual adicional.'
        return StructuredImageDescription(
            file_hash=file_hash,
            description=description,
            visible_text=[str(value) for value in getattr(planned, 'texto_visivel', [])],
            elements=[str(value) for value in getattr(planned, 'elementos', [])],
        )

    @staticmethod
    def _minimal_source_excerpt(raw_text: str, corrections: list[str], limit: int = 1800) -> str:
        terms = {
            word.casefold() for word in __import__('re').findall(r'[\wÀ-ÿ-]{4,}', ' '.join(corrections))
        }
        candidates = [line.strip() for line in raw_text.splitlines() if line.strip()]
        ranked = sorted(
            enumerate(candidates),
            key=lambda pair: (
                -sum(term in pair[1].casefold() for term in terms), pair[0]
            ),
        )
        selected: list[str] = []
        size = 0
        for _, line in ranked:
            if selected and not any(term in line.casefold() for term in terms):
                continue
            if size + len(line) > limit:
                continue
            selected.append(line)
            size += len(line) + 1
            if size >= limit:
                break
        if not selected and candidates:
            selected.append(candidates[0][:limit])
        return '\n'.join(selected)

    @staticmethod
    def _target_blocks(
        document: CompositionDocument, review: FidelityReview, maximum: int = 8,
    ) -> list[ContentBlock]:
        target_ids = set(review.blocos_para_corrigir)
        target_sections = set(review.secoes_para_corrigir)
        selected = [
            block for block in document.blocks
            if block.id in target_ids or (block.secao_id and block.secao_id in target_sections)
        ]
        if selected:
            return selected[:maximum]
        correction_terms = {
            word.casefold()
            for word in __import__('re').findall(
                r'[\wÀ-ÿ-]{4,}', ' '.join(review.correcoes_necessarias)
            )
        }
        ranked = sorted(
            (
                (
                    sum(term in json.dumps(block.to_dict(), ensure_ascii=False).casefold()
                        for term in correction_terms),
                    index,
                    block,
                )
                for index, block in enumerate(document.blocks)
                if block.tipo != 'imagem'
            ),
            key=lambda value: (-value[0], value[1]),
        )
        return [value[2] for value in ranked[:maximum]]

    async def generate(
        self,
        provider: AIProvider,
        *,
        api_key: str,
        text_model: str,
        image_model: str,
        raw_text: str,
        images: list[dict[str, Any]],
        workspace: SessionWorkspace,
        cover_requested: bool,
        run_sync: Callable[..., Awaitable[Any]],
        on_progress: Callable[[str], None] | None = None,
        generation_id: str = '',
        idempotency_key: str = '',
    ) -> CompositionDocument:
        provider_name = getattr(provider, 'name', provider.__class__.__name__).lower()
        text_model = configured_text_model(provider_name, text_model)
        # O modelo de imagem só é exigido quando a capa for gerada. Provedores
        # somente texto, como a DeepSeek, não têm um, e cobrá-lo aqui impediria
        # até a geração sem capa.
        image_model = (
            configured_image_model(provider_name, image_model) if cover_requested else ''
        )
        routed_models = f'text={text_model}|image={image_model or "não utilizado"}'
        generation_id = generation_id or uuid4().hex
        preparation_started = time.perf_counter()
        prepared_images, id_to_hash, fingerprints = await run_sync(
            self._prepare_images, images=images, workspace=workspace,
        )
        cache_key = self._key(
            provider, routed_models, raw_text, fingerprints, cover_requested,
        )
        idempotency_key = idempotency_key or cache_key
        audit = GenerationApiAudit(generation_id, idempotency_key)
        audit.started = preparation_started
        audit.record_stage('preparacao', time.perf_counter() - preparation_started)
        processing_started = time.perf_counter()
        stage_started = processing_started
        active_stage = ''
        ai_calls = 0
        logger.info(
            '[PROCESSAMENTO] Início; geracao=%s idempotencia=%s sessao=%s provider=%s '
            'texto_bytes=%s imagens=%s capa=%s',
            generation_id, idempotency_key, workspace.session_id, provider_name,
            len(raw_text.encode('utf-8')), len(images), bool(cover_requested),
        )
        logger.info('[MODELOS] Texto: Modelo: %s', text_model)
        logger.info('[MODELOS] Imagem: Modelo: %s', image_model or 'não utilizado')

        def progress(stage: str) -> None:
            nonlocal active_stage, stage_started
            now = time.perf_counter()
            if active_stage:
                duration = now - stage_started
                logger.info('[PROCESSAMENTO] %s concluída em %.2f s', active_stage, duration)
                audit.record_stage(active_stage, duration)
            active_stage = stage
            stage_started = now
            logger.info('[PROCESSAMENTO] %s iniciada', stage)
            if on_progress:
                on_progress(stage)

        async def execute_ai(operation: str, function: Callable[..., Any], **kwargs: Any) -> Any:
            """Executa uma chamada de IA, repetindo falhas reconhecidamente transitórias.

            O modelo às vezes entra em repetição e devolve um JSON truncado, e o
            provedor responde 503 em picos de demanda. Nos dois casos a chamada
            seguinte costuma funcionar, então uma nova tentativa evita perder a
            geração inteira.
            """
            ultimo_erro: ProviderError | None = None
            for tentativa in range(1, AI_MAX_ATTEMPTS + 1):
                try:
                    return await _execute_ai_once(operation, function, tentativa, **kwargs)
                except ProviderError as exc:
                    ultimo_erro = exc
                    if exc.code not in RETRYABLE_PROVIDER_CODES:
                        raise
                    if tentativa >= AI_MAX_ATTEMPTS:
                        raise
                    logger.warning(
                        '[PROCESSAMENTO] Tentativa %s de %s falhou em %s (%s); repetindo.',
                        tentativa, AI_MAX_ATTEMPTS, operation, exc.code,
                    )
                    await asyncio.sleep(AI_RETRY_DELAY_SECONDS * tentativa)
            raise ultimo_erro if ultimo_erro else RuntimeError('Falha inesperada na chamada de IA.')

        async def _execute_ai_once(
            operation: str, function: Callable[..., Any], attempt: int, **kwargs: Any
        ) -> Any:
            nonlocal ai_calls
            ai_calls += 1
            endpoint = (
                '/v1/images/generations' if operation == 'capa'
                else '/v1/responses'
            )
            model = str(kwargs.get('model') or '')
            audit.start_call(ai_calls, operation, endpoint, model)
            provider.set_api_audit_context(audit, ai_calls)
            text_bytes = sum(
                len(str(kwargs.get(key) or '').encode('utf-8'))
                for key in ('raw_text', 'prompt')
            )
            if kwargs.get('document') is not None:
                text_bytes += len(json.dumps(
                    kwargs['document'], ensure_ascii=False, separators=(',', ':'),
                ).encode('utf-8'))
            image_bytes = sum(
                path.stat().st_size for _, path in kwargs.get('images', []) if path.is_file()
            )
            request_started = time.perf_counter()
            logger.info(
                '[PROCESSAMENTO] Chamada IA %s iniciada; geracao=%s operacao=%s modelo=%s '
                'endpoint=%s tentativa=%s de %s texto_bytes=%s imagens_bytes=%s',
                ai_calls, generation_id, operation, model, endpoint,
                attempt, AI_MAX_ATTEMPTS, text_bytes, image_bytes,
            )
            error: BaseException | None = None
            try:
                result = await run_sync(function, **kwargs)
                received_bytes = (
                    result.stat().st_size
                    if isinstance(result, Path) and result.is_file()
                    else 0
                )
                logger.info(
                    '[PROCESSAMENTO] Chamada IA %s concluída em %.2f s; operacao=%s '
                    'endpoint=%s recebido_bytes=%s',
                    ai_calls, time.perf_counter() - request_started,
                    operation, endpoint, received_bytes,
                )
                return result
            except Exception as exc:
                error = exc
                logger.error(
                    '[PROCESSAMENTO] Chamada IA %s falhou em %.2f s; operacao=%s '
                    'endpoint=%s timeout=%s excecao=%s',
                    ai_calls, time.perf_counter() - request_started, operation, endpoint,
                    exc.__class__.__name__ == 'APITimeoutError'
                    or 'timeout' in str(exc).casefold(),
                    exc.__class__.__name__,
                )
                raise
            finally:
                provider.clear_api_audit_context()
                audit.finish_call(ai_calls, error)

        try:
            if cache_key in self._cache:
                logger.info(
                    '[PROCESSAMENTO] Cache concluído em %.2f s; chamadas_ia=0',
                    time.perf_counter() - processing_started,
                )
                return CompositionDocument.from_dict(self._cache[cache_key])

            source_units = extract_editorial_units(raw_text)
            editorial_input = structured_source_prompt(raw_text, source_units)
            descriptions_by_hash = {
                prepared['file_hash']: self._image_cache.load(prepared['file_hash'])
                for prepared in prepared_images
            }
            uncached = [
                prepared for prepared in prepared_images
                if descriptions_by_hash[prepared['file_hash']] is None
            ]
            descriptions_by_id: dict[str, StructuredImageDescription] = {}
            for image_id, file_hash in id_to_hash.items():
                cached = descriptions_by_hash.get(file_hash)
                if cached:
                    descriptions_by_id[image_id] = cached
            cached_prompt = [
                description.to_prompt_dict(image_id)
                for image_id, description in descriptions_by_id.items()
            ]
            if cached_prompt:
                editorial_input += '\n\nDESCRIÇÕES DE IMAGENS JÁ ANALISADAS:\n' + json.dumps(
                    cached_prompt, ensure_ascii=False, separators=(',', ':'),
                )
            if uncached:
                # A instrucao de analise vai no conteudo, nao no prompt: o prompt
                # precisa ser identico em toda chamada para o provedor reaproveitar
                # o cache do prefixo.
                editorial_input += '\n\n' + IMAGE_DESCRIPTION_INSTRUCTION

            progress('organizacao')
            try:
                plan = await execute_ai(
                    'organizacao', provider.generate_novelty_plan,
                    api_key=api_key, model=text_model, raw_text=editorial_input,
                    images=[(item['id'], item['thumbnail']) for item in uncached],
                    prompt=WRITER_ORGANIZER_PROMPT,
                    max_output_tokens=PLAN_MAX_OUTPUT_TOKENS,
                )
                for prepared in uncached:
                    description = self._description_from_plan(
                        plan, prepared['id'], prepared['file_hash'],
                    )
                    self._image_cache.save(description)
                    descriptions_by_hash[prepared['file_hash']] = description
                descriptions_by_id = {
                    image_id: descriptions_by_hash[file_hash]
                    for image_id, file_hash in id_to_hash.items()
                    if descriptions_by_hash.get(file_hash) is not None
                }
                document = document_from_editorial_plan(plan, images, source_units)
                required_path_corrections = missing_system_paths(raw_text, document.text)
            except Exception as exc:
                if is_model_availability_error(exc):
                    raise text_model_unavailable_error(provider_name, text_model, exc) from exc
                detail = sanitized_exception_chain(exc, api_key)
                raise ProviderError(
                    'Não foi possível organizar o texto da novidade. '
                    'O conteúdo original e as imagens foram preservados.',
                    code=getattr(exc, 'code', 'editorial_generation_failed'),
                    technical_detail=sanitize_technical_detail(detail, api_key),
                    provider=getattr(provider, 'name', ''), model=text_model,
                    stage='redator_organizador',
                    status_http=getattr(exc, 'status_http', None),
                    exception_type=exc.__class__.__name__,
                ) from exc

            image_description_payload = [
                description.to_prompt_dict(image_id)
                for image_id, description in descriptions_by_id.items()
            ]
            progress('revisao')
            try:
                review = await execute_ai(
                    'revisao', provider.review_novelty,
                    api_key=api_key, model=text_model, raw_text=raw_text,
                    document=_compact_document(document),
                    image_ids=[str(item['id']) for item in images],
                    **({'image_descriptions': image_description_payload} if provider_name == 'openai' else {}),
                    prompt=REVIEWER_PROMPT,
                    max_output_tokens=REVIEW_MAX_OUTPUT_TOKENS,
                )
                if required_path_corrections:
                    path_sections = [
                        unit.id for unit in source_units
                        if unit.caminho in required_path_corrections
                    ]
                    review = review.model_copy(update={
                        'aprovada': False,
                        'correcoes_necessarias': [
                            *review.correcoes_necessarias,
                            *[
                                f'Restaurar junto da rotina correta: {PATH_LABEL} {path}'
                                for path in required_path_corrections
                            ],
                        ],
                        'secoes_para_corrigir': list(dict.fromkeys([
                            *review.secoes_para_corrigir, *path_sections,
                        ])),
                    })
                document.review = _review_dict(review)
                if not review.aprovada and review.correcoes_necessarias:
                    targets = self._target_blocks(document, review)
                    target_ids = [block.id for block in targets]
                    target_sections = list(dict.fromkeys(
                        block.secao_id for block in targets if block.secao_id
                    ))
                    effective_review = review.model_copy(update={
                        'blocos_para_corrigir': list(dict.fromkeys([
                            *review.blocos_para_corrigir, *target_ids,
                        ])),
                        'secoes_para_corrigir': list(dict.fromkeys([
                            *review.secoes_para_corrigir, *target_sections,
                        ])),
                    })
                    correction_payload = {
                        'correcoes': list(effective_review.correcoes_necessarias),
                        'blocos_alvo': [_compact_block(block) for block in targets],
                        'trechos_fonte': self._minimal_source_excerpt(
                            raw_text, effective_review.correcoes_necessarias,
                        ),
                    }
                    progress('correcao')
                    corrected_plan = await execute_ai(
                        'correcao', provider.generate_novelty_plan,
                        api_key=api_key, model=text_model,
                        raw_text=json.dumps(
                            correction_payload, ensure_ascii=False, separators=(',', ':'),
                        ),
                        images=[], prompt=LOCALIZED_CORRECTION_PROMPT,
                        max_output_tokens=CORRECTION_MAX_OUTPUT_TOKENS,
                    )
                    validation_plan = NoveltyPlan(blocos=[
                        PlannedBlock(tipo='titulo', conteudo=document.title),
                        PlannedBlock(
                            tipo='texto', conteudo=document.introduction or 'Conteúdo em correção.',
                            papel='introducao',
                        ),
                        *corrected_plan.blocos,
                    ])
                    corrected_candidate = document_from_editorial_plan(
                        validation_plan, [], None,
                    )
                    document = _merge_corrected_document(
                        document, corrected_candidate, effective_review,
                    )
                    _append_missing_paths(
                        document, missing_system_paths(raw_text, document.text), source_units,
                    )
                    progress('segunda_revisao')
                    second_review = await execute_ai(
                        'segunda_revisao', provider.review_novelty,
                        api_key=api_key, model=text_model, raw_text=raw_text,
                        document=document.to_dict(),
                        image_ids=[str(item['id']) for item in images],
                        **({'image_descriptions': image_description_payload} if provider_name == 'openai' else {}),
                        prompt=REVIEWER_PROMPT,
                        max_output_tokens=REVIEW_MAX_OUTPUT_TOKENS,
                    )
                    document.review = {
                        **_review_dict(second_review),
                        'status': (
                            'concluida' if second_review.aprovada
                            else 'revisao_manual_necessaria'
                        ),
                        'correcao_automatica_realizada': True,
                        'avisos': [
                            *second_review.avisos,
                            *([] if second_review.aprovada else [
                                'A correção foi revisada novamente e ainda exige revisão manual.'
                            ]),
                        ],
                    }
            except Exception as exc:
                if is_model_availability_error(exc):
                    raise text_model_unavailable_error(provider_name, text_model, exc) from exc
                _append_missing_paths(
                    document, missing_system_paths(raw_text, document.text), source_units,
                )
                logger.warning(
                    'Editorial review unavailable; provider=%s exception=%s',
                    getattr(provider, 'name', ''), exc.__class__.__name__,
                )
                document.review = {
                    'aprovada': False,
                    'status': 'nao_concluida',
                    'correcao_automatica_realizada': False,
                    'avisos': [
                        'A matéria foi gerada, mas a revisão automática não pôde ser '
                        'concluída. Revise o conteúdo antes de aprovar.'
                    ],
                }

            document.cover = {
                'solicitada': bool(cover_requested),
                'status': 'nao_gerada',
                'tema': document.title,
                'arquivo': '',
                'mensagem': '',
            }
            if cover_requested:
                progress('capa')
                try:
                    cover_path = await execute_ai(
                        'capa', provider.generate_cover,
                        api_key=api_key, model=image_model,
                        prompt=f'{COVER_PROMPT}\nTEMA: {document.title}\nTITULO: {document.title}',
                        destination=workspace.export_dir / 'capa' / 'capa.png',
                    )
                    document.cover.update(status='gerada', arquivo=str(cover_path))
                except Exception as exc:
                    logger.warning(
                        'Cover generation unavailable; provider=%s exception=%s',
                        getattr(provider, 'name', ''), exc.__class__.__name__,
                    )
                    document.cover.update(
                        status='falhou',
                        mensagem='A matéria foi gerada, mas não foi possível criar a imagem de capa.',
                    )

            progress('preparacao_visualizacao')
            document._undo.clear()
            self._cache[cache_key] = document.to_dict()
            now = time.perf_counter()
            if active_stage:
                duration = now - stage_started
                logger.info('[PROCESSAMENTO] %s concluída em %.2f s', active_stage, duration)
                audit.record_stage(active_stage, duration)
                active_stage = ''
            logger.info(
                '[PROCESSAMENTO] Total concluído em %.2f s; chamadas_ia=%s',
                now - processing_started, ai_calls,
            )
            return document
        finally:
            if active_stage:
                audit.record_stage(active_stage, time.perf_counter() - stage_started)
                active_stage = ''
            audit.log_summary()
