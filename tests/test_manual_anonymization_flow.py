from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from components.image_editor import AnonymizationAreaEditor
from models.anonymization_area import AnonymizationArea
from models.annotation import Annotation
from services.image_service import (
    DATA_ROOT,
    build_anonymization_instructions,
    build_anonymization_mask,
    render_anonymization_preview,
    render_annotations,
    validate_changes_within_mask,
)
from services.providers.exceptions import ProviderError
from services.providers.gemini_provider import GeminiProvider
from services.providers.openai_provider import OpenAIProvider


class ManualAnonymizationFlowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='anon_flow_', dir=DATA_ROOT))
        self.source = self.root / 'source.png'
        Image.new('RGB', (120, 80), 'white').save(self.source)
        draw = ImageDraw.Draw(Image.open(self.source))
        with Image.open(self.source) as image:
            edited = image.convert('RGB')
        draw = ImageDraw.Draw(edited)
        draw.text((10, 10), 'CLIENTE ABC', fill='black')
        edited.save(self.source)
        self.area = AnonymizationArea(
            points=[(0.05, 0.05), (0.55, 0.45)],
            data_type='Cliente', replacement_text='Cliente anonimizado', note='campo superior',
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_create_edit_and_delete_anonymization_area_locally(self) -> None:
        editor = AnonymizationAreaEditor()
        first = editor.click(0.1, 0.1, data_type='Cliente', replacement_text='Cliente X')
        self.assertIn('ponto final', first)
        second = editor.click(0.5, 0.4, data_type='Cliente', replacement_text='Cliente X')
        self.assertIn('pronta', second)
        area = editor.confirm()
        self.assertIsNotNone(area)
        areas = [area]
        areas[0].replacement_text = 'Cliente Y'
        self.assertEqual(areas[0].replacement_text, 'Cliente Y')
        areas = [item for item in areas if item.id != area.id]
        self.assertEqual(areas, [])

    def test_old_session_without_anonymization_areas_is_compatible(self) -> None:
        area = AnonymizationArea.from_dict({
            'points': [(0.1, 0.1), (0.4, 0.4)],
            'data_type': 'Tipo antigo',
        })
        self.assertEqual(area.data_type, 'Texto personalizado')
        self.assertEqual(area.replacement_text, '')

    def test_mask_has_same_dimensions_and_supports_multiple_areas(self) -> None:
        mask = build_anonymization_mask(
            self.source, self.root / 'mask.png',
            [self.area, AnonymizationArea(points=[(0.7, 0.7), (0.9, 0.9)], data_type='Nome')],
        )
        with Image.open(self.source) as source, Image.open(mask) as mask_image:
            self.assertEqual(mask_image.size, source.size)
            self.assertEqual(mask_image.mode, 'L')
            self.assertGreater(mask_image.histogram()[255], 0)

    def test_instructions_include_each_area_without_api_key(self) -> None:
        instructions = build_anonymization_instructions([self.area])
        self.assertIn('Cliente', instructions)
        self.assertIn('Cliente anonimizado', instructions)
        self.assertIn('fora da mascara', instructions)
        self.assertNotIn('api_key', instructions.lower())

    def test_rejects_changes_outside_mask_and_preserves_previous_image_candidate(self) -> None:
        mask = build_anonymization_mask(self.source, self.root / 'mask.png', [self.area])
        invalid = self.root / 'invalid.png'
        with Image.open(self.source) as image:
            result = image.convert('RGB')
        ImageDraw.Draw(result).rectangle((90, 60, 110, 75), fill='red')
        result.save(invalid)
        valid, pixels = validate_changes_within_mask(self.source, invalid, mask)
        self.assertFalse(valid)
        self.assertGreater(pixels, 0)
        self.assertNotEqual(invalid.read_bytes(), self.source.read_bytes())

    def test_accepts_changes_inside_mask(self) -> None:
        mask = build_anonymization_mask(self.source, self.root / 'mask.png', [self.area])
        valid_path = self.root / 'valid.png'
        with Image.open(self.source) as image:
            result = image.convert('RGB')
        ImageDraw.Draw(result).rectangle((8, 8, 60, 35), fill='gray')
        result.save(valid_path)
        valid, _ = validate_changes_within_mask(self.source, valid_path, mask)
        self.assertTrue(valid)

    def test_anonymization_preview_trace_is_not_written_to_final_documentation_image(self) -> None:
        preview = self.root / 'preview.png'
        final = self.root / 'final.png'
        annotations = [Annotation(kind='arrow', points=[(0.1, 0.8), (0.9, 0.8)])]
        render_anonymization_preview(self.source, preview, annotations, [self.area])
        render_annotations(self.source, final, annotations)
        self.assertNotEqual(preview.read_bytes(), final.read_bytes())

    def test_gemini_masked_anonymization_is_safely_unavailable(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            GeminiProvider().anonymize_marked_areas(
                api_key='abc123', model='gemini-2.5-flash-image-preview',
                source=self.source, mask=self.source,
                instructions='somente mascara', destination=self.root / 'out.png',
            )
        self.assertEqual(caught.exception.code, 'gemini_masked_anonymization_unavailable')

    def test_openai_masked_flow_sends_image_mask_and_instructions(self) -> None:
        mask = build_anonymization_mask(self.source, self.root / 'mask.png', [self.area])
        response = Mock()
        response.data = [Mock(b64_json=self._encoded_png())]
        client = Mock()
        client.images.edit.return_value = response
        provider = OpenAIProvider()
        provider._client = Mock(return_value=client)
        with patch('services.providers.openai_provider.validate_image_model', return_value='gpt-image-1.5'):
            destination = provider.anonymize_marked_areas(
                api_key='sk-test', model='gpt-image-1.5', source=self.source,
                mask=mask, instructions='somente mascara', destination=self.root / 'out.png',
            )
        self.assertTrue(destination.is_file())
        kwargs = client.images.edit.call_args.kwargs
        self.assertEqual(kwargs['prompt'], 'somente mascara')
        self.assertIn('mask', kwargs)
        self.assertIn('image', kwargs)

    def test_legacy_treat_image_is_blocked(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            OpenAIProvider().treat_image(
                api_key='sk-test', model='gpt-image-1.5', prompt='anonimize tudo',
                source=self.source, destination=self.root / 'out.png',
            )
        self.assertEqual(caught.exception.code, 'automatic_anonymization_disabled')

    def _encoded_png(self) -> str:
        import base64
        import io
        buffer = io.BytesIO()
        Image.new('RGB', (120, 80), 'white').save(buffer, 'PNG')
        return base64.b64encode(buffer.getvalue()).decode('ascii')

    def test_ui_contract_has_explicit_button_and_disables_without_areas(self) -> None:
        source = Path(__file__).resolve().parents[1].joinpath(
            'components', 'advanced_image_adjuster.py'
        ).read_text(encoding='utf-8')
        self.assertIn('Marcar área para anonimização', source)
        self.assertIn('Anonimizar áreas marcadas com IA', source)
        self.assertIn('process_button.disable()', source)
        self.assertIn('Nenhuma IA foi chamada', source)

    def test_app_does_not_call_manual_anonymization_on_save_approve_or_export(self) -> None:
        source = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8')
        save_region = source[source.index('def content_changed()'):source.index('def open_annotations(')]
        approve_region = source[source.index('def approve()'):source.index('def generate_again()')]
        export_region = source[source.index('def export_novelty('):source.index("with ui.row().classes('w-full justify-center gap-2 mt-5')")]
        self.assertNotIn('anonymize_marked_areas(', save_region)
        self.assertNotIn('anonymize_marked_areas(', approve_region)
        self.assertNotIn('anonymize_marked_areas(', export_region)
        self.assertIn('on_anonymize=anonymize_marked_areas', source)

    def test_screen_state_shape_persists_anonymization_areas_without_credentials(self) -> None:
        payload = {
            'session_id': 'sessao',
            'images': [{'id': 'imagem_1', 'anonymization_areas': [self.area.to_dict()]}],
            'api_key': 'secret-value',
        }
        from services.screen_state_service import ScreenStateStore
        store = ScreenStateStore(self.root / 'screen_state.json')
        store.save(payload)
        saved = store.load()
        self.assertEqual(len(saved['images'][0]['anonymization_areas']), 1)
        self.assertNotIn('api_key', saved)


if __name__ == '__main__':
    unittest.main()
