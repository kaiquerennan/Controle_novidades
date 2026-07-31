from __future__ import annotations

import io
import json
import math
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont

from models.annotation import Annotation
from models.anonymization_area import AnonymizationArea
from services.html_export_service import build_novelty_html
from services.xml_export_service import build_manifest_xml, build_novelty_xml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / 'data'
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
ALLOWED_FORMATS = {'PNG', 'JPEG', 'WEBP'}


def safe_path(path: Path, parent: Path = PROJECT_ROOT) -> Path:
    resolved = path.resolve()
    root = parent.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f'Caminho rejeitado por sair da raiz permitida: {resolved}')
    return resolved


def safe_filename(name: str) -> str:
    stem = re.sub(r'[^A-Za-z0-9._-]+', '_', Path(name).name).strip('._')
    return (stem or 'imagem')[:120]


def validate_image_bytes(data: bytes) -> tuple[str, tuple[int, int]]:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError('A imagem deve ter no mÃ¡ximo 15 MB.')
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            image_format = (image.format or '').upper()
            size = image.size
    except Exception as exc:
        raise ValueError('O arquivo nÃ£o Ã© uma imagem vÃ¡lida.') from exc
    if image_format not in ALLOWED_FORMATS:
        raise ValueError('Formato nÃ£o permitido. Use PNG, JPG, JPEG ou WEBP.')
    if size[0] < 1 or size[1] < 1 or size[0] * size[1] > 100_000_000:
        raise ValueError('DimensÃµes de imagem invÃ¡lidas ou excessivas.')
    return image_format, size


@dataclass(slots=True)
class SessionWorkspace:
    session_id: str
    original_dir: Path
    processed_dir: Path
    export_dir: Path
    temp_dir: Path

    @classmethod
    def create(cls) -> 'SessionWorkspace':
        return cls.open(uuid4().hex)

    @classmethod
    def open(cls, session_id: str) -> 'SessionWorkspace':
        if not re.fullmatch(r'[0-9a-f]{32}', session_id):
            raise ValueError('Identificador de sessÃ£o invÃ¡lido.')
        folders = {
            'original_dir': DATA_ROOT / 'originals' / session_id,
            'processed_dir': DATA_ROOT / 'processed' / session_id,
            'export_dir': DATA_ROOT / 'exports' / session_id,
            'temp_dir': DATA_ROOT / 'temp' / session_id,
        }
        for folder in folders.values():
            safe_path(folder).mkdir(parents=True, exist_ok=True)
        return cls(session_id=session_id, **folders)

    def clear(self) -> None:
        roots = {
            self.original_dir: DATA_ROOT / 'originals',
            self.processed_dir: DATA_ROOT / 'processed',
            self.export_dir: DATA_ROOT / 'exports',
            self.temp_dir: DATA_ROOT / 'temp',
        }
        for folder, root in roots.items():
            target = safe_path(folder, root)
            if target.exists():
                shutil.rmtree(target)

    def save_original(self, filename: str, data: bytes) -> tuple[Path, tuple[int, int]]:
        image_format, size = validate_image_bytes(data)
        extension = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}[image_format]
        stem = Path(safe_filename(filename)).stem
        target = safe_path(self.original_dir / f'{stem}{extension}')
        suffix = 2
        while target.exists():
            target = safe_path(self.original_dir / f'{stem}_{suffix}{extension}')
            suffix += 1
        target.write_bytes(data)
        return target, size

    def clear_temporary(self) -> None:
        target = safe_path(self.temp_dir, DATA_ROOT / 'temp')
        if target.exists():
            shutil.rmtree(target)


def _rgba(hex_color: str, opacity: float) -> tuple[int, int, int, int]:
    red, green, blue = ImageColor.getrgb(hex_color)
    return red, green, blue, round(255 * opacity)



def _normalized_box(
    start: tuple[int, int], end: tuple[int, int],
) -> tuple[int, int, int, int]:
    x0, y0 = start
    x1, y1 = end
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _scaled_points(
    item: Annotation, width: int, height: int, scale: int,
) -> list[tuple[int, int]]:
    return [(round(x * width * scale), round(y * height * scale)) for x, y in item.points]


def _line_width(item: Annotation, width: int, height: int, scale: int) -> int:
    return max(1, round(item.width * max(width, height) * scale / 1200))


