from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from core.config import TEXT_MODELS

from models.novelty_plan import (
    MarkingInstruction,
    NoveltyPlan,
    OpenAINoveltyPlan,
    PlannedBlock,
    PlannedImage,
)
from services.image_service import DATA_ROOT, SessionWorkspace, build_composition_exports
from services.composition_ai_service import (
    review_composition_text,
    suggest_composition_positions,
)
from services.novelty_generation_service import generate_automatic_novelty
from services.providers.base_provider import AIProvider
from services.providers.exceptions import ProviderError
from services.providers.gemini_provider import GeminiProvider
from services.providers.openai_provider import OpenAIProvider


class FakeProvider(AIProvider):
    def __init__(self, plan: NoveltyPlan, fail_ids: set[str] | None = None) -> None:
        self.plan = plan
        self.fail_ids = fail_ids or set()
        self.treated: list[str] = []
        self.received_text = ''
        self.received_images: list[str] = []
        self.plan_calls = 0

    def treat_image(self, *, api_key, model, prompt, source, destination):
        image_id = destination.parent.name
        if image_id in self.fail_ids:
            raise ProviderError('Falha simulada.', code='simulated')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        self.treated.append(image_id)
        return destination

    def suggest_orientation(self, *, api_key, model, source):
        return ''

    def generate_novelty_plan(
        self, *, api_key, model, raw_text, images, prompt
    ) -> NoveltyPlan:
        self.plan_calls += 1
        self.received_text = raw_text
        self.received_images = [image_id for image_id, _ in images]
        return self.plan


class AutomaticWorkflowTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='automatic_workflow_', dir=DATA_ROOT))
        self.workspace = SessionWorkspace(
            session_id='automatic-session',
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

    def image(self, position: int) -> dict[str, object]:
        image_id = f'imagem_{position:03d}'
        original = self.workspace.original_dir / f'{image_id}.png'
        treated = self.workspace.processed_dir / image_id / 'imagem_tratada.png'
        Image.new('RGB', (120, 80), (30 * position, 50, 100)).save(original, 'PNG')
        return {
            'id': image_id,
            'filename': f'tela_{position}.png',
            'original': original,
            'treated': treated,
            'annotated': None,
            'final': None,
            'size': (120, 80),
            'status': 'Pronta',
            'error': '',
        }

    async def run_sync(self, function, **kwargs):
        return function(**kwargs)

    async def test_one_image_short_text_with_automatic_marking(self) -> None:
        images = [self.image(1)]
        plan = NoveltyPlan(
            blocos=[
                PlannedBlock(tipo='titulo', conteudo='Novo controle de cashback'),
                PlannedBlock(tipo='texto', conteudo='Agora o controle está mais claro.'),
                PlannedBlock(tipo='imagem', imagem_ids=['imagem_001'], legenda='Tela do controle'),
            ],
            imagens=[PlannedImage(
                imagem_id='imagem_001',
                marcacoes=[MarkingInstruction(
                    tipo='seta', alvo='Campo Tipo de Comprovante',
                    x=0.65, y=0.4, x2=0.85, y2=0.2, texto='Selecione Não Fiscal',
                )],
            )],
        )
        provider = FakeProvider(plan)
        document = await generate_automatic_novelty(
            provider,
            api_key='memory-only', text_model='fake', image_model='fake-image', raw_text='cashback novo controle',
            images=images, workspace=self.workspace,
            anonymization_prompt='anonymous', novelty_prompt='organize',
            run_sync=self.run_sync,
        )
        self.assertEqual(document.title, 'Novo controle de cashback')
        self.assertEqual([block.tipo for block in document.blocks], ['titulo', 'texto', 'imagem'])
        self.assertEqual(len(images[0]['editor'].document.items), 1)
        self.assertEqual(images[0]['editor'].document.items[0].kind, 'arrow')
        self.assertTrue(images[0]['final'].is_file())

    async def test_five_images_disorganized_text_positions_and_no_mark_when_clear(self) -> None:
        images = [self.image(position) for position in range(1, 6)]
        raw = (
            'menu fiscal > documentos. tem CFOP e CST. repetido repetido. '
            'nova tela cadastro classificação tributária'
        )
        plan = NoveltyPlan(
            blocos=[
                PlannedBlock(tipo='titulo', conteudo='Configuração tributária aprimorada'),
                PlannedBlock(tipo='texto', conteudo='A novidade organiza o cadastro fiscal.'),
                PlannedBlock(tipo='subtitulo', conteudo='Caminho de acesso'),
                PlannedBlock(tipo='texto', conteudo='Acesse Fiscal > Documentos.'),
                PlannedBlock(tipo='imagem', imagem_ids=['imagem_001']),
                PlannedBlock(tipo='subtitulo', conteudo='Classificação Tributária'),
                PlannedBlock(tipo='texto', conteudo='Informe CFOP e CST conforme a operação.'),
                PlannedBlock(
                    tipo='imagem', imagem_ids=['imagem_002', 'imagem_003'],
                    modo_exibicao='lado_a_lado', legenda='Configuração e resultado',
                ),
                PlannedBlock(tipo='imagem', imagem_ids=['imagem_004']),
                PlannedBlock(tipo='texto', conteudo='A configuração torna o processo mais claro.'),
                PlannedBlock(tipo='imagem', imagem_ids=['imagem_005']),
            ],
            imagens=[
                PlannedImage(imagem_id='imagem_001', marcacoes=[]),
                PlannedImage(
                    imagem_id='imagem_002',
                    marcacoes=[MarkingInstruction(
                        tipo='caixa', alvo='CFOP', x=0.2, y=0.3, x2=0.5, y2=0.5
                    )],
                ),
            ],
        )
        provider = FakeProvider(plan)
        document = await generate_automatic_novelty(
            provider,
            api_key='memory-only', text_model='fake', image_model='fake-image', raw_text=raw,
            images=images, workspace=self.workspace,
            anonymization_prompt='anonymous', novelty_prompt='organize',
            run_sync=self.run_sync,
        )
        self.assertEqual(provider.received_text, raw)
        self.assertEqual(provider.received_images, [item['id'] for item in images])
        self.assertEqual(len(images[0]['editor'].document.items), 0)
        self.assertEqual(len(images[1]['editor'].document.items), 1)
        image_blocks = [block for block in document.blocks if block.tipo == 'imagem']
        self.assertEqual(image_blocks[0].imagem_id, 'imagem_001')
        self.assertEqual(image_blocks[1].modo_exibicao, 'lado_a_lado')
        self.assertEqual(image_blocks[1].image_ids, ['imagem_002', 'imagem_003'])
        payload = document.to_dict()
        payload['texto_novidade'] = document.text
        exports = build_composition_exports(
            self.workspace,
            [{**item, 'annotations': item['editor'].document.items} for item in images],
            payload,
            'anonymous',
        )
        self.assertTrue(exports['zip'].is_file())
        exported = __import__('json').loads(exports['json'].read_text(encoding='utf-8'))
        self.assertEqual(
            [block['id'] for block in exported['blocos']],
            [block['id'] for block in payload['blocos']],
        )

    async def test_failure_in_one_image_does_not_stop_remaining_images(self) -> None:
        images = [self.image(position) for position in range(1, 4)]
        plan = NoveltyPlan(blocos=[
            PlannedBlock(tipo='titulo', conteudo='Resultado parcial'),
            PlannedBlock(tipo='texto', conteudo='As imagens disponíveis foram organizadas.'),
            PlannedBlock(tipo='imagem', imagem_ids=['imagem_001']),
            PlannedBlock(tipo='imagem', imagem_ids=['imagem_003']),
        ])
        provider = FakeProvider(plan, fail_ids={'imagem_002'})
        document = await generate_automatic_novelty(
            provider,
            api_key='memory-only', text_model='fake', image_model='fake-image', raw_text='texto', images=images,
            workspace=self.workspace, anonymization_prompt='anonymous',
            novelty_prompt='organize', run_sync=self.run_sync,
        )
        self.assertEqual(images[1]['status'], 'Erro')
        self.assertEqual(provider.received_images, ['imagem_001', 'imagem_003'])
        self.assertEqual(len([b for b in document.blocks if b.tipo == 'imagem']), 2)

    async def test_text_only_generation_uses_the_same_block_document(self) -> None:
        plan = NoveltyPlan(blocos=[
            PlannedBlock(tipo='titulo', conteudo='Text only'),
            PlannedBlock(tipo='texto', conteudo='Complete publication text.'),
        ])
        provider = FakeProvider(plan)
        document = await generate_automatic_novelty(
            provider,
            api_key='memory-only', text_model='fake', image_model='fake-image', raw_text='draft', images=[],
            workspace=self.workspace, anonymization_prompt='anonymous',
            novelty_prompt='organize', run_sync=self.run_sync,
        )
        self.assertEqual(document.title, 'Text only')
        self.assertEqual(provider.treated, [])
        self.assertEqual(provider.received_images, [])

    async def test_missing_image_uses_local_upload_order_fallback_without_api_retry(self) -> None:
        images = [self.image(position) for position in range(1, 6)]
        plan = NoveltyPlan(blocos=[
            PlannedBlock(tipo='titulo', conteudo='Automatic publication'),
            PlannedBlock(tipo='texto', conteudo='Professional organized text.'),
            PlannedBlock(tipo='imagem', imagem_ids=['imagem_001']),
            PlannedBlock(tipo='imagem', imagem_ids=['imagem_003']),
            PlannedBlock(tipo='imagem', imagem_ids=['imagem_005']),
        ])
        provider = FakeProvider(plan)
        stages: list[str] = []
        document = await generate_automatic_novelty(
            provider,
            api_key='memory-only', text_model='fake', image_model='fake-image', raw_text='disorganized text',
            images=images, workspace=self.workspace,
            anonymization_prompt='anonymous', novelty_prompt='organize',
            run_sync=self.run_sync,
            on_progress=lambda stage, position, total: stages.append(stage),
        )
        self.assertEqual(provider.plan_calls, 1)
        self.assertEqual(document.fallback_image_ids, ['imagem_002', 'imagem_004'])
        self.assertEqual(
            [block.imagem_id for block in document.blocks if block.tipo == 'imagem'],
            ['imagem_001', 'imagem_003', 'imagem_005', 'imagem_002', 'imagem_004'],
        )
        self.assertEqual(
            document.to_dict()['imagens_fallback'], ['imagem_002', 'imagem_004']
        )
        for expected in (
            'organizando_texto', 'criando_titulo_subtitulos',
            'anonimizando_imagens', 'analisando_imagens', 'criando_marcacoes',
            'posicionando_imagens', 'montando_publicacao',
        ):
            self.assertIn(expected, stages)

    async def test_review_and_positioning_are_independent_actions(self) -> None:
        document = __import__(
            'models.composition', fromlist=['CompositionDocument']
        ).CompositionDocument()
        target = document.blocks[-1]
        review_provider = FakeProvider(NoveltyPlan(blocos=[
            PlannedBlock(tipo='titulo', conteudo='Reviewed title'),
            PlannedBlock(tipo='texto', conteudo='Reviewed text'),
        ]))
        revised = await review_composition_text(
            review_provider, api_key='memory-only', model='fake',
            document=document, run_sync=self.run_sync,
        )
        self.assertTrue(document.apply_text_revision(revised))
        self.assertEqual(document.title, 'Reviewed title')

        images = [self.image(1), self.image(2)]
        placement_provider = FakeProvider(NoveltyPlan(blocos=[
            PlannedBlock(
                tipo='imagem', imagem_ids=['imagem_001'], apos_bloco=target.id,
                confianca=0.91, motivo='Matches this paragraph.',
            ),
            PlannedBlock(
                tipo='imagem', imagem_ids=['imagem_002'], apos_bloco=target.id,
                confianca=0.2, motivo='Unclear relationship.',
            ),
        ]))
        # Use the current text block id because applying a revision creates new blocks.
        current_target = next(block for block in document.blocks if block.tipo == 'texto')
        placement_provider.plan.blocos[0].apos_bloco = current_target.id
        placement_provider.plan.blocos[1].apos_bloco = current_target.id
        suggestions = await suggest_composition_positions(
            placement_provider, api_key='memory-only', model='fake',
            document=document, images=images, run_sync=self.run_sync,
        )
        applied = document.apply_suggestions(suggestions, confirmed=True)
        self.assertEqual(applied, ['imagem_001'])
        self.assertNotIn('imagem_002', document.used_image_ids())


class StructuredProviderContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='provider_contract_', dir=DATA_ROOT))
        self.image_path = self.root / 'screen.png'
        Image.new('RGB', (16, 12), (20, 40, 60)).save(self.image_path, 'PNG')
        self.plan = NoveltyPlan(blocos=[
            PlannedBlock(tipo='titulo', conteudo='Título'),
            PlannedBlock(tipo='texto', conteudo='Texto'),
            PlannedBlock(tipo='imagem', imagem_ids=['imagem_001']),
        ])

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    @patch('services.providers.openai_provider.OpenAI')
    def test_openai_uses_responses_parse_with_text_and_image(self, openai_class) -> None:
        client = openai_class.return_value
        client.responses.parse.return_value = SimpleNamespace(output_parsed=self.plan)
        result = OpenAIProvider().generate_novelty_plan(
            api_key='test-key', model=TEXT_MODELS['openai'], raw_text='texto bruto',
            images=[('imagem_001', self.image_path)], prompt='organize',
        )
        self.assertEqual(result, self.plan)
        arguments = client.responses.parse.call_args.kwargs
        self.assertIs(arguments['text_format'], OpenAINoveltyPlan)
        content = arguments['input'][0]['content']
        self.assertTrue(any(item.get('type') == 'input_image' for item in content))
        self.assertTrue(any('texto bruto' in item.get('text', '') for item in content))

    @patch('services.providers.gemini_provider.genai.Client')
    def test_gemini_uses_json_schema_with_text_and_image(self, client_class) -> None:
        client = client_class.return_value
        client.models.generate_content.return_value = SimpleNamespace(
            parsed=self.plan.model_dump(), text=''
        )
        result = GeminiProvider().generate_novelty_plan(
            api_key='ascii-test-key', model=TEXT_MODELS['gemini'],
            raw_text='texto bruto', images=[('imagem_001', self.image_path)],
            prompt='organize',
        )
        self.assertEqual(result, self.plan)
        arguments = client.models.generate_content.call_args.kwargs
        self.assertEqual(arguments['config'].response_mime_type, 'application/json')
        self.assertIsNotNone(arguments['config'].response_json_schema)
        self.assertTrue(any(getattr(item, 'inline_data', None) for item in arguments['contents']))


if __name__ == '__main__':
    unittest.main()
