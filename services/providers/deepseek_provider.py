from __future__ import annotations

import json
import logging
from pathlib import Path

from openai import OpenAI

from core.config import DEEPSEEK_BASE_URL
from models.novelty_plan import FidelityReview, NoveltyPlan
from services.model_routing import validate_text_model
from services.providers.base_provider import AIProvider
from services.providers.exceptions import ProviderError, map_provider_exception


logger = logging.getLogger('EMC Novidade Visual IA')

# A DeepSeek e compativel com a API da OpenAI, mas apenas em Chat Completions:
# nao ha Responses API nem structured outputs com json_schema. O esquema vai no
# proprio prompt e a resposta e validada aqui.
SCHEMA_INSTRUCTIONS = (
    'Responda exclusivamente com um objeto JSON valido, sem texto antes ou depois '
    'e sem cercas de codigo. O objeto deve seguir exatamente este JSON Schema:\n\n'
    '{schema}\n\n'
    'Nao acrescente campos fora do esquema.'
)

# Sem structured outputs estrito, o modelo cumpre o esquema mas ignora a estrutura
# esperada da materia. Estas exigencias vao explicitas para nao voltar um plano
# sem titulo, que o fluxo editorial recusa.
PLAN_STRUCTURE_REQUIREMENTS = (
    '\n\nEXIGENCIAS DE ESTRUTURA, obrigatorias:\n'
    '- O primeiro item de "blocos" precisa ter "tipo": "titulo", com o titulo da '
    'materia em "conteudo".\n'
    '- Depois do titulo, inclua ao menos um item com "tipo": "texto" contendo o '
    'primeiro paragrafo, e marque esse item com "papel": "introducao".\n'
    '- Use "tipo": "texto" para os demais paragrafos e "tipo": "lista" apenas '
    'para enumeracoes, preenchendo "itens" com objetos {"texto": "..."}.\n'
    '- Nao devolva um plano apenas com um bloco de texto.'
)

IMAGE_UNSUPPORTED = (
    'O provedor DeepSeek nao processa nem gera imagens. Use Gemini ou OpenAI '
    'para operacoes de imagem.'
)


class DeepSeekProvider(AIProvider):
    name = 'deepseek'

    def _client(self, api_key: str, timeout: float) -> OpenAI:
        if not str(api_key or '').strip():
            raise ProviderError(
                'Informe uma chave da API da DeepSeek.', code='missing_api_key',
            )
        return OpenAI(
            api_key=api_key.strip(), base_url=DEEPSEEK_BASE_URL, timeout=timeout,
            max_retries=1,
        )

    def _complete_json(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        payload: str,
        schema: dict,
        max_output_tokens: int | None,
        timeout: float,
        extra_instructions: str = '',
    ) -> str:
        client = self._client(api_key, timeout)
        instrucoes = prompt + '\n\n' + SCHEMA_INSTRUCTIONS.format(
            schema=json.dumps(schema, ensure_ascii=False)
        ) + extra_instructions
        response = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': instrucoes},
                {'role': 'user', 'content': payload},
            ],
            response_format={'type': 'json_object'},
            max_tokens=max_output_tokens or 8000,
            stream=False,
        )
        self._record_usage(response)
        choices = response.choices or []
        conteudo = choices[0].message.content if choices else ''
        if not str(conteudo or '').strip():
            raise ProviderError(
                'A DeepSeek nao retornou conteudo.', code='deepseek_empty_response',
            )
        return str(conteudo)

    def _record_usage(self, response) -> None:
        audit = getattr(self, '_api_audit', None)
        if audit:
            audit.record_response_usage(int(getattr(self, '_api_logical_call', 0) or 0), response)

    def generate_novelty_plan(
        self,
        *,
        api_key: str,
        model: str,
        raw_text: str,
        images: list[tuple[str, Path]],
        prompt: str,
        max_output_tokens: int | None = None,
    ) -> NoveltyPlan:
        model = validate_text_model(self.name, model)
        if images:
            # Sem visao no modelo: as imagens seguem para o fallback local, que as
            # posiciona ao final na ordem de upload. Nenhuma imagem e descartada.
            logger.info(
                '[DEEPSEEK] %s imagem(ns) nao enviada(s): o provedor nao processa imagens; '
                'o posicionamento sera feito pelo fallback local.',
                len(images),
            )
        try:
            conteudo = self._complete_json(
                api_key=api_key,
                model=model,
                prompt=prompt,
                payload=f'TEXTO BRUTO DA NOVIDADE:\n{raw_text}',
                schema=NoveltyPlan.model_json_schema(),
                max_output_tokens=max_output_tokens,
                timeout=180.0,
                extra_instructions=PLAN_STRUCTURE_REQUIREMENTS,
            )
            plano = NoveltyPlan.model_validate_json(conteudo)
            if not any(bloco.tipo == 'titulo' for bloco in plano.blocos):
                raise ProviderError(
                    'A DeepSeek devolveu a materia sem titulo.',
                    code='invalid_schema_response',
                )
            return plano
        except ProviderError:
            raise
        except Exception as exc:
            raise map_provider_exception(self.name, exc, api_key) from exc

    def review_novelty(
        self,
        *,
        api_key: str,
        model: str,
        raw_text: str,
        document: dict,
        image_ids: list[str],
        image_descriptions: list[dict] | None = None,
        prompt: str = '',
        max_output_tokens: int | None = None,
    ) -> FidelityReview:
        model = validate_text_model(self.name, model)
        payload = json.dumps(
            {
                'texto_bruto': raw_text,
                'documento': document,
                'imagens_enviadas': image_ids,
            },
            ensure_ascii=False,
        )
        try:
            conteudo = self._complete_json(
                api_key=api_key,
                model=model,
                prompt=prompt,
                payload=payload,
                schema=FidelityReview.model_json_schema(),
                max_output_tokens=max_output_tokens,
                timeout=180.0,
            )
            return FidelityReview.model_validate_json(conteudo)
        except ProviderError:
            raise
        except Exception as exc:
            raise map_provider_exception(self.name, exc, api_key) from exc

    def treat_image(
        self, *, api_key: str, model: str, prompt: str, source: Path, destination: Path,
    ) -> Path:
        raise ProviderError(IMAGE_UNSUPPORTED, code='deepseek_image_unsupported')

    def anonymize_marked_areas(
        self, *, api_key: str, model: str, source: Path, mask: Path,
        instructions: str, destination: Path,
    ) -> Path:
        raise ProviderError(IMAGE_UNSUPPORTED, code='deepseek_image_unsupported')

    def suggest_orientation(self, *, api_key: str, model: str, source: Path) -> str:
        raise ProviderError(IMAGE_UNSUPPORTED, code='deepseek_image_unsupported')

    def generate_cover(
        self, *, api_key: str, model: str, prompt: str, destination: Path,
    ) -> Path:
        raise ProviderError(
            'O provedor DeepSeek nao gera imagens, entao a capa ilustrativa nao esta '
            'disponivel. Desmarque a capa ou use Gemini ou OpenAI.',
            code='deepseek_cover_unsupported',
        )


__all__ = ['DeepSeekProvider']
