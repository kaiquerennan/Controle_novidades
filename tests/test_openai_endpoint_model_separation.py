from __future__ import annotations

import base64
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from core.config import IMAGE_MODELS, TEXT_MODELS
from models.novelty_plan import FidelityReview, NoveltyPlan, PlannedBlock
from services.image_service import DATA_ROOT
from services.providers.exceptions import ProviderError
from services.providers.openai_provider import OpenAIProvider


class OpenAIEndpointModelSeparationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='openai_model_split_', dir=DATA_ROOT))
        self.source = self.root / 'source.png'
        self.mask = self.root / 'mask.png'
        Image.new('RGB', (32, 20), (20, 40, 60)).save(self.source, 'PNG')
        Image.new('L', (32, 20), 0).save(self.mask, 'PNG')
        buffer = io.BytesIO()
        Image.new('RGB', (32, 20), (80, 100, 120)).save(buffer, 'PNG')
        self.encoded_image = base64.b64encode(buffer.getvalue()).decode('ascii')
        self.provider = OpenAIProvider()
        self.client = MagicMock()

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_response_and_image_endpoints_receive_only_their_own_models(self) -> None:
        plan = NoveltyPlan(blocos=[
            PlannedBlock(tipo='titulo', conteudo='Título'),
            PlannedBlock(tipo='texto', conteudo='Texto'),
        ])
        review = FidelityReview(aprovada=True)
        with patch.object(self.provider, '_client', return_value=self.client):
            self.client.responses.parse.return_value = SimpleNamespace(output_parsed=plan)
            self.provider.generate_novelty_plan(
                api_key='test-key', model=TEXT_MODELS['openai'], raw_text='texto',
                images=[], prompt='organizar',
            )
            self.assertEqual(
                self.client.responses.parse.call_args.kwargs['model'],
                TEXT_MODELS['openai'],
            )

            self.client.responses.parse.reset_mock()
            self.client.responses.parse.return_value = SimpleNamespace(output_parsed=review)
            self.provider.review_novelty(
                api_key='test-key', model=TEXT_MODELS['openai'], raw_text='texto',
                document={'blocos': []}, image_ids=[], prompt='revisar',
            )
            self.assertEqual(
                self.client.responses.parse.call_args.kwargs['model'],
                TEXT_MODELS['openai'],
            )

            self.client.responses.create.return_value = SimpleNamespace(output_text='Orientação')
            self.provider.suggest_orientation(
                api_key='test-key', model=TEXT_MODELS['openai'], source=self.source,
            )
            self.assertEqual(
                self.client.responses.create.call_args.kwargs['model'],
                TEXT_MODELS['openai'],
            )

            self.client.images.edit.return_value = SimpleNamespace(
                data=[SimpleNamespace(b64_json=self.encoded_image)]
            )
            self.provider.anonymize_marked_areas(
                api_key='test-key', model=IMAGE_MODELS['openai'], source=self.source,
                mask=self.mask, instructions='anonimizar area marcada',
                destination=self.root / 'treated.png',
            )
            self.assertEqual(
                self.client.images.edit.call_args.kwargs['model'],
                IMAGE_MODELS['openai'],
            )
            self.assertIn('mask', self.client.images.edit.call_args.kwargs)

            self.client.images.generate.return_value = SimpleNamespace(
                data=[SimpleNamespace(b64_json=self.encoded_image)]
            )
            self.provider.generate_cover(
                api_key='test-key', model=IMAGE_MODELS['openai'], prompt='capa',
                destination=self.root / 'cover.png',
            )
            self.assertEqual(
                self.client.images.generate.call_args.kwargs['model'],
                IMAGE_MODELS['openai'],
            )

    def test_crossed_models_are_rejected_before_any_openai_endpoint(self) -> None:
        with patch.object(self.provider, '_client', return_value=self.client):
            with self.assertRaises(ProviderError) as text_error:
                self.provider.generate_novelty_plan(
                    api_key='test-key', model=IMAGE_MODELS['openai'], raw_text='texto',
                    images=[], prompt='organizar',
                )
            self.assertEqual(text_error.exception.code, 'image_model_in_text_flow')
            self.client.responses.parse.assert_not_called()

            with self.assertRaises(ProviderError) as image_error:
                self.provider.anonymize_marked_areas(
                    api_key='test-key', model=TEXT_MODELS['openai'], source=self.source,
                    mask=self.mask, instructions='anonimizar area marcada',
                    destination=self.root / 'invalid.png',
                )
            self.assertEqual(image_error.exception.code, 'text_model_in_image_flow')
            self.client.images.edit.assert_not_called()

            with self.assertRaises(ProviderError) as legacy_error:
                self.provider.treat_image(
                    api_key='test-key', model=IMAGE_MODELS['openai'], prompt='anonimizar',
                    source=self.source, destination=self.root / 'legacy.png',
                )
            self.assertEqual(legacy_error.exception.code, 'automatic_anonymization_disabled')

            with self.assertRaises(ProviderError) as cover_error:
                self.provider.generate_cover(
                    api_key='test-key', model=TEXT_MODELS['openai'], prompt='capa',
                    destination=self.root / 'invalid_cover.png',
                )
            self.assertEqual(cover_error.exception.code, 'text_model_in_image_flow')
            self.client.images.generate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
