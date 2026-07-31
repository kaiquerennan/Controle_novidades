from __future__ import annotations

from hashlib import sha256
import mimetypes
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET


SCHEMA_VERSION = '1.0'


def _write(root: ET.Element, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space='  ')
    tree.write(destination, encoding='utf-8', xml_declaration=True)
    return destination


def build_novelty_xml(metadata: dict[str, Any], destination: Path) -> Path:
    root = ET.Element('novidade', {
        'schemaVersion': SCHEMA_VERSION,
        'origem': str(metadata.get('origem') or 'manual'),
    })
    content = ET.SubElement(root, 'conteudo')
    images = {
        str(item.get('id')): item for item in metadata.get('imagens') or []
    }
    for raw in metadata.get('blocos') or []:
        block_type = str(raw.get('tipo') or '')
        attrs = {
            'id': str(raw.get('id') or ''),
            'ordem': str(raw.get('ordem') or ''),
            'tipo': block_type,
        }
        if raw.get('papel'):
            attrs['papel'] = str(raw['papel'])
        if raw.get('modo_exibicao'):
            attrs['modoExibicao'] = str(raw['modo_exibicao'])
        block = ET.SubElement(content, 'bloco', attrs)
        if block_type in {'titulo', 'subtitulo', 'texto'}:
            text_element = ET.SubElement(block, 'texto')
            text_element.text = str(raw.get('conteudo') or '')
            for highlight in raw.get('destaques') or []:
                ET.SubElement(block, 'destaque').text = str(highlight)
        elif block_type == 'lista':
            list_element = ET.SubElement(block, 'lista')
            for raw_item in raw.get('itens') or []:
                item = ET.SubElement(list_element, 'item')
                item.text = str(raw_item.get('texto') or '')
                for highlight in raw_item.get('destaques') or []:
                    ET.SubElement(item, 'destaque').text = str(highlight)
        elif block_type == 'imagem':
            for image_id in (
                raw.get('imagem_id'), raw.get('imagem_secundaria_id')
            ):
                if not image_id:
                    continue
                image = images.get(str(image_id), {})
                image_element = ET.SubElement(block, 'imagem', {
                    'ref': str(image_id),
                    'arquivo': str(image.get('arquivo_final') or ''),
                    'original': str(image.get('arquivo_original') or ''),
                })
                if image.get('anotacoes'):
                    ET.SubElement(image_element, 'anotacoes', {
                        'arquivo': str(image['anotacoes']),
                    })
            if raw.get('legenda'):
                ET.SubElement(block, 'legenda').text = str(raw['legenda'])

    fallback = list(metadata.get('imagens_fallback') or [])
    if fallback:
        fallback_element = ET.SubElement(root, 'imagensFallback')
        for image_id in fallback:
            ET.SubElement(fallback_element, 'imagemRef', {'ref': str(image_id)})

    cover_data = dict(metadata.get('capa') or {})
    cover_attrs = {
        'solicitada': str(bool(cover_data.get('solicitada'))).lower(),
        'status': str(cover_data.get('status') or 'nao_gerada'),
    }
    if cover_data.get('arquivo'):
        cover_attrs['arquivo'] = str(cover_data['arquivo'])
    if cover_data.get('tema'):
        cover_attrs['tema'] = str(cover_data['tema'])
    ET.SubElement(root, 'capa', cover_attrs)

    review_data = dict(metadata.get('revisao') or {})
    review = ET.SubElement(root, 'revisao', {
        'aprovada': str(bool(review_data.get('aprovada'))).lower(),
        'status': str(review_data.get('status') or 'pendente'),
    })
    for field, item_name in (
        ('avisos', 'aviso'),
        ('correcoes_necessarias', 'correcao'),
        ('imagens_nao_utilizadas', 'imagemRef'),
        ('informacoes_possivelmente_inventadas', 'informacao'),
        ('nomes_tecnicos_alterados', 'nome'),
    ):
        values = list(review_data.get(field) or [])
        if not values:
            continue
        group = ET.SubElement(review, field)
        for value in values:
            ET.SubElement(group, item_name).text = str(value)
    return _write(root, destination)


def _role(arcname: str) -> str:
    if arcname == 'novidade.xml':
        return 'documento_principal'
    if arcname == 'novidade.html':
        return 'documento_html_freetextbox'
    if arcname == 'novidade.json':
        return 'metadados_json'
    if arcname.startswith('imagens/'):
        return 'imagem'
    if arcname.startswith('anotacoes/'):
        return 'anotacoes'
    if arcname.startswith('capa/'):
        return 'capa'
    if arcname.endswith('.txt'):
        return 'diagnostico'
    return 'compatibilidade'


def build_manifest_xml(
    entries: Iterable[tuple[Path, str]], destination: Path,
) -> Path:
    root = ET.Element('manifest', {
        'schemaVersion': SCHEMA_VERSION,
        'application': 'EMC Novidade Visual IA',
    })
    files = ET.SubElement(root, 'arquivos')
    unique: dict[str, Path] = {}
    for source, arcname in entries:
        unique[arcname.replace('\\', '/')] = Path(source)
    for arcname in sorted(unique):
        source = unique[arcname]
        content = source.read_bytes()
        mime_type = mimetypes.guess_type(arcname)[0] or 'application/octet-stream'
        ET.SubElement(files, 'arquivo', {
            'caminho': arcname,
            'papel': _role(arcname),
            'mimeType': mime_type,
            'tamanho': str(len(content)),
            'sha256': sha256(content).hexdigest(),
        })
    return _write(root, destination)