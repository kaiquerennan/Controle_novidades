from __future__ import annotations

import unittest

from core.config import (
    DEFAULT_MODELS,
    IMAGE_MODELS,
    OPENAI_IMAGE_MODEL,
    OPENAI_TEXT_MODEL,
    TEXT_MODELS,
)
from services.model_routing import (
    configured_image_model,
    configured_text_model,
    validate_image_model,
    validate_text_model,
)
from services.providers.exceptions import ProviderError


class ModelRoutingTestCase(unittest.TestCase):
    def test_openai_defaults_are_explicit_and_independent(self) -> None:
        self.assertEqual(OPENAI_TEXT_MODEL, 'gpt-5-mini')
        self.assertEqual(OPENAI_IMAGE_MODEL, 'gpt-image-1.5')
        self.assertEqual(DEFAULT_MODELS['openai']['text'], OPENAI_TEXT_MODEL)
        self.assertEqual(DEFAULT_MODELS['openai']['image'], OPENAI_IMAGE_MODEL)
        self.assertNotEqual(OPENAI_TEXT_MODEL, OPENAI_IMAGE_MODEL)

    def test_configured_models_use_their_independent_defaults(self) -> None:
        self.assertEqual(configured_text_model('openai'), TEXT_MODELS['openai'])
        self.assertEqual(configured_image_model('openai'), IMAGE_MODELS['openai'])

    def test_selected_text_model_is_respected(self) -> None:
        self.assertEqual(configured_text_model('openai', 'gpt-5-mini'), 'gpt-5-mini')

    def test_gpt_image_is_rejected_by_text_flow(self) -> None:
        with self.assertRaises(ProviderError) as raised:
            validate_text_model('openai', IMAGE_MODELS['openai'])
        self.assertEqual(raised.exception.code, 'image_model_in_text_flow')

    def test_openai_text_model_is_rejected_by_image_flow(self) -> None:
        with self.assertRaises(ProviderError) as raised:
            validate_image_model('openai', TEXT_MODELS['openai'])
        self.assertEqual(raised.exception.code, 'text_model_in_image_flow')

    def test_gemini_defaults_remain_unchanged_and_separate(self) -> None:
        self.assertEqual(TEXT_MODELS['gemini'], 'gemini-2.5-pro')
        self.assertEqual(IMAGE_MODELS['gemini'], 'gemini-3.1-flash-image')
        self.assertEqual(configured_text_model('gemini'), TEXT_MODELS['gemini'])
        self.assertEqual(configured_image_model('gemini'), IMAGE_MODELS['gemini'])


if __name__ == '__main__':
    unittest.main()