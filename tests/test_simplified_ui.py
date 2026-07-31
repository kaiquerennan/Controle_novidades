from __future__ import annotations

import unittest
from pathlib import Path


class SimplifiedUIContractTestCase(unittest.TestCase):
    def test_main_flow_does_not_render_the_block_builder(self) -> None:
        source = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(
            encoding='utf-8'
        )
        self.assertIn("'Gerar Novidade'", source)
        self.assertIn("'Visualizar Novidade'", source)
        self.assertNotIn("ui.label('MONTAGEM DA NOVIDADE')", source)
        self.assertNotIn("on_review_text=review_text_with_ai", source)
        self.assertNotIn("on_suggest_positions=suggest_positions_with_ai", source)
        self.assertNotIn('generate_automatic_novelty(', source)
        self.assertNotIn('provider.treat_image', source)

    def test_manual_builder_is_only_inside_collapsed_advanced_section(self) -> None:
        source = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(
            encoding='utf-8'
        )
        panel = source.index('def advanced_panel()')
        advanced = source.index('with ui.expansion(', panel)
        builder = source.index('CompositionBuilder(', advanced)
        collapsed = source.index("advanced.value = bool(state['advanced_open'])", builder)
        self.assertLess(advanced, builder)
        self.assertLess(builder, collapsed)
        self.assertIn("'advanced_open': False", source)


if __name__ == '__main__':
    unittest.main()
