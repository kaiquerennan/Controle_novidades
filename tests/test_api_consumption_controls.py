from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from core.config import IMAGE_MODELS, TEXT_MODELS
from models.novelty_plan import (
    AnalyzedImage,
    FidelityReview,
    NoveltyPlan,
    OpenAINoveltyPlan,
    PlannedBlock,
    PlannedImage,
)
from services.api_usage_service import GenerationApiAudit
from services.editorial_orchestration_service import EditorialOrchestrator
from services.generation_control_service import SessionGenerationRegistry
from services.image_analysis_cache_service import ImageAnalysisCache
from services.image_service import DATA_ROOT, SessionWorkspace
from services.providers.base_provider import AIProvider
from services.providers.openai_provider import OpenAIProvider


class TrackingProvider(AIProvider):
    name = 'openai'

    def __init__(
        self,
        plans: list[NoveltyPlan],
        reviews: list[FidelityReview] | None = None,
    ) -> None:
        self.plans = list(plans)
        self.reviews = list(reviews or [FidelityReview(aprovada=True)])
        self.plan_kwargs: list[dict] = []
        self.review_kwargs: list[dict] = []

    def treat_image(self, **kwargs):
        raise AssertionError('Fluxo editorial não deve editar a captura.')

    def suggest_orientation(self, **kwargs):
        return ''

    def generate_novelty_plan(self, **kwargs):
        self.plan_kwargs.append(kwargs)
        return self.plans[min(len(self.plan_kwargs) - 1, len(self.plans) - 1)]

    def review_novelty(self, **kwargs):
        self.review_kwargs.append(kwargs)
        return self.reviews[min(len(self.review_kwargs) - 1, len(self.reviews) - 1)]

    def generate_cover(self, **kwargs):
        destination = Path(kwargs['destination'])
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (64, 40), (20, 30, 40)).save(destination, 'PNG')
        return destination


def initial_plan(image_id: str) -> NoveltyPlan:
    return OpenAINoveltyPlan(
        blocos=[
            PlannedBlock(tipo='titulo', conteudo='Título auditável'),
            PlannedBlock(
                tipo='texto',
                conteudo='O campo CFOP está disponível.',
                papel='introducao',
                secao_id='secao_fiscal',
            ),
            PlannedBlock(tipo='imagem', imagem_ids=[image_id], secao_id='secao_imagem'),
        ],
        imagens=[
            AnalyzedImage(
                imagem_id=image_id,
                descricao='Tela fiscal com o campo CFOP.',
                texto_visivel=['CFOP'],
                elementos=['campo'],
            )
        ],
    )


class ConsumptionOrchestrationTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='consumption_controls_', dir=DATA_ROOT))
        self.workspace = SessionWorkspace(
            session_id='consumption-session',
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
        self.image_path = self.workspace.original_dir / 'tela.png'
        Image.new('RGB', (180, 120), (30, 60, 90)).save(self.image_path, 'PNG')
        self.image = {
            'id': 'imagem_001',
            'filename': 'tela.png',
            'original': self.image_path,
            'treated': self.image_path,
            'final': None,
            'annotated': None,
            'status': 'Pronta',
            'error': '',
        }
        self.cache_root = self.root / 'analysis_cache'

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    async def run_sync(self, function, **kwargs):
        return function(**kwargs)

    async def generate(self, orchestrator, provider):
        return await orchestrator.generate(
            provider,
            api_key='memory-only',
            text_model=TEXT_MODELS['openai'],
            image_model=IMAGE_MODELS['openai'],
            raw_text='O campo CFOP está disponível na rotina Fiscal.',
            images=[self.image],
            workspace=self.workspace,
            cover_requested=False,
            run_sync=self.run_sync,
            generation_id='generation-test',
            idempotency_key='idempotency-test',
        )

    async def test_image_is_sent_once_then_replaced_by_cached_description(self) -> None:
        first = EditorialOrchestrator()
        first._image_cache = ImageAnalysisCache(self.cache_root)
        first_provider = TrackingProvider([initial_plan('imagem_001')])
        await self.generate(first, first_provider)
        self.assertEqual(
            [image_id for image_id, _ in first_provider.plan_kwargs[0]['images']],
            ['imagem_001'],
        )
        self.assertEqual(
            first_provider.review_kwargs[0]['image_descriptions'][0]['descricao'],
            'Tela fiscal com o campo CFOP.',
        )

        second = EditorialOrchestrator()
        second._image_cache = ImageAnalysisCache(self.cache_root)
        second_provider = TrackingProvider([initial_plan('imagem_001')])
        await self.generate(second, second_provider)
        self.assertEqual(second_provider.plan_kwargs[0]['images'], [])
        self.assertIn(
            'DESCRIÇÕES DE IMAGENS JÁ ANALISADAS',
            second_provider.plan_kwargs[0]['raw_text'],
        )

    async def test_generation_logs_stage_and_total_consumption_summary(self) -> None:
        orchestrator = EditorialOrchestrator()
        orchestrator._image_cache = ImageAnalysisCache(self.cache_root)
        provider = TrackingProvider([initial_plan('imagem_001')])
        with self.assertLogs('EMC Novidade Visual IA', level='INFO') as captured:
            await self.generate(orchestrator, provider)
        output = '\n'.join(captured.output)
        self.assertIn('"event":"api_call"', output)
        self.assertIn('"stage":"organizacao"', output)
        self.assertIn('"stage":"revisao"', output)
        self.assertIn('"stage":"preparacao"', output)
        self.assertIn('"event":"generation_summary"', output)
        self.assertIn('Custo aproximado:', output)

    async def test_correction_uses_only_targets_and_never_resends_image(self) -> None:
        corrected = NoveltyPlan(blocos=[
            PlannedBlock(
                tipo='texto',
                conteudo='O campo CFOP permanece disponível.',
                secao_id='secao_fiscal',
            )
        ])
        provider = TrackingProvider(
            [initial_plan('imagem_001'), corrected],
            [
                FidelityReview(
                    aprovada=False,
                    correcoes_necessarias=['Ajustar a descrição do campo CFOP.'],
                    secoes_para_corrigir=['secao_fiscal'],
                ),
                FidelityReview(aprovada=True),
            ],
        )
        orchestrator = EditorialOrchestrator()
        orchestrator._image_cache = ImageAnalysisCache(self.cache_root)
        await self.generate(orchestrator, provider)

        self.assertEqual(len(provider.plan_kwargs), 2)
        correction = provider.plan_kwargs[1]
        self.assertEqual(correction['images'], [])
        self.assertNotIn('TEXTO ORIGINAL INTEGRAL', correction['raw_text'])
        self.assertNotIn('DOCUMENTO ATUAL', correction['prompt'])
        self.assertLess(len(correction['prompt']), 600)
        self.assertIn('blocos_alvo', correction['raw_text'])
        self.assertEqual(len(provider.review_kwargs), 2)
        self.assertTrue(provider.review_kwargs[1]['image_descriptions'])


class ProviderCompatibilityTestCase(unittest.TestCase):
    def test_enriched_image_schema_is_openai_only(self) -> None:
        gemini_schema = str(NoveltyPlan.model_json_schema())
        openai_schema = str(OpenAINoveltyPlan.model_json_schema())
        self.assertNotIn('descricao', gemini_schema)
        self.assertIn('descricao', openai_schema)
        self.assertIn('texto_visivel', openai_schema)


class ApiAuditTestCase(unittest.TestCase):
    def test_usage_attempts_request_id_cost_and_safe_logs(self) -> None:
        provider = OpenAIProvider()
        client = MagicMock()
        plan = NoveltyPlan(blocos=[
            PlannedBlock(tipo='titulo', conteudo='Título'),
            PlannedBlock(tipo='texto', conteudo='Texto'),
        ])
        response = SimpleNamespace(
            output_parsed=plan,
            _request_id='req_audit_123',
            usage=SimpleNamespace(
                input_tokens=1000,
                output_tokens=200,
                input_tokens_details=SimpleNamespace(cached_tokens=100),
                output_tokens_details=SimpleNamespace(reasoning_tokens=40),
            ),
        )
        client.responses.parse.return_value = response
        audit = GenerationApiAudit('generation-audit', 'idempotency-audit')
        audit.start_call(1, 'organizacao', '/v1/responses', 'gpt-5-mini')
        audit.record_attempt(1, '/v1/responses', 1)
        audit.record_attempt(1, '/v1/responses', 2)
        provider.set_api_audit_context(audit, 1)

        with self.assertLogs('EMC Novidade Visual IA', level='INFO') as captured:
            with patch.object(provider, '_client', return_value=client):
                provider.generate_novelty_plan(
                    api_key='test-secret-api-key',
                    model='gpt-5-mini',
                    raw_text='texto reservado',
                    images=[],
                    prompt='PROMPT-NAO-DEVE-APARECER',
                )
            provider.clear_api_audit_context()
            audit.finish_call(1)
            summary = audit.log_summary()

        self.assertEqual(summary['http_attempts'], 2)
        self.assertEqual(summary['input_tokens'], 1000)
        self.assertEqual(summary['cached_tokens'], 100)
        self.assertEqual(summary['output_tokens'], 200)
        self.assertEqual(summary['reasoning_tokens'], 40)
        self.assertGreater(summary['estimated_cost_usd'], 0)
        output = '\n'.join(captured.output)
        self.assertIn('req_audit_123', output)
        self.assertIn('Tentativas HTTP: 2', output)
        self.assertNotIn('test-secret-api-key', output)
        self.assertNotIn('PROMPT-NAO-DEVE-APARECER', output)
        self.assertNotIn('texto reservado', output)


class GenerationRegistryTestCase(unittest.TestCase):
    def test_only_one_generation_can_be_active_per_session(self) -> None:
        registry = SessionGenerationRegistry()
        first_lock = registry.lock_for('session-1')
        self.assertIs(first_lock, registry.lock_for('session-1'))
        self.assertTrue(registry.try_begin('session-1', 'generation-1', 'same-input'))
        self.assertFalse(registry.try_begin('session-1', 'generation-2', 'same-input'))
        self.assertFalse(registry.try_begin('session-1', 'generation-3', 'other-input'))
        registry.finish('session-1', 'generation-2')
        self.assertTrue(registry.is_active('session-1'))
        registry.finish('session-1', 'generation-1')
        self.assertFalse(registry.is_active('session-1'))
        self.assertTrue(registry.try_begin('session-1', 'generation-4', 'same-input'))


class UiConcurrencyContractTestCase(unittest.TestCase):
    def test_ui_has_session_lock_idempotency_and_immediate_button_control(self) -> None:
        source = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(
            encoding='utf-8'
        )
        self.assertIn("'generation_in_progress': False", source)
        self.assertIn('GENERATION_REGISTRY.lock_for', source)
        self.assertIn('GENERATION_REGISTRY.try_begin', source)
        self.assertIn('idempotency_key = generation_fingerprint()', source)
        self.assertIn("safe_visual_update('desabilitar_botao_gerar', generation_button.disable)", source)
        self.assertIn("safe_visual_update('desabilitar_botao_tentar_novamente', retry_button.disable)", source)
        self.assertIn("safe_visual_update('habilitar_botao_gerar', generation_button.enable)", source)
        self.assertIn("safe_visual_update('habilitar_botao_tentar_novamente', retry_button.enable)", source)
        self.assertIn('async with generation_lock:', source)
        provider_source = Path(__file__).resolve().parents[1].joinpath(
            'services', 'providers', 'openai_provider.py'
        ).read_text(encoding='utf-8')
        self.assertIn('max_retries=1', provider_source)
        self.assertIn('max_output_tokens=max_output_tokens or 12000', provider_source)
        self.assertIn('max_output_tokens=max_output_tokens or 6500', provider_source)


if __name__ == '__main__':
    unittest.main()
