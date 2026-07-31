from __future__ import annotations

from hashlib import sha256
import json
import shutil
import tempfile
import unittest
import unicodedata
import zipfile
from pathlib import Path

from PIL import Image
from core.config import IMAGE_MODELS, TEXT_MODELS

from components.image_editor import ImageEditor
from models.novelty_plan import FidelityReview, NoveltyPlan, PlannedBlock
from services.editorial_orchestration_service import (
    COVER_PROMPT,
    REVIEWER_PROMPT,
    WRITER_ORGANIZER_PROMPT,
    EditorialOrchestrator,
)
from services.image_analysis_cache_service import ImageAnalysisCache
from services.image_service import DATA_ROOT, SessionWorkspace, build_composition_exports
from services.providers.base_provider import AIProvider
from services.providers.exceptions import ProviderError, map_provider_exception


def accentless_lower(value: str) -> str:
    return ''.join(
        character for character in unicodedata.normalize('NFD', value.lower())
        if unicodedata.category(character) != 'Mn'
    )

def valid_plan(image_ids: list[str] | None = None) -> NoveltyPlan:
    blocks = [
        PlannedBlock(tipo='titulo', conteudo='Controle de NFC-e'),
        PlannedBlock(
            tipo='texto', papel='introducao',
            conteudo='O campo CFOP foi mantido na tela Pedidos.',
        ),
        PlannedBlock(tipo='subtitulo', conteudo='Configuracao no modulo Fiscal'),
        PlannedBlock(
            tipo='texto', conteudo='Acesse Fiscal > NFC-e e informe o campo CFOP.'
        ),
    ]
    for image_id in image_ids or []:
        blocks.append(PlannedBlock(tipo='imagem', imagem_ids=[image_id]))
    return NoveltyPlan(blocos=blocks)


class FakeEditorialProvider(AIProvider):
    name = 'openai'

    def __init__(
        self,
        plans: list[NoveltyPlan],
        review: FidelityReview | Exception | None = None,
        *,
        fail_plan: bool = False,
        fail_cover: bool = False,
    ) -> None:
        self.plans = list(plans)
        self.review_result = review or FidelityReview(aprovada=True)
        self.fail_plan = fail_plan
        self.fail_cover = fail_cover
        self.plan_calls = 0
        self.review_calls = 0
        self.cover_calls = 0
        self.treat_calls = 0
        self.received_images: list[list[str]] = []
        self.plan_models: list[str] = []
        self.review_models: list[str] = []
        self.cover_models: list[str] = []

    def treat_image(self, **kwargs):
        self.treat_calls += 1
        raise AssertionError('Print editing must not be called by the editorial flow.')

    def suggest_orientation(self, **kwargs):
        return ''

    def generate_novelty_plan(self, **kwargs):
        self.plan_calls += 1
        self.plan_models.append(kwargs['model'])
        self.received_images.append([value[0] for value in kwargs['images']])
        if self.fail_plan:
            raise ProviderError('simulated writer failure', code='simulated')
        return self.plans[min(self.plan_calls - 1, len(self.plans) - 1)]

    def review_novelty(self, **kwargs):
        self.review_calls += 1
        self.review_models.append(kwargs['model'])
        if isinstance(self.review_result, Exception):
            raise self.review_result
        return self.review_result

    def generate_cover(self, **kwargs):
        self.cover_calls += 1
        self.cover_models.append(kwargs['model'])
        if self.fail_cover:
            raise ProviderError('simulated cover failure', code='simulated_cover')
        destination = Path(kwargs['destination'])
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (320, 180), (20, 50, 90)).save(destination, 'PNG')
        return destination


class EditorialOrchestrationTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='editorial_test_', dir=DATA_ROOT))
        self.workspace = SessionWorkspace(
            session_id='editorial-session',
            original_dir=self.root / 'originals',
            processed_dir=self.root / 'processed',
            export_dir=self.root / 'exports',
            temp_dir=self.root / 'temp',
        )
        for folder in (
            self.workspace.original_dir, self.workspace.processed_dir,
            self.workspace.export_dir, self.workspace.temp_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    async def run_sync(self, function, **kwargs):
        return function(**kwargs)

    def image(self, position: int, *, duplicate_of: Path | None = None) -> dict:
        path = self.workspace.original_dir / f'tela_{position}.png'
        if duplicate_of:
            shutil.copy2(duplicate_of, path)
        else:
            Image.new('RGB', (120, 80), (position * 30, 60, 100)).save(path, 'PNG')
        return {
            'id': f'imagem_{position:03d}',
            'filename': path.name,
            'original': path,
            'treated': path,
            'final': None,
            'annotated': None,
            'status': 'Pronta',
            'error': '',
            'editor': ImageEditor(),
        }

    async def generate(
        self, provider, images=None, cover=False, orchestrator=None,
        raw_text='NFC-e na tela Pedidos com campo CFOP.',
    ):
        active_orchestrator = orchestrator or EditorialOrchestrator()
        if orchestrator is None:
            active_orchestrator._image_cache = ImageAnalysisCache(self.root / 'analysis_cache')
        return await active_orchestrator.generate(
            provider,
            api_key='memory-only',
            text_model=TEXT_MODELS['openai'],
            image_model=IMAGE_MODELS['openai'],
            raw_text=raw_text,
            images=images or [], workspace=self.workspace,
            cover_requested=cover, run_sync=self.run_sync,
        )

    async def test_text_only_generates_title_introduction_and_sections(self) -> None:
        document = await self.generate(FakeEditorialProvider([valid_plan()]))
        self.assertEqual(document.title, 'Controle de NFC-e')
        self.assertIn('CFOP', document.introduction)
        self.assertTrue(any(block.tipo == 'subtitulo' for block in document.blocks))

    async def test_one_image_is_used_without_editing(self) -> None:
        image = self.image(1)
        before = sha256(Path(image['original']).read_bytes()).hexdigest()
        provider = FakeEditorialProvider([valid_plan(['imagem_001'])])
        document = await self.generate(provider, [image])
        self.assertEqual(document.used_image_ids(), {'imagem_001'})
        self.assertEqual(provider.treat_calls, 0)
        self.assertEqual(before, sha256(Path(image['original']).read_bytes()).hexdigest())
        self.assertEqual(image['final'], image['original'])

    async def test_multiple_images_follow_plan_order(self) -> None:
        images = [self.image(i) for i in range(1, 4)]
        plan = valid_plan(['imagem_003', 'imagem_001', 'imagem_002'])
        document = await self.generate(FakeEditorialProvider([plan]), images)
        self.assertEqual(
            [block.imagem_id for block in document.blocks if block.tipo == 'imagem'],
            ['imagem_003', 'imagem_001', 'imagem_002'],
        )

    async def test_unassociated_images_fallback_in_upload_order(self) -> None:
        images = [self.image(i) for i in range(1, 5)]
        document = await self.generate(
            FakeEditorialProvider([valid_plan(['imagem_002'])]), images
        )
        self.assertEqual(
            document.fallback_image_ids,
            ['imagem_001', 'imagem_003', 'imagem_004'],
        )
        self.assertEqual(document.used_image_ids(), {item['id'] for item in images})

    async def test_invalid_external_image_id_is_ignored(self) -> None:
        images = [self.image(1)]
        plan = valid_plan(['imagem_externa'])
        document = await self.generate(FakeEditorialProvider([plan]), images)
        self.assertNotIn('imagem_externa', document.used_image_ids())
        self.assertEqual(document.used_image_ids(), {'imagem_001'})

    async def test_duplicate_file_is_not_resent_and_is_not_discarded(self) -> None:
        first = self.image(1)
        second = self.image(2, duplicate_of=Path(first['original']))
        provider = FakeEditorialProvider([valid_plan(['imagem_001'])])
        document = await self.generate(provider, [first, second])
        self.assertLessEqual(len(provider.received_images[0]), 1)
        self.assertNotIn('imagem_002', provider.received_images[0])
        self.assertEqual(document.used_image_ids(), {'imagem_001', 'imagem_002'})

    async def test_valid_review_approves_document(self) -> None:
        provider = FakeEditorialProvider(
            [valid_plan()], FidelityReview(aprovada=True, avisos=[])
        )
        document = await self.generate(provider)
        self.assertTrue(document.review['aprovada'])
        self.assertEqual(provider.review_calls, 1)

    async def test_review_records_possible_invention(self) -> None:
        review = FidelityReview(
            aprovada=False,
            informacoes_possivelmente_inventadas=['Campo inexistente'],
            avisos=['Revise o campo inexistente.'],
        )
        document = await self.generate(FakeEditorialProvider([valid_plan()], review))
        self.assertEqual(
            document.review['informacoes_possivelmente_inventadas'], ['Campo inexistente']
        )

    async def test_review_records_changed_technical_name(self) -> None:
        review = FidelityReview(
            aprovada=False, nomes_tecnicos_alterados=['CFOP para C.F.O.P.']
        )
        document = await self.generate(FakeEditorialProvider([valid_plan()], review))
        self.assertTrue(document.review['nomes_tecnicos_alterados'])

    async def test_review_records_missing_image(self) -> None:
        review = FidelityReview(
            aprovada=False, imagens_nao_utilizadas=['imagem_001']
        )
        document = await self.generate(FakeEditorialProvider([valid_plan()], review))
        self.assertEqual(document.review['imagens_nao_utilizadas'], ['imagem_001'])

    async def test_only_one_automatic_correction_is_allowed(self) -> None:
        review = FidelityReview(
            aprovada=False, correcoes_necessarias=['Remover afirmacao sem fonte.']
        )
        provider = FakeEditorialProvider([valid_plan(), valid_plan()], review)
        document = await self.generate(provider)
        self.assertEqual(provider.plan_calls, 2)
        self.assertEqual(provider.review_calls, 2)
        self.assertTrue(document.review['correcao_automatica_realizada'])

    async def test_omitted_source_path_forces_one_correction_and_local_preservation(self) -> None:
        provider = FakeEditorialProvider(
            [valid_plan(), valid_plan()], FidelityReview(aprovada=True)
        )
        document = await self.generate(
            provider,
            raw_text='Acesse: Fiscal > Cadastros > NFC-e',
        )
        self.assertEqual(provider.plan_calls, 1)
        self.assertEqual(provider.review_calls, 1)
        path_blocks = [block for block in document.blocks if block.papel == 'caminho']
        self.assertEqual(len(path_blocks), 1)
        self.assertEqual(
            path_blocks[0].conteudo,
            'Acesse através do caminho:\nFiscal / Cadastros / NFC-e',
        )
        self.assertFalse(document.review['correcao_automatica_realizada'])

    async def test_reviewer_failure_keeps_document_with_warning(self) -> None:
        provider = FakeEditorialProvider([valid_plan()], RuntimeError('review unavailable'))
        document = await self.generate(provider)
        self.assertEqual(document.review['status'], 'nao_concluida')
        self.assertTrue(document.review['avisos'])

    async def test_writer_failure_preserves_original_image(self) -> None:
        image = self.image(1)
        before = Path(image['original']).read_bytes()
        provider = FakeEditorialProvider([valid_plan()], fail_plan=True)
        with self.assertRaises(ProviderError) as raised:
            await self.generate(provider, [image])
        self.assertIn('preservados', raised.exception.user_message)
        self.assertEqual(before, Path(image['original']).read_bytes())

    async def test_models_are_routed_by_operation(self) -> None:
        provider = FakeEditorialProvider([valid_plan()])
        await self.generate(provider, cover=True)
        self.assertEqual(provider.plan_models, [TEXT_MODELS['openai']])
        self.assertEqual(provider.review_models, [TEXT_MODELS['openai']])
        self.assertEqual(provider.cover_models, [IMAGE_MODELS['openai']])
        self.assertFalse(TEXT_MODELS['openai'].startswith('gpt-image'))
        self.assertEqual(TEXT_MODELS['openai'], 'gpt-5-mini')
        self.assertEqual(IMAGE_MODELS['openai'], 'gpt-image-1.5')

    async def test_generation_logs_text_and_image_models_separately(self) -> None:
        provider = FakeEditorialProvider([valid_plan()])
        with self.assertLogs('EMC Novidade Visual IA', level='INFO') as captured:
            await self.generate(provider, cover=True)
        output = '\n'.join(captured.output)
        self.assertIn('[MODELOS] Texto: Modelo: gpt-5-mini', output)
        self.assertIn('[MODELOS] Imagem: Modelo: gpt-image-1.5', output)

    async def test_text_model_is_rejected_for_cover(self) -> None:
        provider = FakeEditorialProvider([valid_plan()])
        with self.assertRaises(ProviderError) as raised:
            await EditorialOrchestrator().generate(
                provider, api_key='memory-only',
                text_model=TEXT_MODELS['openai'],
                image_model=TEXT_MODELS['openai'],
                raw_text='NFC-e na tela Pedidos com campo CFOP.', images=[],
                workspace=self.workspace, cover_requested=True, run_sync=self.run_sync,
            )
        self.assertEqual(raised.exception.code, 'text_model_in_image_flow')
        self.assertEqual(provider.cover_calls, 0)

    async def test_unavailable_text_model_has_clear_error(self) -> None:
        provider = FakeEditorialProvider([valid_plan()], fail_plan=True)
        provider.generate_novelty_plan = lambda **kwargs: (_ for _ in ()).throw(
            ProviderError(
                'O modelo informado não existe.', code='openai_model_not_found',
                provider='openai', model=kwargs['model'], status_http=404,
            )
        )
        with self.assertRaises(ProviderError) as raised:
            await self.generate(provider)
        self.assertEqual(raised.exception.code, 'text_model_unavailable')
        self.assertIn(TEXT_MODELS['openai'], raised.exception.user_message)
        self.assertIn('não está disponível', raised.exception.user_message)

    async def test_cover_is_not_called_when_unchecked(self) -> None:
        provider = FakeEditorialProvider([valid_plan()])
        document = await self.generate(provider, cover=False)
        self.assertEqual(provider.cover_calls, 0)
        self.assertEqual(document.cover['status'], 'nao_gerada')

    async def test_simulated_cover_is_separate_from_screenshots(self) -> None:
        image = self.image(1)
        provider = FakeEditorialProvider([valid_plan(['imagem_001'])])
        document = await self.generate(provider, [image], cover=True)
        self.assertEqual(provider.cover_calls, 1)
        self.assertEqual(document.cover['status'], 'gerada')
        self.assertNotEqual(Path(document.cover['arquivo']), Path(image['original']))

    async def test_cover_failure_does_not_interrupt_document(self) -> None:
        provider = FakeEditorialProvider([valid_plan()], fail_cover=True)
        document = await self.generate(provider, cover=True)
        self.assertEqual(document.cover['status'], 'falhou')
        self.assertTrue(document.title)
        self.assertIn('capa', document.cover['mensagem'])

    async def test_cover_removal_keeps_all_blocks(self) -> None:
        document = await self.generate(FakeEditorialProvider([valid_plan()]), cover=True)
        before = [block.to_dict() for block in document.blocks]
        self.assertTrue(document.remove_cover())
        self.assertEqual(before, [block.to_dict() for block in document.blocks])
        self.assertEqual(document.cover['status'], 'removida')

    async def test_identical_request_uses_memory_cache(self) -> None:
        provider = FakeEditorialProvider([valid_plan()])
        orchestrator = EditorialOrchestrator()
        await self.generate(provider, orchestrator=orchestrator)
        await self.generate(provider, orchestrator=orchestrator)
        self.assertEqual(provider.plan_calls, 1)
        self.assertEqual(provider.review_calls, 1)

    async def test_export_contains_cover_review_blocks_and_all_images(self) -> None:
        images = [self.image(1), self.image(2)]
        provider = FakeEditorialProvider([valid_plan(['imagem_001'])])
        document = await self.generate(provider, images, cover=True)
        payload = document.to_dict()
        payload['texto_novidade'] = document.text
        result = build_composition_exports(
            self.workspace,
            [{**item, 'annotations': []} for item in images],
            payload,
            WRITER_ORGANIZER_PROMPT,
        )
        exported = json.loads(Path(result['json']).read_text(encoding='utf-8'))
        self.assertEqual(len(exported['imagens']), 2)
        self.assertEqual(exported['revisao']['aprovada'], True)
        self.assertEqual(exported['capa']['arquivo'], 'capa/capa.png')
        self.assertTrue(exported['introducao'])
        with zipfile.ZipFile(result['zip']) as archive:
            self.assertIn('capa/capa.png', archive.namelist())


class EditorialStaticContractTestCase(unittest.TestCase):
    def test_writer_prompt_preserves_technical_names(self) -> None:
        lowered = accentless_lower(WRITER_ORGANIZER_PROMPT)
        for term in ('telas', 'campos', 'menus', 'botoes', 'codigos', 'siglas'):
            self.assertIn(term, lowered)

    def test_writer_prompt_forbids_unrequested_metadata(self) -> None:
        lowered = WRITER_ORGANIZER_PROMPT.lower()
        for term in ('data', 'hora', 'autor', 'tempo de leitura', 'comercial'):
            self.assertIn(term, lowered)

    def test_organizer_prompt_forbids_omission_and_markings(self) -> None:
        lowered = accentless_lower(WRITER_ORGANIZER_PROMPT)
        self.assertIn('use todas as imagens', lowered)
        self.assertIn('nao crie marcacoes', lowered)
        self.assertIn('nunca altere, substitua ou omita', lowered)

    def test_reviewer_prompt_covers_fidelity_checks(self) -> None:
        lowered = REVIEWER_PROMPT.lower()
        for term in ('invencoes', 'nomes tecnicos', 'imagens omitidas', 'ids invalidos'):
            self.assertIn(term, lowered)

    def test_cover_prompt_forbids_fake_interface(self) -> None:
        lowered = COVER_PROMPT.lower()
        for term in ('nao pode parecer captura', 'nao crie interface', 'dados sensiveis'):
            self.assertIn(term, lowered)

    def test_billing_hard_limit_has_friendly_message(self) -> None:
        class BillingError(Exception):
            status_code = 429
            body = {'error': {'code': 'billing_hard_limit_reached'}}

        mapped = map_provider_exception('openai', BillingError('billing_hard_limit_reached'), 'secret')
        self.assertIn('limite financeiro', mapped.user_message)
        self.assertNotIn('modelo e os parametros', mapped.user_message.lower())

    def test_no_database_views_sql_or_pgc_dependency_was_added(self) -> None:
        root = Path(__file__).resolve().parents[1]
        files = [
            *root.joinpath('services').rglob('*.py'),
            *root.joinpath('models').rglob('*.py'),
            root / 'app.py',
        ]
        source = '\n'.join(path.read_text(encoding='utf-8').lower() for path in files)
        for forbidden in ('import sqlite3', 'sqlalchemy', 'select * from', 'c:\\automacao'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
