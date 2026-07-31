from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from components.composition_builder import CompositionBuilder
from models.composition import CompositionDocument, ContentBlock
from services.image_service import DATA_ROOT, SessionWorkspace, build_composition_exports


class CompositionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='composition_test_', dir=DATA_ROOT))
        self.workspace = SessionWorkspace(
            session_id='composition-session',
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

    def make_image(self, position: int) -> dict[str, object]:
        color = ((position * 40) % 255, (position * 70) % 255, (position * 90) % 255)
        paths = {}
        for kind, folder in (
            ('original', self.workspace.original_dir),
            ('treated', self.workspace.processed_dir),
            ('final', self.workspace.export_dir / 'generated'),
        ):
            path = folder / str(position) / f'{kind}.png'
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (32, 20), color).save(path, 'PNG')
            paths[kind] = path
        return {
            'id': f'imagem_{position:03d}',
            'filename': f'capture_{position}.png',
            **paths,
            'status': 'Exportada',
            'error': '',
            'annotations': [],
        }

    def test_text_only_novelty(self) -> None:
        document = CompositionDocument()
        title, text = document.blocks
        document.update(title.id, conteudo='Controle de Cashback')
        document.update(text.id, conteudo='Texto introdutório e conclusão.')
        payload = document.to_dict()
        payload['texto_novidade'] = document.text
        result = build_composition_exports(
            self.workspace, [], payload, 'prompt unchanged'
        )
        metadata = json.loads(result['json'].read_text(encoding='utf-8'))
        self.assertEqual(metadata['titulo'], 'Controle de Cashback')
        self.assertEqual([block['tipo'] for block in metadata['blocos']], ['titulo', 'texto'])
        self.assertIsNone(result['treated'])
        with zipfile.ZipFile(result['zip']) as archive:
            self.assertIn('novidade.json', archive.namelist())

    def test_single_image_preserves_legacy_export(self) -> None:
        image = self.make_image(1)
        document = CompositionDocument()
        document.add('imagem', imagem_id=str(image['id']))
        payload = document.to_dict()
        payload['texto_novidade'] = document.text
        result = build_composition_exports(
            self.workspace, [image], payload, 'prompt unchanged'
        )
        self.assertTrue((self.workspace.export_dir / 'marcacoes.json').is_file())
        self.assertTrue((self.workspace.export_dir / 'imagem_final.png').is_file())
        with zipfile.ZipFile(result['zip']) as archive:
            names = set(archive.namelist())
        self.assertIn('novidade.json', names)
        self.assertIn('imagens/01_final.png', names)
        self.assertIn('marcacoes.json', names)

    def test_five_images_interleaved_and_side_by_side_match_export_order(self) -> None:
        images = [self.make_image(position) for position in range(1, 6)]
        document = CompositionDocument()
        title, text = document.blocks
        document.update(title.id, conteudo='Nova configuração')
        document.update(text.id, conteudo='Introdução')
        document.add('imagem', imagem_id='imagem_001', modo_exibicao='ampliado')
        document.add('subtitulo', conteudo='Configuração')
        document.add('texto', conteudo='Detalhes da configuração')
        document.add(
            'imagem', imagem_id='imagem_002', imagem_secundaria_id='imagem_003',
            modo_exibicao='lado_a_lado', legenda='Comparação das telas',
        )
        document.add('divisor')
        document.add('texto', conteudo='Conclusão')
        document.add('imagem', imagem_id='imagem_004')
        document.add('imagem', imagem_id='imagem_005')
        payload = document.to_dict()
        payload['texto_novidade'] = document.text
        result = build_composition_exports(
            self.workspace, images, payload, 'prompt unchanged'
        )
        metadata = json.loads(result['json'].read_text(encoding='utf-8'))
        expected_ids = [block['id'] for block in payload['blocos']]
        exported_ids = [block['id'] for block in metadata['blocos']]
        self.assertEqual(exported_ids, expected_ids)
        self.assertEqual(
            [block['ordem'] for block in metadata['blocos']],
            list(range(1, len(metadata['blocos']) + 1)),
        )
        side = next(
            block for block in metadata['blocos']
            if block.get('modo_exibicao') == 'lado_a_lado'
        )
        self.assertEqual(side['imagens_ids'] if 'imagens_ids' in side else [
            side['imagem_id'], side['imagem_secundaria_id']
        ], ['imagem_002', 'imagem_003'])
        self.assertEqual(len(side['arquivos']), 2)
        image_metadata = {
            item['id']: item for item in metadata['imagens']
        }
        self.assertEqual(
            image_metadata['imagem_002']['posicao_composicao'], side['ordem']
        )
        self.assertEqual(
            image_metadata['imagem_003']['bloco_id'], side['id']
        )
        self.assertEqual(
            image_metadata['imagem_002']['modo_exibicao'], 'lado_a_lado'
        )
        with zipfile.ZipFile(result['zip']) as archive:
            names = set(archive.namelist())
        for position in range(1, 6):
            prefix = f'{position:02d}'
            self.assertIn(f'imagens/{prefix}_original.png', names)
            self.assertIn(f'imagens/{prefix}_tratada.png', names)
            self.assertIn(f'imagens/{prefix}_final.png', names)

    def test_remove_reinsert_split_join_subtitle_and_undo(self) -> None:
        document = CompositionDocument()
        text = document.blocks[1]
        document.update(text.id, conteudo='Primeira parte Segunda parte')
        self.assertTrue(document.split_text(text.id, 14))
        first = document.find(text.id)
        self.assertIsNotNone(first)
        self.assertEqual(len([b for b in document.blocks if b.tipo == 'texto']), 2)
        self.assertTrue(document.join_with_next(text.id))
        subtitle = document.add('subtitulo', conteudo='Detalhes')
        image = document.add('imagem', imagem_id='imagem_001')
        self.assertIn('imagem_001', document.used_image_ids())
        self.assertTrue(document.delete(image.id))
        self.assertIn('imagem_001', document.available_image_ids(['imagem_001']))
        document.add('imagem', index=2, imagem_id='imagem_001')
        self.assertEqual(document.blocks[2].imagem_id, 'imagem_001')
        self.assertTrue(document.move_to_start(subtitle.id))
        self.assertEqual(document.blocks[0].id, subtitle.id)
        self.assertTrue(document.undo())
        self.assertNotEqual(document.blocks[0].id, subtitle.id)

    def test_drag_operations_use_the_same_document(self) -> None:
        images = [self.make_image(1), self.make_image(2)]
        document = CompositionDocument()
        changes: list[int] = []
        builder = CompositionBuilder(document, lambda: images, lambda: changes.append(1))
        builder.dragged_image_id = 'imagem_001'
        builder.drop(1)
        self.assertEqual(document.blocks[1].imagem_id, 'imagem_001')
        dragged = document.blocks[1]
        builder.dragged_block_id = dragged.id
        builder.drop(len(document.blocks))
        self.assertEqual(document.blocks[-1].id, dragged.id)
        self.assertGreaterEqual(len(changes), 2)

    def test_suggestion_requires_confirmation_records_reason_and_is_reversible(self) -> None:
        document = CompositionDocument()
        target = document.blocks[1]
        suggestions = [
            {
                'imagem_id': 'imagem_001',
                'apos_bloco': target.id,
                'confianca': 0.91,
                'motivo': 'A imagem corresponde ao trecho.',
            },
            {
                'imagem_id': 'imagem_002',
                'apos_bloco': target.id,
                'confianca': 0.3,
                'motivo': 'Vínculo incerto.',
            },
        ]
        with self.assertRaises(ValueError):
            document.apply_suggestions(suggestions, confirmed=False)
        applied = document.apply_suggestions(suggestions, confirmed=True)
        self.assertEqual(applied, ['imagem_001'])
        image_block = next(block for block in document.blocks if block.tipo == 'imagem')
        self.assertEqual(image_block.confianca, 0.91)
        self.assertEqual(image_block.motivo, 'A imagem corresponde ao trecho.')
        self.assertNotIn('imagem_002', document.used_image_ids())
        self.assertTrue(document.move_to_start(image_block.id))
        self.assertEqual(document.blocks[0].id, image_block.id)
        self.assertTrue(document.undo())
        self.assertNotEqual(document.blocks[0].id, image_block.id)

    def test_session_round_trip_preserves_composition(self) -> None:
        document = CompositionDocument(source='pgc')
        document.update(document.blocks[0].id, conteudo='Título importado')
        document.update(document.blocks[1].id, conteudo='Texto preservado')
        document.add('imagem', imagem_id='imagem_001', legenda='Legenda')
        restored = CompositionDocument.from_dict(document.to_dict())
        self.assertEqual(restored.source, 'pgc')
        self.assertEqual(restored.to_dict(), document.to_dict())

    def test_text_revision_preserves_image_anchor_and_can_be_undone(self) -> None:
        document = CompositionDocument()
        document.update(document.blocks[0].id, conteudo='Old title')
        document.update(document.blocks[1].id, conteudo='Old text')
        image = document.add('imagem', index=1, imagem_id='imagem_001')
        before = document.to_dict()
        revised = [
            ContentBlock(tipo='titulo', conteudo='New title'),
            ContentBlock(tipo='subtitulo', conteudo='Details'),
            ContentBlock(tipo='texto', conteudo='New text'),
        ]
        self.assertTrue(document.apply_text_revision(revised))
        self.assertIn(image.id, [block.id for block in document.blocks])
        self.assertEqual(document.title, 'New title')
        self.assertTrue(document.undo())
        self.assertEqual(document.to_dict(), before)

    def test_side_by_side_suggestion_is_confirmed_and_reversible(self) -> None:
        document = CompositionDocument()
        target = document.blocks[-1]
        applied = document.apply_suggestions([{
            'imagem_ids': ['imagem_001', 'imagem_002'],
            'apos_bloco': target.id,
            'confianca': 0.94,
            'motivo': 'Related screens',
            'modo_exibicao': 'lado_a_lado',
            'legenda': 'Before and after',
        }], confirmed=True)
        self.assertEqual(applied, ['imagem_001', 'imagem_002'])
        block = document.blocks[-1]
        self.assertEqual(block.image_ids, ['imagem_001', 'imagem_002'])
        self.assertEqual(block.modo_exibicao, 'lado_a_lado')
        self.assertTrue(document.undo())
        self.assertFalse(document.used_image_ids())


if __name__ == '__main__':
    unittest.main()
