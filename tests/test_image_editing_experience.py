from __future__ import annotations

from hashlib import sha256
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from components.advanced_image_adjuster import AdvancedImageAdjuster
import components.advanced_image_adjuster as advanced_image_adjuster
from components.image_editor import ImageEditor
from models.annotation import Annotation
from services.image_service import DATA_ROOT, SessionWorkspace, build_composition_exports, render_annotations
from services.screen_state_service import ScreenStateStore


class ImageEditingExperienceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='image_edit_', dir=DATA_ROOT))
        self.source = self.root / 'original.png'
        Image.new('RGB', (200, 100), 'white').save(self.source)
        self._notify = advanced_image_adjuster.ui.notify
        advanced_image_adjuster.ui.notify = lambda *args, **kwargs: None

    def tearDown(self) -> None:
        advanced_image_adjuster.ui.notify = self._notify
        shutil.rmtree(self.root)

    def values(self, kind: str, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = dict(
            kind=kind, color='#ff0000', width=4, font_size=24,
            text_background='#ffffff', opacity=0.4 if kind == 'highlight' else 1,
            text='Texto', number=7, border_color='#ff0000', fill_color='#ffff00',
            fill_enabled=kind in {'highlight'}, fill_opacity=0.3,
            arrow_head_size=18, arrow_head_style='filled', double_arrow=False,
            bold=False, text_background_enabled=True,
        )
        values.update(overrides)
        return values

    def click(self, editor: ImageEditor, kind: str, **overrides: object) -> None:
        values = self.values(kind, **overrides)
        editor.click(0.2, 0.2, **values)
        if kind in editor.TWO_POINT_KINDS:
            editor.click(0.8, 0.7, **values)
        self.assertTrue(editor.confirm())

    def workspace(self) -> SessionWorkspace:
        return SessionWorkspace(
            session_id='edit-session', original_dir=self.root,
            processed_dir=self.root, export_dir=self.root / 'export', temp_dir=self.root / 'temp',
        )

    def image_item(self, editor: ImageEditor, image_id: str = 'imagem_001') -> dict[str, object]:
        return {
            'id': image_id, 'filename': 'original.png', 'original': self.source,
            'treated': self.source, 'annotated': None, 'final': None,
            'size': (200, 100), 'status': 'Pronta', 'error': '', 'editor': editor,
        }

    def test_all_tools_create_normalized_annotations(self) -> None:
        editor = ImageEditor()
        kinds = ('arrow', 'line', 'circle', 'rectangle', 'text', 'marker', 'highlight', 'arrow_text')
        for kind in kinds:
            self.click(editor, kind)
        self.assertEqual([item.kind for item in editor.document.items], list(kinds))
        for annotation in editor.document.items:
            for x, y in annotation.points:
                self.assertGreaterEqual(x, 0)
                self.assertLessEqual(x, 1)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(y, 1)

    def test_existing_annotation_can_move_update_delete_undo_and_redo(self) -> None:
        editor = ImageEditor()
        self.click(editor, 'text')
        annotation_id = editor.document.items[0].id
        before = list(editor.document.items[0].points)
        self.assertTrue(editor.update_selected(annotation_id, text='Alterado', color='#0000ff'))
        self.assertTrue(editor.move_selected(annotation_id, 0.1, 0.1))
        self.assertNotEqual(editor.document.items[0].points, before)
        self.assertEqual(editor.document.items[0].text, 'Alterado')
        self.assertTrue(editor.document.delete(annotation_id))
        self.assertTrue(editor.document.undo())
        self.assertEqual(len(editor.document.items), 1)
        self.assertTrue(editor.document.redo())
        self.assertEqual(len(editor.document.items), 0)

    def test_highlight_creates_derived_file_and_preserves_original(self) -> None:
        before = sha256(self.source.read_bytes()).hexdigest()
        destination = self.root / 'derived.png'
        render_annotations(
            self.source, destination,
            [Annotation(kind='highlight', points=[(0.1, 0.1), (0.9, 0.5)], opacity=0.35)],
        )
        self.assertTrue(destination.is_file())
        self.assertNotEqual(destination.read_bytes(), self.source.read_bytes())
        self.assertEqual(before, sha256(self.source.read_bytes()).hexdigest())

    def test_shape_rendering_accepts_all_drag_directions(self) -> None:
        destination = self.root / 'directions.png'
        cases = [
            ((0.2, 0.2), (0.8, 0.7)),
            ((0.8, 0.2), (0.2, 0.7)),
            ((0.2, 0.7), (0.8, 0.2)),
            ((0.8, 0.7), (0.2, 0.2)),
        ]
        for start, end in cases:
            with self.subTest(start=start, end=end):
                render_annotations(
                    self.source, destination,
                    [
                        Annotation(kind='rectangle', points=[start, end], fill_enabled=True),
                        Annotation(kind='circle', points=[start, end], fill_enabled=True),
                        Annotation(kind='highlight', points=[start, end], opacity=0.35),
                    ],
                )
                self.assertTrue(destination.is_file())

    def test_arrows_render_horizontal_vertical_diagonal_short_long_and_double(self) -> None:
        destination = self.root / 'arrows.png'
        annotations = [
            Annotation(kind='arrow', points=[(0.1, 0.2), (0.9, 0.2)]),
            Annotation(kind='arrow', points=[(0.2, 0.1), (0.2, 0.9)]),
            Annotation(kind='arrow', points=[(0.1, 0.9), (0.9, 0.1)]),
            Annotation(kind='arrow', points=[(0.45, 0.45), (0.52, 0.52)], arrow_head_style='open'),
            Annotation(kind='arrow', points=[(0.9, 0.8), (0.1, 0.8)], double_arrow=True),
        ]
        render_annotations(self.source, destination, annotations)
        self.assertTrue(destination.is_file())
        self.assertNotEqual(destination.read_bytes(), self.source.read_bytes())

    def test_all_shapes_render_on_small_and_large_images(self) -> None:
        small = self.root / 'small.png'
        large = self.root / 'large.png'
        Image.new('RGB', (80, 50), 'white').save(small)
        Image.new('RGB', (1800, 1200), 'white').save(large)
        annotations = [
            Annotation(kind='arrow', points=[(0.1, 0.1), (0.9, 0.8)]),
            Annotation(kind='line', points=[(0.1, 0.8), (0.9, 0.8)]),
            Annotation(kind='rectangle', points=[(0.1, 0.1), (0.4, 0.4)], fill_enabled=True),
            Annotation(kind='circle', points=[(0.5, 0.1), (0.9, 0.4)], fill_enabled=True),
            Annotation(kind='highlight', points=[(0.1, 0.5), (0.9, 0.7)]),
            Annotation(kind='text', points=[(0.1, 0.75)], text='Texto', bold=True),
            Annotation(kind='marker', points=[(0.5, 0.5)], number=3),
            Annotation(kind='arrow_text', points=[(0.1, 0.9), (0.9, 0.5)], text='Seta com texto'),
        ]
        for source in (small, large):
            destination = self.root / f'{source.stem}_marked.png'
            render_annotations(source, destination, annotations)
            self.assertTrue(destination.is_file())
            self.assertNotEqual(destination.read_bytes(), source.read_bytes())

    def test_save_confirms_active_draft_and_exports_final_with_annotations(self) -> None:
        editor = ImageEditor()
        values = self.values('arrow')
        editor.click(0.1, 0.1, **values)
        editor.click(0.9, 0.8, **values)
        item = self.image_item(editor)
        persisted: list[dict[str, object]] = []

        def persist() -> None:
            persisted.append({'annotations': editor.document.to_list(), 'final': item['final']})

        adjuster = AdvancedImageAdjuster([item], self.workspace(), persist)
        adjuster.save()
        self.assertEqual(len(editor.document.items), 1)
        self.assertEqual(len(persisted[-1]['annotations']), 1)
        self.assertTrue(Path(item['final']).is_file())
        self.assertNotEqual(Path(item['final']).read_bytes(), self.source.read_bytes())

    def test_manual_add_then_save_does_not_duplicate_annotation(self) -> None:
        editor = ImageEditor()
        values = self.values('arrow')
        editor.click(0.2, 0.2, **values)
        editor.click(0.8, 0.7, **values)
        self.assertTrue(editor.confirm())
        item = self.image_item(editor)
        AdvancedImageAdjuster([item], self.workspace(), lambda: None).save()
        self.assertEqual(len(editor.document.items), 1)

    def test_old_session_annotation_loads_with_safe_defaults(self) -> None:
        restored = Annotation.from_dict({
            'kind': 'arrow', 'points': [(0.1, 0.1), (0.8, 0.8)],
            'color': '#00ff00', 'width': 3,
        })
        self.assertEqual(restored.border_color, '#00ff00')
        self.assertEqual(restored.arrow_head_style, 'filled')
        self.assertFalse(restored.double_arrow)
        self.assertTrue(restored.text_background_enabled)

    def test_composition_export_uses_marked_final_and_persists_annotations_json(self) -> None:
        editor = ImageEditor()
        self.click(editor, 'arrow_text', text='Leia aqui')
        item = self.image_item(editor)
        item['final'] = render_annotations(
            self.source, self.workspace().export_dir / 'geradas' / 'imagem_001' / 'imagem_final.png',
            editor.document.items,
        )
        exports = build_composition_exports(
            self.workspace(), [{**item, 'annotations': list(editor.document.items)}],
            {'origem': 'manual', 'titulo': 'Teste', 'texto_novidade': 'Texto',
             'blocos': [{'tipo': 'imagem', 'imagem_id': 'imagem_001'}]},
            'prompt',
        )
        self.assertTrue(Path(exports['final']).is_file())
        self.assertTrue(Path(exports['treated']).is_file())
        self.assertNotEqual(Path(exports['final']).read_bytes(), self.source.read_bytes())
        self.assertEqual(Path(exports['treated']).read_bytes(), self.source.read_bytes())
        annotation_files = list((self.workspace().export_dir / 'novidade' / 'anotacoes').glob('*_anotacoes.json'))
        self.assertEqual(len(annotation_files), 1)
        annotations = json.loads(annotation_files[0].read_text(encoding='utf-8'))
        self.assertEqual(len(annotations), 1)
        self.assertEqual(annotations[0]['kind'], 'arrow_text')

    def test_adjuster_opens_requested_image(self) -> None:
        images = [
            {'id': 'imagem_001', 'filename': 'a.png', 'editor': ImageEditor()},
            {'id': 'imagem_002', 'filename': 'b.png', 'editor': ImageEditor()},
        ]
        adjuster = AdvancedImageAdjuster(images, self.workspace(), lambda: None, 'imagem_002')
        self.assertEqual(adjuster.current()['id'], 'imagem_002')

    def test_preview_exposes_direct_edit_button_and_selected_callback(self) -> None:
        root = Path(__file__).resolve().parents[1]
        preview = (root / 'components' / 'novelty_preview.py').read_text(encoding='utf-8')
        app = (root / 'app.py').read_text(encoding='utf-8')
        adjuster = (root / 'components' / 'advanced_image_adjuster.py').read_text(encoding='utf-8')
        for forbidden in ('ProviderFactory', 'generate_novelty_plan', 'review_novelty', 'generate_cover'):
            self.assertNotIn(forbidden, adjuster)
        self.assertIn('Forma da marcação', adjuster)
        self.assertIn("'arrow_text': 'Seta com texto'", adjuster)
        self.assertIn("'Editar imagem'", preview)
        self.assertIn('on_edit_image(value)', preview)
        self.assertIn("state['annotation_image_id'] = image_id", app)
        self.assertIn("selected_id=state.get('annotation_image_id')", app)


if __name__ == '__main__':
    unittest.main()
