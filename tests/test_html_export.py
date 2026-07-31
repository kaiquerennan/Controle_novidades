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

from models.annotation import Annotation
from models.composition import CompositionDocument, ContentBlock
from services.html_export_service import build_novelty_html, safe_image_src
from services.image_service import DATA_ROOT, SessionWorkspace, build_composition_exports, render_annotations


class FreeTextBoxHtmlExportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='html_export_', dir=DATA_ROOT))
        self.workspace = SessionWorkspace(
            session_id='html-session', original_dir=self.root / 'original',
            processed_dir=self.root / 'processed', export_dir=self.root / 'export',
            temp_dir=self.root / 'temp',
        )
        for folder in (
            self.workspace.original_dir, self.workspace.processed_dir,
            self.workspace.export_dir, self.workspace.temp_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def export(self):
        original = self.workspace.original_dir / 'tela.png'
        Image.new('RGB', (134, 76), 'white').save(original)
        final = self.workspace.processed_dir / 'tela_final.png'
        render_annotations(
            original, final,
            [Annotation(kind='highlight', points=[(0.1, 0.1), (0.8, 0.5)], opacity=0.35)],
        )
        document = CompositionDocument(blocks=[
            ContentBlock(tipo='titulo', conteudo='Título & segurança'),
            ContentBlock(
                tipo='texto', papel='introducao',
                conteudo='Introdução com <script>alert(1)</script>.',
            ),
            ContentBlock(tipo='subtitulo', conteudo='Opções fiscais'),
            ContentBlock(
                tipo='texto', papel='caminho',
                conteudo='Acesse através do caminho:\nFiscal / Cadastros / Tabelas',
            ),
            ContentBlock(tipo='lista', itens=[
                {'texto': 'Opção automática', 'destaques': ['Opção automática']},
                {'texto': 'Opção manual', 'destaques': []},
            ]),
            ContentBlock(tipo='imagem', imagem_id='imagem_001', legenda='Tela final'),
        ])
        payload = document.to_dict()
        payload['texto_novidade'] = document.text
        return build_composition_exports(
            self.workspace,
            [{
                'id': 'imagem_001', 'filename': 'tela.png', 'original': original,
                'treated': original, 'final': final, 'annotations': [],
                'status': 'Pronta', 'error': '',
            }],
            payload, 'prompt de teste',
        )

    def test_html_uses_freetextbox_markup_semantics_and_exact_order(self) -> None:
        result = self.export()
        html = Path(result['html']).read_text(encoding='utf-8')
        self.assertIn('face="Verdana"', html)
        self.assertIn('size="2"', html)
        self.assertIn('<ul data-spread="false">', html)
        self.assertIn('<li style="text-align: justify;">', html)
        self.assertIn('<strong>Acesse através do caminho:</strong><br>', html)
        self.assertIn('<strong>Fiscal / Cadastros / Tabelas</strong>', html)
        self.assertIn('<strong>Opção automática</strong>', html)
        self.assertIn('style="text-align: center;"', html)
        self.assertIn('width="134" height="76" border="0"', html)
        self.assertIn('src="/app_dotnet/AppExtranet/imagens/01_final.png"', html)
        positions = [
            html.index('Título &amp; segurança'), html.index('Introdução com'),
            html.index('Opções fiscais'), html.index('Acesse através'),
            html.index('<ul'), html.index('<img'), html.index('Tela final'),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_html_escapes_content_and_contains_no_active_or_external_content(self) -> None:
        result = self.export()
        html = Path(result['html']).read_text(encoding='utf-8')
        lowered = html.lower()
        self.assertIn('&lt;script&gt;', lowered)
        for forbidden in ('<script', '<iframe', ' onclick=', ' onload=', 'javascript:', 'data:image', 'file:', 'c:\\'):
            self.assertNotIn(forbidden, lowered)
        with self.assertRaises(ValueError):
            safe_image_src('https://externo/imagem.png')
        with self.assertRaises(ValueError):
            safe_image_src('../imagem.png')
        with self.assertRaises(ValueError):
            safe_image_src('C:\\temp\\imagem.png')
        with self.assertRaises(ValueError):
            safe_image_src('data:image/png;base64,abc')

    def test_html_is_in_zip_and_manifest_with_correct_hash_and_mime(self) -> None:
        result = self.export()
        html_path = Path(result['html'])
        with zipfile.ZipFile(result['zip']) as archive:
            self.assertIn('novidade.html', archive.namelist())
            self.assertEqual(archive.read('novidade.html'), html_path.read_bytes())
        manifest = ET.parse(result['manifest']).getroot()
        entry = next(
            item for item in manifest.findall('./arquivos/arquivo')
            if item.attrib['caminho'] == 'novidade.html'
        )
        self.assertEqual(entry.attrib['papel'], 'documento_html_freetextbox')
        self.assertEqual(entry.attrib['mimeType'], 'text/html')
        self.assertEqual(entry.attrib['tamanho'], str(html_path.stat().st_size))
        self.assertEqual(entry.attrib['sha256'], sha256(html_path.read_bytes()).hexdigest())

    def test_json_and_xml_keep_lists_highlights_and_order(self) -> None:
        result = self.export()
        metadata = json.loads(Path(result['json']).read_text(encoding='utf-8'))
        self.assertEqual(
            [block['tipo'] for block in metadata['blocos']],
            ['titulo', 'texto', 'subtitulo', 'texto', 'lista', 'imagem'],
        )
        root = ET.parse(result['xml']).getroot()
        self.assertIsNotNone(root.find("./conteudo/bloco[@tipo='lista']/lista/item/destaque"))


if __name__ == '__main__':
    unittest.main()