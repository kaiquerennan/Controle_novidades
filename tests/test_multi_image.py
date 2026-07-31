from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from components.image_editor import ImageEditor
from models.annotation import Annotation
from services.image_service import (
    DATA_ROOT,
    SessionWorkspace,
    build_multi_exports,
    process_image_batch,
)


class MultiImageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='multi_image_test_', dir=DATA_ROOT))
        self.workspace = SessionWorkspace(
            session_id='test-session',
            original_dir=self.root / 'originals',
            processed_dir=self.root / 'processed',
            export_dir=self.root / 'exports',
            temp_dir=self.root / 'temp',
        )
        for folder in (
            self.workspace.original_dir,
            self.workspace.processed_dir,
            self.workspace.export_dir,
            self.workspace.temp_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def image(self, path: Path, color: tuple[int, int, int]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (20, 12), color).save(path, 'PNG')
        return path

    def item(self, position: int, color: tuple[int, int, int]) -> dict[str, object]:
        original = self.image(
            self.workspace.original_dir / f'{position}_source.png', color
        )
        treated = self.image(
            self.workspace.processed_dir / str(position) / 'treated.png', color
        )
        final = self.image(
            self.workspace.export_dir / 'generated' / str(position) / 'final.png', color
        )
        annotation = Annotation(
            kind='marker', points=[(position / 10, position / 10)], number=position
        )
        return {
            'id': str(position),
            'filename': f'capture_{position}.png',
            'original': original,
            'treated': treated,
            'final': final,
            'status': 'Exportada',
            'error': '',
            'annotations': [annotation],
        }

    def test_one_image_keeps_legacy_and_new_exports(self) -> None:
        result = build_multi_exports(
            self.workspace,
            [self.item(1, (255, 0, 0))],
            'Shared novelty text',
            'unchanged prompt',
        )
        metadata = json.loads(result['json'].read_text(encoding='utf-8'))
        self.assertEqual(metadata['texto_novidade'], 'Shared novelty text')
        self.assertEqual(len(metadata['imagens']), 1)
        self.assertTrue((self.workspace.export_dir / 'marcacoes.json').is_file())
        self.assertTrue((self.workspace.export_dir / 'imagem_tratada.png').is_file())
        with zipfile.ZipFile(result['zip']) as archive:
            names = set(archive.namelist())
        self.assertIn('novidade/novidade.json', names)
        self.assertIn('novidade/imagens/01_final.png', names)
        self.assertIn('marcacoes.json', names)

    def test_three_images_preserve_order_text_and_annotations(self) -> None:
        items = [
            self.item(3, (0, 0, 255)),
            self.item(1, (255, 0, 0)),
            self.item(2, (0, 255, 0)),
        ]
        items[1]['status'] = 'Erro'
        items[1]['error'] = 'simulated error'
        result = build_multi_exports(
            self.workspace, items, 'One text for every image', 'unchanged prompt'
        )
        metadata = json.loads(result['json'].read_text(encoding='utf-8'))
        self.assertEqual(metadata['texto_novidade'], 'One text for every image')
        self.assertEqual(
            [entry['nome_original'] for entry in metadata['imagens']],
            ['capture_3.png', 'capture_1.png', 'capture_2.png'],
        )
        self.assertEqual([entry['ordem'] for entry in metadata['imagens']], [1, 2, 3])
        self.assertEqual(metadata['imagens'][1]['mensagem_erro'], 'simulated error')
        annotation_files = [
            self.workspace.export_dir / 'novidade' / entry['anotacoes']
            for entry in metadata['imagens']
        ]
        marker_numbers = [
            json.loads(path.read_text(encoding='utf-8'))[0]['number']
            for path in annotation_files
        ]
        self.assertEqual(marker_numbers, [3, 1, 2])
        with zipfile.ZipFile(result['zip']) as archive:
            names = set(archive.namelist())
        for position in ('01', '02', '03'):
            self.assertIn(f'novidade/imagens/{position}_original.png', names)
            self.assertIn(f'novidade/imagens/{position}_tratada.png', names)
            self.assertIn(f'novidade/imagens/{position}_final.png', names)
            self.assertIn(f'novidade/anotacoes/{position}_anotacoes.json', names)

    def test_duplicate_upload_names_are_not_overwritten(self) -> None:
        source = self.image(self.root / 'source.png', (20, 40, 60)).read_bytes()
        first, _ = self.workspace.save_original('same.png', source)
        second, _ = self.workspace.save_original('same.png', source)
        self.assertNotEqual(first, second)
        self.assertEqual(second.name, 'same_2.png')

    def test_editors_keep_annotations_independent(self) -> None:
        first = ImageEditor()
        second = ImageEditor()
        first.document.add(Annotation(kind='marker', points=[(0.2, 0.2)], number=1))
        second.document.add(Annotation(kind='rectangle', points=[(0.1, 0.1), (0.8, 0.8)]))
        self.assertEqual(first.document.items[0].kind, 'marker')
        self.assertEqual(second.document.items[0].kind, 'rectangle')
        first.document.clear()
        self.assertEqual(len(first.document.items), 0)
        self.assertEqual(len(second.document.items), 1)


class BatchProcessingTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_batch_is_sequential_skips_completed_and_continues_after_failure(self) -> None:
        items = [
            {'filename': 'one.png', 'status': 'Pendente'},
            {'filename': 'done.png', 'status': 'Tratada com IA'},
            {'filename': 'fail.png', 'status': 'Pendente'},
            {'filename': 'three.png', 'status': 'Pendente'},
        ]
        calls: list[str] = []

        async def process_one(item: dict[str, object]) -> bool:
            calls.append(str(item['filename']))
            if item['filename'] == 'fail.png':
                item['status'] = 'Erro'
                item['error'] = 'simulated failure'
                return False
            item['status'] = 'Tratada com IA'
            return True

        result = await process_image_batch(
            items, process_one, is_cancelled=lambda: False
        )
        self.assertEqual(calls, ['one.png', 'fail.png', 'three.png'])
        self.assertEqual(result['successes'], 2)
        self.assertEqual(result['failures'], 1)
        self.assertEqual(items[2]['error'], 'simulated failure')
        self.assertEqual(items[3]['status'], 'Tratada com IA')

    async def test_batch_cancellation_stops_before_next_image(self) -> None:
        items = [
            {'filename': 'one.png', 'status': 'Pendente'},
            {'filename': 'two.png', 'status': 'Pendente'},
        ]
        calls: list[str] = []

        async def process_one(item: dict[str, object]) -> bool:
            calls.append(str(item['filename']))
            return True

        result = await process_image_batch(
            items, process_one, is_cancelled=lambda: len(calls) == 1
        )
        self.assertEqual(calls, ['one.png'])
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['processed'], 1)


if __name__ == '__main__':
    unittest.main()