def _arrow_head_length(
    item: Annotation, start: tuple[int, int], end: tuple[int, int], scale: int,
) -> float:
    distance = math.hypot(end[0] - start[0], end[1] - start[1])
    requested = item.arrow_head_size * scale
    return max(8 * scale, min(distance * 0.38, requested + item.width * scale * 2.4))


def _draw_arrow_head(
    draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int],
    color: tuple[int, int, int, int], line_width: int, item: Annotation, scale: int,
) -> None:
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    head = _arrow_head_length(item, start, end, scale)
    spread = math.pi / 7
    left = (
        end[0] - head * math.cos(angle - spread),
        end[1] - head * math.sin(angle - spread),
    )
    right = (
        end[0] - head * math.cos(angle + spread),
        end[1] - head * math.sin(angle + spread),
    )
    if item.arrow_head_style == 'open':
        draw.line([end, left], fill=color, width=line_width)
        draw.line([end, right], fill=color, width=line_width)
    else:
        draw.polygon([end, left, right], fill=color)


def _draw_arrow(
    draw: ImageDraw.ImageDraw, item: Annotation, points: list[tuple[int, int]],
    color: tuple[int, int, int, int], line_width: int, scale: int,
) -> None:
    if len(points) < 2:
        return
    start, end = points[0], points[1]
    draw.line([start, end], fill=color, width=line_width)
    if start != end:
        _draw_arrow_head(draw, start, end, color, line_width, item, scale)
        if item.double_arrow:
            _draw_arrow_head(draw, end, start, color, line_width, item, scale)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path('C:/Windows/Fonts/arialbd.ttf') if bold else Path('C:/Windows/Fonts/arial.ttf'),
        Path('C:/Windows/Fonts/segoeuib.ttf') if bold else Path('C:/Windows/Fonts/segoeui.ttf'),
    ]
    for candidate in candidates:
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                pass
    return ImageFont.load_default()


