from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from models.novelty_plan import NoveltyPlan, PlannedBlock
from services.editorial_orchestration_service import (
    REVIEWER_PROMPT,
    WRITER_ORGANIZER_PROMPT,
    document_from_editorial_plan,
)
from services.image_service import DATA_ROOT, SessionWorkspace
from services.screen_state_service import ScreenStateStore
from services.text_sanitizer import strip_markdown


class WriterEditorialQualityTestCase(unittest.TestCase):
    def test_prompt_requires_editorial_flow_and_plain_text(self) -> None:
        prompt = WRITER_ORGANIZER_PROMPT.lower()
        for expected in (
            'publicação editorial', 'qual é a novidade', 'como ela funciona',
            'onde ela está disponível', 'poucos parágrafos', 'título natural',
            'acesse através do caminho', 'não use markdown',
        ):
            self.assertIn(expected, prompt)
        for forbidden_expression in (
            'criado campo', 'próximos passos', 'nova ordem de mudança',
            'será realizado',
        ):
            self.assertIn(forbidden_expression, prompt)

    def test_prompt_forbids_summarizing_or_omitting_functional_facts(self) -> None:
        writer = WRITER_ORGANIZER_PROMPT.lower()
        for expected in (
            'não resuma', 'não omita funcionalidades', 'campos', 'menus', 'botões',
            'abas', 'parâmetros', 'listas de opções', 'exemplos fornecidos',
            'observações importantes', 'cobertura informacional equivalente',
            'esse caminho é obrigatório', 'editor técnico',
            'nunca misture alterações de telas', 'não crie introdução ou conclusão genérica',
            'otimiza processos',
        ):
            self.assertIn(expected, writer)
        reviewer = ' '.join(REVIEWER_PROMPT.lower().split())
        for expected in (
            'cobertura informacional item a item', 'nenhuma funcionalidade',
            'caminho do sistema', 'substituido por descricao generica',
            'correcoes_necessarias', 'preservar integralmente',
        ):
            self.assertIn(expected, reviewer)
    def test_markdown_is_removed_from_every_visible_editorial_field(self) -> None:
        plan = NoveltyPlan(blocos=[
            PlannedBlock(tipo='titulo', conteudo='# **Perfis IBS/CBS**'),
            PlannedBlock(
                tipo='texto', papel='introducao',
                conteudo='__A novidade__ reúne os perfis.',
            ),
            PlannedBlock(tipo='subtitulo', conteudo='## Onde está disponível'),
            PlannedBlock(
                tipo='texto',
                conteudo='Acesse: Fiscal > Configurações > Perfis',
            ),
        ])
        document = document_from_editorial_plan(plan, [])
        visible_text = '\n'.join(
            block.conteudo for block in document.blocks if block.conteudo
        )
        for marker in ('**', '__', '#', '`'):
            self.assertNotIn(marker, visible_text)
        self.assertIn(
            'Acesse através do caminho:\nFiscal / Configurações / Perfis', visible_text
        )

    def test_path_is_a_separate_semantic_block_without_markdown(self) -> None:
        plan = NoveltyPlan(blocos=[
            PlannedBlock(tipo='titulo', conteudo='Novidade'),
            PlannedBlock(tipo='texto', papel='introducao', conteudo='Introdução objetiva.'),
            PlannedBlock(
                tipo='texto',
                conteudo='Acesse: Fiscal > Cadastros > Perfis',
            ),
        ])
        document = document_from_editorial_plan(plan, [])
        path_blocks = [block for block in document.blocks if block.papel == 'caminho']
        self.assertEqual(len(path_blocks), 1)
        self.assertEqual(
            path_blocks[0].conteudo,
            'Acesse através do caminho:\nFiscal / Cadastros / Perfis',
        )
        self.assertNotIn('**', path_blocks[0].conteudo)

    def test_plain_text_sanitizer_preserves_content(self) -> None:
        value = strip_markdown('## Título\n- **Opção A**\n[Portal](https://example.com)')
        self.assertEqual(value, 'Título\n- Opção A\nPortal (https://example.com)')


class ScreenPersistenceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='screen_state_', dir=DATA_ROOT))
        self.store = ScreenStateStore(self.root / 'state.json')

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_snapshot_round_trip_preserves_screen_without_secrets(self) -> None:
        payload = {
            'session_id': 'a' * 32,
            'raw_text': 'Texto ainda em edição',
            'images': [{
                'id': 'imagem_001', 'original': 'data/originals/a/tela.png',
                'annotations': [{'kind': 'arrow', 'points': [[0.1, 0.2], [0.8, 0.9]]}],
            }],
            'composition': {
                'origem': 'manual',
                'blocos': [{'id': 'b1', 'tipo': 'titulo', 'conteudo': 'Título'}],
            },
            'generated': True,
            'approved': False,
            'current_step': 'Visualizar',
            'settings': {
                'provider': 'openai', 'text_model': 'gpt-5-mini',
                'image_model': 'gpt-image-1.5', 'cover_requested': False,
                'api_key': 'must-not-be-persisted',
            },
            'authorization': 'Bearer secret',
        }
        self.store.save(payload)
        restored = self.store.load()
        self.assertIsNotNone(restored)
        self.assertEqual(restored['raw_text'], payload['raw_text'])
        self.assertEqual(restored['images'], payload['images'])
        self.assertEqual(restored['composition'], payload['composition'])
        self.assertEqual(restored['current_step'], 'Visualizar')
        self.assertEqual(restored['settings']['text_model'], 'gpt-5-mini')
        self.assertEqual(restored['settings']['image_model'], 'gpt-image-1.5')
        serialized = self.store.path.read_text(encoding='utf-8')
        self.assertNotIn('must-not-be-persisted', serialized)
        self.assertNotIn('Bearer secret', serialized)
        self.assertNotIn('api_key', restored['settings'])

    def test_corrupt_snapshot_is_ignored(self) -> None:
        self.store.path.write_text('{invalid', encoding='utf-8')
        self.assertIsNone(self.store.load())

    def test_workspace_can_be_reopened_and_cleared(self) -> None:
        session_id = 'b' * 32
        workspace = SessionWorkspace.open(session_id)
        marker = workspace.original_dir / 'marker.txt'
        marker.write_text('preserved', encoding='utf-8')
        reopened = SessionWorkspace.open(session_id)
        self.assertEqual(reopened.original_dir, workspace.original_dir)
        self.assertTrue(marker.is_file())
        reopened.clear()
        self.assertFalse(workspace.original_dir.exists())


class PersistenceUiContractTestCase(unittest.TestCase):
    def test_ui_restores_before_generation_and_has_confirmed_reset(self) -> None:
        source = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(
            encoding='utf-8'
        )
        restore_start = source.index('saved = state_store.load()')
        generation_start = source.index('async def generate_novelty()')
        restore_region = source[restore_start:generation_start]
        self.assertNotIn('editorial_orchestrator.generate(', restore_region)
        self.assertIn('Nova Novidade', source)
        self.assertIn('with ui.dialog() as new_novelty_dialog', source)
        self.assertIn('persist_screen_state()', source)
        self.assertIn("'composition': composition.to_dict()", source)
        self.assertIn("'text_model': str(controls['text_model'].value or '')", source)
        self.assertIn("'image_model': str(controls['image_model'].value or '')", source)
        self.assertIn("saved_settings.get('text_model')", source)
        self.assertIn("saved_settings.get('image_model')", source)
        self.assertNotIn("'api_key':", source[source.index('state_store.save({'):generation_start])


if __name__ == '__main__':
    unittest.main()