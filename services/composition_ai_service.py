from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from models.composition import CompositionDocument, ContentBlock
from services.providers.base_provider import AIProvider


TEXT_REVIEW_PROMPT = """Revise apenas o conte\u00fado editorial da novidade em portugu\u00eas do Brasil.
Corrija ortografia, melhore a clareza, organize par\u00e1grafos e sugira subt\u00edtulos quando \u00fateis.
Preserve rigorosamente o sentido t\u00e9cnico, nomes de campos, menus, regras e funcionalidades.
N\u00e3o invente informa\u00e7\u00f5es. Retorne somente blocos titulo, subtitulo, texto e divisor.
N\u00e3o retorne imagens nem marca\u00e7\u00f5es."""

IMAGE_PLACEMENT_PROMPT = """Sugira somente o posicionamento das imagens na composi\u00e7\u00e3o fornecida.
N\u00e3o reescreva o texto. Analise o texto, os subt\u00edtulos e tudo que estiver vis\u00edvel nas imagens.
Para cada imagem com v\u00ednculo claro, retorne um bloco imagem com imagem_ids, apos_bloco,
confianca entre 0 e 1 e motivo curto. Use lado_a_lado somente para duas imagens relacionadas.
Quando a confian\u00e7a for baixa, use confian\u00e7a abaixo de 0.6 e n\u00e3o invente v\u00ednculo.
Use exclusivamente IDs de blocos e imagens fornecidos. N\u00e3o retorne marca\u00e7\u00f5es."""


async def review_composition_text(
    provider: AIProvider,
    *,
    api_key: str,
    model: str,
    document: CompositionDocument,
    run_sync: Callable[..., Awaitable[Any]],
) -> list[ContentBlock]:
    plan = await run_sync(
        provider.generate_novelty_plan,
        api_key=api_key,
        model=model,
        raw_text=json.dumps(document.to_dict(), ensure_ascii=False),
        images=[],
        prompt=TEXT_REVIEW_PROMPT,
    )
    return [
        ContentBlock(tipo=block.tipo, conteudo=block.conteudo)
        for block in plan.blocos
        if block.tipo in {'titulo', 'subtitulo', 'texto', 'divisor'}
    ]


async def suggest_composition_positions(
    provider: AIProvider,
    *,
    api_key: str,
    model: str,
    document: CompositionDocument,
    images: list[dict[str, Any]],
    run_sync: Callable[..., Awaitable[Any]],
) -> list[dict[str, Any]]:
    usable: list[tuple[str, Path]] = []
    for item in images:
        path = item.get('final') or item.get('annotated') or item.get('treated')
        if path and not item.get('error'):
            usable.append((str(item['id']), Path(path)))
    plan = await run_sync(
        provider.generate_novelty_plan,
        api_key=api_key,
        model=model,
        raw_text=json.dumps(document.to_dict(), ensure_ascii=False),
        images=usable,
        prompt=IMAGE_PLACEMENT_PROMPT,
    )
    suggestions: list[dict[str, Any]] = []
    returned: set[str] = set()
    for block in plan.blocos:
        if block.tipo != 'imagem':
            continue
        ids = [str(value) for value in block.imagem_ids]
        returned.update(ids)
        suggestions.append({
            'imagem_ids': ids,
            'imagem_id': ids[0] if ids else '',
            'apos_bloco': block.apos_bloco,
            'confianca': block.confianca or 0.0,
            'motivo': block.motivo,
            'legenda': block.legenda,
            'modo_exibicao': block.modo_exibicao,
        })
    for image_id, _ in usable:
        if image_id not in returned:
            suggestions.append({
                'imagem_id': image_id,
                'apos_bloco': '',
                'confianca': 0.0,
                'motivo': 'Sem v\u00ednculo claro; posicionamento manual necess\u00e1rio.',
            })
    return suggestions
