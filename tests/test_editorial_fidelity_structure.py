from __future__ import annotations

import unittest

from models.novelty_plan import FidelityReview, NoveltyPlan, PlannedBlock, StructuredListItem
from services.editorial_orchestration_service import document_from_editorial_plan
from services.editorial_structure_service import extract_editorial_units
from services.text_sanitizer import strip_markdown


class EditorialStructureTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = (
            'Pedidos\nAcesse: Fiscal > Pedidos\nCampos: CFOP, Série\n'
            '- **Automático**\n- Manual\n\n'
            'Clientes\nAcesse: Fiscal > Clientes\nBotões: Salvar\n'
            'Parâmetros: Validar limite\nExemplo: Cliente XPTO'
        )

    def test_routines_paths_and_functional_inventory_stay_separate(self) -> None:
        units = extract_editorial_units(self.raw)
        self.assertEqual([unit.caminho for unit in units], [
            'Fiscal / Pedidos', 'Fiscal / Clientes',
        ])
        plan = NoveltyPlan(blocos=[
            PlannedBlock(tipo='titulo', conteudo='Controles fiscais'),
            PlannedBlock(
                tipo='texto', papel='introducao', secao_id='rotina_001',
                conteudo='Os controles foram organizados.',
            ),
            PlannedBlock(
                tipo='subtitulo', secao_id='rotina_001', rotina='Pedidos',
                conteudo='Pedidos',
            ),
            PlannedBlock(
                tipo='subtitulo', secao_id='rotina_002', rotina='Clientes',
                conteudo='Clientes',
            ),
        ])
        document = document_from_editorial_plan(plan, [], units)
        paths = [block for block in document.blocks if block.papel == 'caminho']
        self.assertEqual([(block.secao_id, block.caminho) for block in paths], [
            ('rotina_001', 'Fiscal / Pedidos'),
            ('rotina_002', 'Fiscal / Clientes'),
        ])
        first = [block for block in document.blocks if block.secao_id == 'rotina_001']
        second = [block for block in document.blocks if block.secao_id == 'rotina_002']
        first_text = document.__class__(blocks=first).text
        second_text = document.__class__(blocks=second).text
        for value in ('CFOP', 'Série', 'Automático', 'Manual'):
            self.assertIn(value, first_text)
            self.assertNotIn(value, second_text)
        for value in ('Salvar', 'Validar limite', 'Cliente XPTO'):
            self.assertIn(value, second_text)
            self.assertNotIn(value, first_text)

    def test_two_line_system_path_stays_with_its_routine(self) -> None:
        units = extract_editorial_units(
            'Pedidos\nAcesse através do caminho:\nFiscal / Cadastros / Pedidos\nCampos: CFOP'
        )
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].nome, 'Pedidos')
        self.assertEqual(units[0].caminho, 'Fiscal / Cadastros / Pedidos')
        self.assertEqual(units[0].campos, ['CFOP'])
    def test_lists_and_highlights_are_semantic(self) -> None:
        plan = NoveltyPlan(blocos=[
            PlannedBlock(tipo='titulo', conteudo='Título'),
            PlannedBlock(tipo='texto', papel='introducao', conteudo='Introdução'),
            PlannedBlock(tipo='lista', secao_id='rotina_001', itens=[
                StructuredListItem(texto='Automático', destaques=['Automático']),
                StructuredListItem(texto='Manual'),
            ]),
        ])
        document = document_from_editorial_plan(plan, [])
        list_block = next(block for block in document.blocks if block.tipo == 'lista')
        self.assertEqual(list_block.itens[0]['texto'], 'Automático')
        self.assertEqual(list_block.itens[0]['destaques'], ['Automático'])
        self.assertNotIn('**', str(document.to_dict()))

    def test_sanitizer_keeps_technical_characters_and_list_markers(self) -> None:
        value = strip_markdown('# Título\n- Código A*\nParâmetro #1')
        self.assertEqual(value, 'Título\n- Código A*\nParâmetro #1')


if __name__ == '__main__':
    unittest.main()