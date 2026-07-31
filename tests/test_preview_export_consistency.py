from __future__ import annotations

import unittest
from pathlib import Path

from models.composition import CompositionDocument, ContentBlock
from services.html_export_service import build_novelty_html


class PreviewExportConsistencyTestCase(unittest.TestCase):
    def test_semantic_blocks_survive_persistence_in_exact_order(self) -> None:
        document = CompositionDocument(blocks=[
            ContentBlock(tipo='titulo', conteudo='Título'),
            ContentBlock(tipo='texto', papel='introducao', conteudo='Introdução'),
            ContentBlock(tipo='lista', itens=[
                {'texto': 'Primeiro', 'destaques': ['Primeiro']},
                {'texto': 'Segundo', 'destaques': []},
            ]),
            ContentBlock(
                tipo='texto', papel='caminho',
                conteudo='Acesse através do caminho:\nFiscal / Rotina',
            ),
        ])
        restored = CompositionDocument.from_dict(document.to_dict())
        self.assertEqual(restored.to_dict(), document.to_dict())
        self.assertEqual(
            [block.tipo for block in restored.blocks],
            ['titulo', 'texto', 'lista', 'texto'],
        )
        self.assertEqual(restored.blocks[2].itens[0]['destaques'], ['Primeiro'])

    def test_preview_has_same_semantic_renderers_as_html(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / 'components' / 'novelty_preview.py').read_text(encoding='utf-8')
        self.assertIn("elif block.tipo == 'lista'", source)
        self.assertIn('render_inline(', source)
        self.assertIn("block.papel == 'caminho'", source)
        self.assertIn("item.get('destaques')", source)

    def test_local_preview_and_export_helpers_have_no_provider_or_ai_call(self) -> None:
        root = Path(__file__).resolve().parents[1]
        sources = '\n'.join(
            (root / relative).read_text(encoding='utf-8')
            for relative in (
                'components/novelty_preview.py', 'services/html_export_service.py',
                'components/advanced_image_adjuster.py',
            )
        )
        for forbidden in ('ProviderFactory', 'generate_novelty_plan', 'review_novelty', 'generate_cover'):
            self.assertNotIn(forbidden, sources)

    def test_html_sequence_follows_composition_sequence(self) -> None:
        document = CompositionDocument(blocks=[
            ContentBlock(tipo='titulo', conteudo='Título'),
            ContentBlock(tipo='lista', itens=[{'texto': 'Item', 'destaques': ['Item']}]),
            ContentBlock(tipo='texto', conteudo='Final'),
        ])
        metadata = {**document.to_dict(), 'imagens': []}
        html = build_novelty_html(metadata)
        self.assertLess(html.index('Título'), html.index('<ul'))
        self.assertLess(html.index('<ul'), html.index('Final'))


if __name__ == '__main__':
    unittest.main()