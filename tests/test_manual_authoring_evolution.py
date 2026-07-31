from __future__ import annotations

from hashlib import sha256
import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from PIL import Image

from models.annotation import Annotation, AnnotationDocument
from models.composition import CompositionDocument, ContentBlock
from services.image_service import DATA_ROOT, SessionWorkspace, build_composition_exports, render_annotations
from services.system_path_formatter import (
    classify_path, format_system_navigation, missing_system_paths,
)


class AnnotationAndOrganizationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='manual_authoring_', dir=DATA_ROOT))

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_annotations_use_normalized_coordinates_and_preserve_original(self) -> None:
        source = self.root / 'original.png'
        destination = self.root / 'final.png'
        Image.new('RGB', (200, 100), (255, 255, 255)).save(source)
        original_hash = sha256(source.read_bytes()).hexdigest()
        annotation = Annotation(kind='arrow', points=[(-1, 0.25), (2, 0.75)])
        self.assertEqual(annotation.points, [(0.0, 0.25), (1.0, 0.75)])
        render_annotations(source, destination, [annotation])
        self.assertEqual(original_hash, sha256(source.read_bytes()).hexdigest())
        self.assertTrue(destination.is_file())
        self.assertNotEqual(source, destination)

    def test_annotation_undo_and_redo(self) -> None:
        document = AnnotationDocument()
        document.add(Annotation(kind='circle', points=[(0.1, 0.1), (0.5, 0.5)]))
        self.assertTrue(document.undo())
        self.assertEqual(document.items, [])
        self.assertTrue(document.redo())
        self.assertEqual(len(document.items), 1)

    def test_move_only_image_block_without_loss_and_with_redo(self) -> None:
        document = CompositionDocument(blocks=[
            ContentBlock(tipo='titulo', conteudo='Título'),
            ContentBlock(tipo='texto', conteudo='Introdução', papel='introducao'),
            ContentBlock(tipo='imagem', imagem_id='imagem_001'),
            ContentBlock(tipo='subtitulo', conteudo='Seção'),
            ContentBlock(tipo='imagem', imagem_id='imagem_002'),
        ])
        second = next(block for block in document.blocks if block.imagem_id == 'imagem_002')
        self.assertTrue(document.move_image_block(second.id, 1))
        self.assertEqual(document.used_image_ids(), {'imagem_001', 'imagem_002'})
        self.assertEqual(document.blocks[1].imagem_id, 'imagem_002')
        self.assertTrue(document.undo())
        self.assertEqual(document.blocks[-1].imagem_id, 'imagem_002')
        self.assertTrue(document.redo())
        self.assertEqual(document.blocks[1].imagem_id, 'imagem_002')

    def test_non_image_block_is_rejected_by_image_only_move(self) -> None:
        document = CompositionDocument()
        self.assertFalse(document.move_image_block(document.blocks[0].id, 1))

    def test_new_change_after_undo_clears_redo(self) -> None:
        document = CompositionDocument()
        document.update(document.blocks[0].id, conteudo='Primeiro')
        self.assertTrue(document.undo())
        document.update(document.blocks[0].id, conteudo='Segundo')
        self.assertFalse(document.redo())


class SystemPathFormatterTestCase(unittest.TestCase):
    def test_formats_only_explicit_system_navigation(self) -> None:
        self.assertEqual(
            format_system_navigation('Acesse: Fiscal > Configurações > NFC-e'),
            'Acesse através do caminho:\nFiscal / Configurações / NFC-e',
        )

    def test_preserves_urls_and_windows_directories(self) -> None:
        url = 'Acesse: https://portal.emc.example/configuracao'
        windows = r'Acesse: C:\EMC\config\arquivo.ini'
        self.assertEqual(format_system_navigation(url), url)
        self.assertEqual(format_system_navigation(windows), windows)
        self.assertEqual(classify_path('https://example.com/a/b'), 'url')
        self.assertEqual(classify_path(r'\\servidor\pasta'), 'windows')
    def test_inline_navigation_does_not_turn_the_whole_sentence_into_a_path(self) -> None:
        source = (
            'Nova opção permite exportar o relatório em PDF pela rotina '
            'Relatórios > Vendas.'
        )
        self.assertEqual(missing_system_paths(source, 'Texto final sem caminho explícito.'), [])

    def test_explicit_navigation_is_still_required_in_canonical_format(self) -> None:
        source = 'Acesse: Fiscal > Configurações > NFC-e'
        final = 'Acesse através do caminho:\nFiscal / Configurações / NFC-e'
        self.assertEqual(missing_system_paths(source, final), [])
        self.assertEqual(missing_system_paths(source, 'Caminho ausente.'), [
            'Fiscal / Configurações / NFC-e'
        ])


class XmlExportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='xml_export_', dir=DATA_ROOT))
        self.workspace = SessionWorkspace(
            session_id='xml-session',
            original_dir=self.root / 'originals',
            processed_dir=self.root / 'processed',
            export_dir=self.root / 'exports',
            temp_dir=self.root / 'temp',
        )
        for folder in (
            self.workspace.original_dir, self.workspace.processed_dir,
            self.workspace.export_dir, self.workspace.temp_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_zip_contains_xml_manifest_and_relative_image_references(self) -> None:
        images = []
        blocks = [
            ContentBlock(tipo='titulo', conteudo='Novidade'),
            ContentBlock(tipo='texto', conteudo='Introdução', papel='introducao'),
        ]
        for index in (1, 2):
            path = self.workspace.original_dir / f'tela_{index}.png'
            Image.new('RGB', (80, 60), (index * 50, 80, 120)).save(path)
            image_id = f'imagem_{index:03d}'
            images.append({
                'id': image_id, 'filename': path.name, 'original': path,
                'treated': path, 'final': path, 'annotations': [],
                'status': 'Preservada', 'error': '',
            })
            blocks.append(ContentBlock(tipo='imagem', imagem_id=image_id))
        document = CompositionDocument(blocks=blocks)
        payload = document.to_dict()
        payload['texto_novidade'] = document.text
        result = build_composition_exports(self.workspace, images, payload, 'prompt')
        self.assertTrue(Path(result['xml']).is_file())
        self.assertTrue(Path(result['manifest']).is_file())

        xml_root = ET.parse(result['xml']).getroot()
        references = [item.attrib['arquivo'] for item in xml_root.findall('.//imagem')]
        self.assertEqual(references, ['imagens/01_final.png', 'imagens/02_final.png'])
        self.assertTrue(all(':' not in value and not value.startswith('/') for value in references))

        manifest_root = ET.parse(result['manifest']).getroot()
        manifest_files = {
            item.attrib['caminho']: item for item in manifest_root.findall('.//arquivo')
        }
        self.assertIn('novidade.xml', manifest_files)
        self.assertEqual(
            manifest_files['novidade.xml'].attrib['sha256'],
            sha256(Path(result['xml']).read_bytes()).hexdigest(),
        )
        with zipfile.ZipFile(result['zip']) as archive:
            names = set(archive.namelist())
            self.assertIn('novidade.xml', names)
            self.assertIn('manifest.xml', names)
            self.assertTrue(set(references).issubset(names))
            exported = ET.fromstring(archive.read('novidade.xml'))
            self.assertEqual(exported.attrib['schemaVersion'], '1.0')


class VisibleControlsContractTestCase(unittest.TestCase):
    def test_manual_controls_are_visible_and_no_api_is_required(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app_source = (root / 'app.py').read_text(encoding='utf-8')
        adjuster_source = (root / 'components' / 'advanced_image_adjuster.py').read_text(
            encoding='utf-8'
        )
        preview_source = (root / 'components' / 'novelty_preview.py').read_text(
            encoding='utf-8'
        )
        self.assertIn('Adicionar marcações', app_source)
        self.assertIn("icon='redo', on_click=redo", adjuster_source)
        self.assertIn('move_image_block', preview_source)
        self.assertNotIn('generate_novelty_plan', preview_source)


if __name__ == '__main__':
    unittest.main()