def _draw_text_label(
    draw: ImageDraw.ImageDraw, item: Annotation, position: tuple[int, int],
    color: tuple[int, int, int, int], line_width: int, scale: int,
) -> None:
    label = item.text if item.kind != 'marker' else str(item.number)
    if not label and item.kind != 'marker':
        return
    font = _font(item.font_size * scale, bold=item.bold)
    x, y = position
    bbox = draw.textbbox((x, y), label, font=font, stroke_width=max(0, line_width // 4))
    padding = max(4 * scale, item.font_size * scale // 5)
    if item.text_background_enabled:
        background = _rgba(item.text_background, min(1.0, item.opacity * 0.88))
        draw.rounded_rectangle(
            [bbox[0] - padding, bbox[1] - padding, bbox[2] + padding, bbox[3] + padding],
            radius=padding,
            fill=background,
        )
    draw.text((x, y), label, font=font, fill=color)


def _arrow_text_position(
    item: Annotation, points: list[tuple[int, int]], scale: int) -> tuple[int, int]:
    start, end = points[0], points[1]
    mid_x = (start[0] + end[0]) / 2
    mid_y = (start[1] + end[1]) / 2
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = max(1.0, math.hypot(dx, dy))
    offset = max(12 * scale, item.font_size * scale * 0.55)
    return round(mid_x - dy / length * offset), round(mid_y + dx / length * offset)


def _annotation_scale(width: int, height: int) -> int:
    largest = max(width, height)
    if largest <= 1800:
        return 3
    if largest <= 3600:
        return 2
    return 1


def render_annotations(
    source: Path,
    destination: Path,
    annotations: list[Annotation],
) -> Path:
    source = safe_path(source)
    destination = safe_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        base = opened.convert('RGBA')
    width, height = base.size
    scale = _annotation_scale(width, height)
    overlay_size = (width * scale, height * scale)
    overlay = Image.new('RGBA', overlay_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for item in annotations:
        points = _scaled_points(item, width, height, scale)
        color = _rgba(item.color, item.opacity)
        border = _rgba(item.border_color or item.color, item.opacity)
        fill = _rgba(item.fill_color, item.fill_opacity if item.fill_enabled else 0.0)
        line_width = _line_width(item, width, height, scale)
        if item.kind == 'highlight' and len(points) >= 2:
            box = _normalized_box(points[0], points[1])
            highlight_fill = _rgba(item.fill_color or item.color, item.fill_opacity or min(0.45, item.opacity))
            draw.rounded_rectangle(
                box,
                radius=max(2 * scale, line_width),
                fill=highlight_fill,
                outline=border,
                width=line_width,
            )
        elif item.kind == 'rectangle' and len(points) >= 2:
            draw.rectangle(
                _normalized_box(points[0], points[1]),
                outline=border,
                fill=fill if item.fill_enabled else None,
                width=line_width,
            )
        elif item.kind == 'circle' and len(points) >= 2:
            draw.ellipse(
                _normalized_box(points[0], points[1]),
                outline=border,
                fill=fill if item.fill_enabled else None,
                width=line_width,
            )
        elif item.kind == 'line' and len(points) >= 2:
            draw.line([points[0], points[1]], fill=color, width=line_width)
        elif item.kind in {'arrow', 'arrow_text'} and len(points) >= 2:
            _draw_arrow(draw, item, points, color, line_width, scale)
            if item.kind == 'arrow_text':
                _draw_text_label(
                    draw, item, _arrow_text_position(item, points, scale),
                    color, line_width, scale,
                )
        elif item.kind == 'text':
            _draw_text_label(draw, item, points[0], color, line_width, scale)
        elif item.kind == 'marker':
            x, y = points[0]
            radius = max(14 * scale, item.font_size * scale // 2)
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)
            font = _font(max(12 * scale, round(radius * 1.15)), bold=True)
            label = str(item.number)
            bbox = draw.textbbox((0, 0), label, font=font)
            draw.text(
                (x - (bbox[2] - bbox[0]) / 2, y - (bbox[3] - bbox[1]) / 2 - bbox[1]),
                label,
                fill=(255, 255, 255, 255),
                font=font,
            )

    if scale > 1:
        overlay = overlay.resize(base.size, Image.Resampling.LANCZOS)
    result = Image.alpha_composite(base, overlay).convert('RGB')
    result.save(destination, format='PNG', optimize=True)
    return destination


def _area_box(
    area: AnonymizationArea, width: int, height: int, margin: int = 0,
) -> tuple[int, int, int, int]:
    points = [(round(x * width), round(y * height)) for x, y in area.points]
    x0, y0, x1, y1 = _normalized_box(points[0], points[1])
    return (
        max(0, x0 - margin), max(0, y0 - margin),
        min(width, x1 + margin), min(height, y1 + margin),
    )


def build_anonymization_instructions(areas: list[AnonymizationArea]) -> str:
    lines = [
        'Edite somente as regioes brancas da mascara enviada.',
        'Preserve integralmente tudo que estiver fora da mascara.',
        'Nao altere layout, cores, textos ou elementos fora das areas autorizadas.',
        'Substitua o conteudo sensivel de cada area conforme a lista:',
    ]
    for index, area in enumerate(areas, 1):
        replacement = area.replacement_text or f'[{area.data_type}]'
        note = f' Observacao: {area.note}.' if area.note else ''
        lines.append(
            f'{index}. Tipo: {area.data_type}. Texto de substituicao: {replacement}.{note}'
        )
    return '\n'.join(lines)


def build_anonymization_mask(
    source: Path, destination: Path, areas: list[AnonymizationArea], margin: int = 2,
) -> Path:
    source = safe_path(source)
    destination = safe_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        width, height = opened.size
    mask = Image.new('L', (width, height), 0)
    draw = ImageDraw.Draw(mask)
    for area in areas:
        draw.rectangle(_area_box(area, width, height, margin), fill=255)
    mask.save(destination, 'PNG')
    return destination


def validate_changes_within_mask(
    before: Path, after: Path, mask: Path, *, margin: int = 3, threshold: int = 10,
) -> tuple[bool, int]:
    before = safe_path(before)
    after = safe_path(after)
    mask = safe_path(mask)
    with Image.open(before) as before_image, Image.open(after) as after_image:
        base = before_image.convert('RGB')
        result = after_image.convert('RGB')
        if result.size != base.size:
            return False, base.size[0] * base.size[1]
        diff = ImageChops.difference(base, result).convert('L')
    with Image.open(mask) as mask_image:
        allowed = mask_image.convert('L')
        if allowed.size != diff.size:
            allowed = allowed.resize(diff.size, Image.Resampling.NEAREST)
    if margin > 0:
        size = margin * 2 + 1
        allowed = allowed.filter(ImageFilter.MaxFilter(size))
    outside = Image.new('L', diff.size, 255)
    outside.paste(0, mask=allowed)
    relevant = ImageChops.multiply(diff.point(lambda value: 255 if value > threshold else 0), outside)
    changed_pixels = sum(relevant.histogram()[1:])
    tolerance = max(12, round(diff.size[0] * diff.size[1] * 0.00005))
    return changed_pixels <= tolerance, changed_pixels


def render_anonymization_preview(
    source: Path,
    destination: Path,
    annotations: list[Annotation],
    areas: list[AnonymizationArea],
    draft_area: AnonymizationArea | None = None,
) -> Path:
    render_annotations(source, destination, annotations)
    with Image.open(destination) as opened:
        base = opened.convert('RGBA')
    width, height = base.size
    scale = _annotation_scale(width, height)
    overlay = Image.new('RGBA', (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    visible_areas = [*areas, *([draft_area] if draft_area else [])]
    font = _font(14 * scale, bold=True)
    dash = 8 * scale
    gap = 5 * scale
    for index, area in enumerate(visible_areas, 1):
        x0, y0, x1, y1 = _area_box(area, width * scale, height * scale)
        color = (14, 165, 233, 255)
        fill = (14, 165, 233, 35)
        draw.rectangle((x0, y0, x1, y1), fill=fill)
        for x in range(x0, x1, dash + gap):
            draw.line([(x, y0), (min(x + dash, x1), y0)], fill=color, width=2 * scale)
            draw.line([(x, y1), (min(x + dash, x1), y1)], fill=color, width=2 * scale)
        for y in range(y0, y1, dash + gap):
            draw.line([(x0, y), (x0, min(y + dash, y1))], fill=color, width=2 * scale)
            draw.line([(x1, y), (x1, min(y + dash, y1))], fill=color, width=2 * scale)
        label = f'Anon {index}: {area.data_type}'
        bbox = draw.textbbox((x0, max(0, y0 - 22 * scale)), label, font=font)
        draw.rounded_rectangle(
            [bbox[0] - 4 * scale, bbox[1] - 2 * scale, bbox[2] + 4 * scale, bbox[3] + 2 * scale],
            radius=4 * scale,
            fill=(14, 165, 233, 230),
        )
        draw.text((bbox[0], bbox[1]), label, font=font, fill=(255, 255, 255, 255))
    if scale > 1:
        overlay = overlay.resize(base.size, Image.Resampling.LANCZOS)
    Image.alpha_composite(base, overlay).convert('RGB').save(destination, 'PNG', optimize=True)
    return destination
def build_exports(
    workspace: SessionWorkspace,
    original_path: Path,
    treated_path: Path,
    final_path: Path,
    annotations: list[Annotation],
    orientation: str,
    prompt: str,
) -> dict[str, Path]:
    metadata = {
        'application': 'EMC Novidade Visual IA',
        'session_id': workspace.session_id,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'original_file': original_path.name,
        'treated_file': treated_path.name,
        'final_file': final_path.name,
        'orientation': orientation.strip(),
        'annotations': [item.to_dict() for item in annotations],
    }
    json_path = safe_path(workspace.export_dir / 'marcacoes.json')
    prompt_path = safe_path(workspace.export_dir / 'prompt_utilizado.txt')
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    prompt_path.write_text(prompt, encoding='utf-8')

    treated_export = safe_path(workspace.export_dir / 'imagem_tratada.png')
    final_export = safe_path(workspace.export_dir / 'imagem_final.png')
    with Image.open(treated_path) as image:
        image.convert('RGB').save(treated_export, 'PNG')
    with Image.open(final_path) as image:
        image.convert('RGB').save(final_export, 'PNG')

    zip_path = safe_path(workspace.export_dir / 'emc_novidade_visual_ia.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in (original_path, treated_export, final_export, json_path, prompt_path):
            archive.write(safe_path(path), arcname=path.name)
    return {
        'treated': treated_export,
        'final': final_export,
        'json': json_path,
        'prompt': prompt_path,
        'zip': zip_path,
    }


def build_multi_exports(
    workspace: SessionWorkspace,
    images: list[dict[str, Any]],
    text_novelty: str,
    prompt: str,
) -> dict[str, Path]:
    if not images:
        raise ValueError('A novidade precisa ter ao menos uma imagem para exportaÃ§Ã£o.')
    novelty_dir = safe_path(workspace.export_dir / 'novidade')
    images_dir = safe_path(novelty_dir / 'imagens')
    annotations_dir = safe_path(novelty_dir / 'anotacoes')
    images_dir.mkdir(parents=True, exist_ok=True)
    annotations_dir.mkdir(parents=True, exist_ok=True)
    digits = max(2, len(str(len(images))))
    metadata_images: list[dict[str, Any]] = []
    archive_paths: list[tuple[Path, str]] = []
    first_treated: Path | None = None
    first_final: Path | None = None
    for order, item in enumerate(images, 1):
        prefix = str(order).zfill(digits)
        original_source = safe_path(Path(item['original']))
        treated_source = safe_path(Path(item['treated']))
        final_source = safe_path(Path(item.get('final') or treated_source))
        exports = {
            'original': safe_path(images_dir / f'{prefix}_original.png'),
            'tratada': safe_path(images_dir / f'{prefix}_tratada.png'),
            'final': safe_path(images_dir / f'{prefix}_final.png'),
        }
        for source, target in zip(
            (original_source, treated_source, final_source), exports.values(), strict=True
        ):
            with Image.open(source) as opened:
                opened.convert('RGB').save(target, 'PNG')
        annotations = list(item.get('annotations') or [])
        annotation_path = safe_path(annotations_dir / f'{prefix}_anotacoes.json')
        annotation_path.write_text(
            json.dumps(
                [value.to_dict() if isinstance(value, Annotation) else value for value in annotations],
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )
        metadata_images.append({
            'ordem': order,
            'nome_original': str(item.get('filename') or original_source.name),
            'arquivo_original': 'imagens/' + exports['original'].name,
            'arquivo_tratado': 'imagens/' + exports['tratada'].name,
            'arquivo_final': 'imagens/' + exports['final'].name,
            'status': str(item.get('status') or ''),
            'mensagem_erro': str(item.get('error') or ''),
            'anotacoes': f'anotacoes/{annotation_path.name}',
        })
        for path in exports.values():
            archive_paths.append((path, f'novidade/imagens/{path.name}'))
        archive_paths.append((annotation_path, f'novidade/anotacoes/{annotation_path.name}'))
        first_treated = first_treated or exports['tratada']
        first_final = first_final or exports['final']

    metadata = {
        'application': 'EMC Novidade Visual IA',
        'session_id': workspace.session_id,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'titulo': '',
        'texto_novidade': text_novelty.strip(),
        'imagens': metadata_images,
    }
    json_path = safe_path(novelty_dir / 'novidade.json')
    prompt_path = safe_path(novelty_dir / 'prompt_utilizado.txt')
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    prompt_path.write_text(prompt, encoding='utf-8')
    archive_paths.extend((
        (json_path, 'novidade/novidade.json'),
        (prompt_path, 'novidade/prompt_utilizado.txt'),
    ))
    if len(images) == 1:
        item = images[0]
        legacy = build_exports(
            workspace,
            Path(item['original']),
            Path(item['treated']),
            Path(item.get('final') or item['treated']),
            list(item.get('annotations') or []),
            text_novelty,
            prompt,
        )
        for key in ('treated', 'final', 'json', 'prompt'):
            archive_paths.append((legacy[key], legacy[key].name))
    zip_path = safe_path(workspace.export_dir / 'emc_novidade_visual_ia.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, arcname in archive_paths:
            archive.write(safe_path(path), arcname=arcname)
    return {
        'treated': first_treated,
        'final': first_final,
        'json': json_path,
        'prompt': prompt_path,
        'zip': zip_path,
    }


def build_composition_exports(
    workspace: SessionWorkspace,
    images: list[dict[str, Any]],
    composition: dict[str, Any],
    prompt: str,
    html_override: str = '',
) -> dict[str, Path | None]:
    """Gera os artefatos finais.

    Quando ``html_override`` vem preenchido, o HTML editado manualmente na tela
    substitui o gerado a partir da composição. Os demais artefatos continuam
    saindo do CompositionDocument.
    """
    novelty_dir = safe_path(workspace.export_dir / 'novidade')
    images_dir = safe_path(novelty_dir / 'imagens')
    annotations_dir = safe_path(novelty_dir / 'anotacoes')
    images_dir.mkdir(parents=True, exist_ok=True)
    annotations_dir.mkdir(parents=True, exist_ok=True)
    digits = max(2, len(str(max(1, len(images)))))
    image_exports: dict[str, dict[str, Any]] = {}
    metadata_images: list[dict[str, Any]] = []
    archive_paths: list[tuple[Path, str]] = []

    for order, item in enumerate(images, 1):
        prefix = str(order).zfill(digits)
        image_id = str(item['id'])
        original_source = safe_path(Path(item['original']))
        treated_source = safe_path(Path(item['treated']))
        final_source = safe_path(Path(item.get('final') or treated_source))
        exported = {
            'original': safe_path(images_dir / f'{prefix}_original.png'),
            'tratada': safe_path(images_dir / f'{prefix}_tratada.png'),
            'final': safe_path(images_dir / f'{prefix}_final.png'),
        }
        for source, target in zip(
            (original_source, treated_source, final_source), exported.values(), strict=True
        ):
            with Image.open(source) as opened:
                opened.convert('RGB').save(target, 'PNG')
        with Image.open(exported['final']) as final_image:
            final_width, final_height = final_image.size
        annotations = list(item.get('annotations') or [])
        annotation_path = safe_path(annotations_dir / f'{prefix}_anotacoes.json')
        annotation_path.write_text(
            json.dumps(
                [value.to_dict() if isinstance(value, Annotation) else value for value in annotations],
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )
        image_exports[image_id] = {
            'id': image_id,
            'ordem_arquivo': order,
            'arquivo_original': f'imagens/{exported["original"].name}',
            'arquivo_tratado': f'imagens/{exported["tratada"].name}',
            'arquivo_final': f'imagens/{exported["final"].name}',
            'anotacoes': f'anotacoes/{annotation_path.name}',
            'treated_path': exported['tratada'],
            'final_path': exported['final'],
        }
        metadata_images.append({
            key: value for key, value in {
                **image_exports[image_id],
                'nome_original': str(item.get('filename') or original_source.name),
                'largura': final_width,
                'altura': final_height,
                'status': str(item.get('status') or ''),
                'mensagem_erro': str(item.get('error') or ''),
            }.items() if not key.endswith('_path')
        })
        for path in exported.values():
            archive_paths.append((path, f'imagens/{path.name}'))
        archive_paths.append((annotation_path, f'anotacoes/{annotation_path.name}'))

    exported_blocks: list[dict[str, Any]] = []
    for order, raw_block in enumerate(composition.get('blocos') or [], 1):
        block = {
            key: value for key, value in dict(raw_block).items()
            if key != 'ordem'
        }
        block['ordem'] = order
        if block.get('tipo') == 'imagem':
            image_id = str(block.get('imagem_id') or '')
            if image_id not in image_exports:
                raise ValueError(f'Imagem do bloco nÃ£o encontrada na sessÃ£o: {image_id}')
            block['arquivo'] = image_exports[image_id]['arquivo_final']
            secondary_id = str(block.get('imagem_secundaria_id') or '')
            if secondary_id:
                if secondary_id not in image_exports:
                    raise ValueError(
                        f'Imagem secundÃ¡ria do bloco nÃ£o encontrada na sessÃ£o: {secondary_id}'
                    )
                block['arquivos'] = [
                    image_exports[image_id]['arquivo_final'],
                    image_exports[secondary_id]['arquivo_final'],
                ]
        exported_blocks.append(block)

    metadata_by_id = {str(item['id']): item for item in metadata_images}
    for block in exported_blocks:
        if block.get('tipo') != 'imagem':
            continue
        related_ids = [
            value for value in (
                block.get('imagem_id'), block.get('imagem_secundaria_id')
            ) if value
        ]
        for image_id in related_ids:
            image_metadata = metadata_by_id.get(str(image_id))
            if image_metadata is None:
                continue
            image_metadata.update(
                bloco_id=block.get('id'),
                posicao_composicao=block.get('ordem'),
                legenda=block.get('legenda') or '',
                modo_exibicao=block.get('modo_exibicao') or 'normal',
            )

    cover_metadata = dict(composition.get('capa') or {})
    cover_export: Path | None = None
    if cover_metadata.get('status') == 'gerada' and cover_metadata.get('arquivo'):
        cover_source = safe_path(Path(str(cover_metadata['arquivo'])))
        cover_dir = safe_path(novelty_dir / 'capa')
        cover_dir.mkdir(parents=True, exist_ok=True)
        cover_export = safe_path(cover_dir / 'capa.png')
        if cover_source != cover_export:
            shutil.copy2(cover_source, cover_export)
        cover_metadata['arquivo'] = 'capa/capa.png'
        with Image.open(cover_export) as cover_image:
            cover_metadata['largura'], cover_metadata['altura'] = cover_image.size
        archive_paths.append((cover_export, 'capa/capa.png'))

    introduction = next((
        str(block.get('conteudo') or '') for block in exported_blocks
        if block.get('tipo') == 'texto' and block.get('papel') == 'introducao'
    ), '')
    if not introduction:
        introduction = next((
            str(block.get('conteudo') or '') for block in exported_blocks
            if block.get('tipo') == 'texto'
        ), '')

    metadata = {
        'application': 'EMC Novidade Visual IA',
        'session_id': workspace.session_id,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'origem': str(composition.get('origem') or 'manual'),
        'titulo': str(composition.get('titulo') or ''),
        'introducao': introduction,
        'texto_novidade': str(composition.get('texto_novidade') or ''),
        'blocos': exported_blocks,
        'imagens': metadata_images,
        'imagens_fallback': list(composition.get('imagens_fallback') or []),
        'capa': cover_metadata,
        'revisao': dict(composition.get('revisao') or {}),
    }
    json_path = safe_path(novelty_dir / 'novidade.json')
    prompt_path = safe_path(novelty_dir / 'prompt_utilizado.txt')
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    prompt_path.write_text(prompt, encoding='utf-8')
    archive_paths.extend((
        (json_path, 'novidade.json'),
        (prompt_path, 'prompt_utilizado.txt'),
    ))
    html_path = safe_path(novelty_dir / 'novidade.html')
    html_path.write_text(
        html_override.strip() or build_novelty_html(metadata), encoding='utf-8'
    )
    archive_paths.append((html_path, 'novidade.html'))

    xml_path = safe_path(novelty_dir / 'novidade.xml')
    build_novelty_xml(metadata, xml_path)
    archive_paths.append((xml_path, 'novidade.xml'))

    legacy: dict[str, Path] | None = None
    if len(images) == 1:
        item = images[0]
        legacy = build_exports(
            workspace,
            Path(item['original']),
            Path(item['treated']),
            Path(item.get('final') or item['treated']),
            list(item.get('annotations') or []),
            str(composition.get('texto_novidade') or ''),
            prompt,
        )
        for key in ('treated', 'final', 'json'):
            archive_paths.append((legacy[key], legacy[key].name))

    manifest_path = safe_path(novelty_dir / 'manifest.xml')
    build_manifest_xml(archive_paths, manifest_path)
    archive_paths.append((manifest_path, 'manifest.xml'))

    zip_path = safe_path(workspace.export_dir / 'emc_novidade_visual_ia.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, arcname in archive_paths:
            archive.write(safe_path(path), arcname=arcname)
    first = next(iter(image_exports.values()), None)
    return {
        'treated': legacy['treated'] if legacy else (first['treated_path'] if first else None),
        'final': legacy['final'] if legacy else (first['final_path'] if first else None),
        'json': json_path,
        'xml': xml_path,
        'html': html_path,
        'manifest': manifest_path,
        'prompt': prompt_path,
        'zip': zip_path,
        'cover': cover_export,
    }


async def process_image_batch(
    images: list[dict[str, Any]],
    process_one: Callable[[dict[str, Any]], Awaitable[bool]],
    *,
    is_cancelled: Callable[[], bool],
    on_start: Callable[[dict[str, Any], int, int], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, int | bool]:
    completed_statuses = {'Tratada com IA', 'Exportada'}
    pending = [item for item in images if item['status'] not in completed_statuses]
    successes = 0
    failures = 0
    processed = 0
    cancelled = False
    total = len(pending)
    for position, item in enumerate(pending, 1):
        if is_cancelled():
            cancelled = True
            break
        if on_start:
            on_start(item, position, total)
        if await process_one(item):
            successes += 1
        else:
            failures += 1
        processed = position
        if on_progress:
            on_progress(position, total)
    return {
        'total': total,
        'processed': processed,
        'successes': successes,
        'failures': failures,
        'cancelled': cancelled,
    }
