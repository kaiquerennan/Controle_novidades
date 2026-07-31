from __future__ import annotations

from collections.abc import Awaitable, Callable
from hashlib import sha256
import json
import logging
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from PIL import Image

from models.composition import CompositionDocument, ContentBlock
from models.editorial_source import EditorialUnit
from models.novelty_plan import FidelityReview, NoveltyPlan, PlannedBlock
from services.image_service import SessionWorkspace, safe_path
from services.api_usage_service import GenerationApiAudit
from services.image_analysis_cache_service import (
    ImageAnalysisCache,
    StructuredImageDescription,
)
from services.editorial_structure_service import (
    extract_editorial_units,
    structured_source_prompt,
)
from services.model_routing import (
    configured_image_model,
    configured_text_model,
    is_model_availability_error,
    text_model_unavailable_error,
)
from services.system_path_formatter import (
    PATH_LABEL,
    format_system_navigation,
    missing_system_paths,
)
from services.text_sanitizer import markdown_highlights, strip_markdown
from services.providers.base_provider import AIProvider
from services.providers.exceptions import (
    ProviderError,
    sanitize_technical_detail,
    sanitized_exception_chain,
)


logger = logging.getLogger('EMC Novidade Visual IA')

PLAN_MAX_OUTPUT_TOKENS = 10500
CORRECTION_MAX_OUTPUT_TOKENS = 6800
REVIEW_MAX_OUTPUT_TOKENS = 6500


COMMON_FIDELITY_RULES = """Use somente fatos fornecidos na entrada.

Preserve integralmente as informações funcionais relacionadas às alterações efetivamente
implementadas, incluindo nomes técnicos, telas, campos, menus, botões, abas, parâmetros,
módulos, códigos, siglas, caminhos do sistema, listas funcionais, opções e IDs de imagens.

Nem toda informação presente na entrada deve ser publicada.

Informações de testes, simulações, cálculos de conferência, validações, hipóteses,
diagnósticos, logs, consultas ao banco de dados, XML, versões testadas, dados utilizados
em exemplos e números de IC, OC, chamados ou tarefas internas devem ser utilizadas apenas
como contexto para compreender a alteração.

Não inclua essas informações na matéria final, salvo quando forem indispensáveis para
explicar ao usuário o comportamento efetivo da funcionalidade.

Não trate como alteração concluída uma solicitação, requisito, hipótese, problema relatado,
investigação, análise em andamento ou implementação futura.

Não invente informações, não altere nomes técnicos e não substitua detalhes funcionais
importantes por descrições genéricas."""


WRITER_ORGANIZER_PROMPT = """Transforme o conteúdo recebido em uma matéria profissional para o
Portal de Novidades da EMC.

Atue como editor técnico especializado em novidades de versão. O conteúdo recebido pode
misturar alterações implementadas, solicitações, requisitos, testes, simulações, cálculos,
exemplos, diagnósticos, hipóteses e informações internas.

Antes de redigir, analise internamente toda a entrada e classifique as informações em:

1. Alterações efetivamente implementadas.
2. Correções efetivamente implementadas.
3. Melhorias efetivamente implementadas.
4. Rotinas, telas, caminhos, campos, botões, parâmetros e opções relacionados.
5. Informações utilizadas apenas como evidência, teste, validação, diagnóstico ou contexto.
6. Solicitações, hipóteses, pendências e implementações futuras que não podem ser apresentadas
   como concluídas.

Escreva a matéria somente com base nas alterações, correções e melhorias efetivamente
confirmadas pela entrada.

Utilize testes, cálculos, valores, exemplos, XML, consultas ao banco de dados, versões,
hipóteses, diagnósticos, números de IC, OC, chamados e tarefas internas apenas para
compreender o contexto. Não transforme essas informações em conteúdo da matéria.

Quando um exemplo representar diretamente uma opção, regra ou comportamento disponível
ao usuário, preserve apenas a informação funcional necessária. Não publique dados de teste,
valores de conferência ou identificadores utilizados exclusivamente na validação.

Não afirme que uma correção foi realizada quando a entrada apresentar apenas um problema,
uma solicitação, uma hipótese, uma proposta ou uma análise ainda não concluída.

Não crie uma matéria do zero e não acrescente fatos externos. Atue somente como editor
técnico do conteúdo fornecido.

O resultado deve soar como uma publicação editorial, nunca como manual, especificação
funcional, Ordem de Mudança, documentação interna, relatório de testes ou checklist.

Organize a leitura nesta sequência:

1. Apresente primeiro qual é a novidade.
2. Explique depois, de forma natural, como ela funciona.
3. Informe onde ela está disponível ou integrada, somente quando essa informação existir
   na entrada.

A introdução deve explicar a novidade usando apenas fatos do texto original. Não crie
introdução ou conclusão genérica.

Para cada rotina, tela ou contexto funcional alterado, crie uma seção independente.
Apresente um pequeno texto, depois o bloco de caminho daquela rotina, quando ele tiver
sido informado, e em seguida explique somente as alterações correspondentes.

Repita essa estrutura para todas as rotinas. Nunca misture alterações de telas, rotinas,
seções estruturadas ou caminhos diferentes no mesmo bloco.

Crie um título natural, claro e específico que represente o assunto principal.

Evite títulos genéricos como:

O que mudou
Como funciona
Justificativa
Próximos passos
Cadastro
Configuração
Integração

Prefira títulos que expressem diretamente a funcionalidade, como:

Perfis específicos para destinatários IBS/CBS

em vez de:

Cadastro de Perfis

Agrupe informações relacionadas em poucos parágrafos consistentes. Evite dezenas de
tópicos, numeração desnecessária e a transformação de cada frase em um item.

Use listas somente quando a própria informação exigir uma enumeração objetiva, como
perfis, formatos, campos ou opções. Nunca use uma lista para explicar toda a implementação.

Descreva o comportamento da funcionalidade, não o trabalho realizado pelo desenvolvimento.

Evite frases de marketing como:

traz grandes benefícios
otimiza processos
garante maior eficiência
avanços significativos
moderniza
facilita significativamente

Use essas expressões somente quando constarem literalmente na entrada e forem relevantes
para a explicação funcional.

Não use expressões como:

Criado
Criada
Implementado
Implementada
Criada tela
Criado campo
Criado botão
Criado menu
Criada seção
Criado parâmetro
Criada rotina
Item 1
Item 2
Item 3
Passo 1
Passo 2
Justificativa
Próximos passos
Será realizado
Será criada
Posteriormente
Nova OC
Nova ordem de mudança
Implementação futura

Prefira construções editoriais como:

A rotina passa a disponibilizar...
A tela agora apresenta...
Foi adicionada a opção...
Foi corrigido o comportamento...
O sistema passa a considerar...
A funcionalidade permite...
""" + COMMON_FIDELITY_RULES + """

Não inclua conteúdo comercial, data, hora, autor, comentários internos ou tempo de leitura.

Não reduza a cobertura das informações funcionais relacionadas às alterações efetivamente
implementadas.

Antes de redigir, faça internamente um inventário das funcionalidades, rotinas, telas,
campos, botões, abas, parâmetros, opções, listas e caminhos relacionados às alterações
confirmadas.

Trate como obrigatórias somente as informações funcionais necessárias para explicar:

1. O que mudou.
2. Como a funcionalidade passa a funcionar.
3. Onde a alteração está disponível, quando essa informação existir.
4. Quais opções, regras, campos ou comportamentos foram disponibilizados ou modificados.

Não considere obrigatórios:

- testes;
- simulações;
- cálculos de conferência;
- valores utilizados apenas para validação;
- hipóteses;
- diagnósticos;
- versões testadas;
- logs;
- XML;
- consultas ao banco de dados;
- números de IC, OC, chamados ou tarefas;
- comentários internos;
- caminhos de arquivos ou diretórios utilizados apenas pela equipe técnica.

A matéria final deve preservar a cobertura funcional das alterações implementadas, mas
deve remover contexto técnico, investigativo ou operacional que não pertença à publicação.

É permitido reorganizar os assuntos, melhorar a redação, agrupar informações relacionadas,
eliminar repetições reais e tornar a leitura mais agradável.

Eliminar repetição não autoriza remover detalhes funcionais complementares, regras,
opções, campos ou comportamentos necessários para compreender a alteração.

Quando a entrada informar explicitamente uma navegação do sistema, o caminho é obrigatório
e deve ficar em um bloco próprio, separado dos demais caminhos, usando duas linhas de
texto simples exatamente neste formato:

Acesse através do caminho:
X / Y / Z

Preserve todos os níveis e nomes fornecidos, sem resumir, trocar, unir ou inventar níveis.

Cada rotina deve manter seu próprio bloco de caminho.

Não aplique esse formato a:

- URLs;
- diretórios do Windows;
- caminhos de arquivos;
- endereços de rede;
- informações técnicas internas que não representem navegação do usuário no sistema.

Não escreva asteriscos. O negrito será aplicado pela interface.

Retorne somente texto simples nos conteúdos dos blocos.

Não use Markdown, HTML ou símbolos de formatação.

Não use:

**
#
__
crases
títulos Markdown
links Markdown
marcadores Markdown inseridos dentro de blocos de texto

O destaque visual será aplicado exclusivamente pela interface.

Crie título, introdução e poucas seções específicas, somente quando melhorarem a leitura.

Marque o primeiro bloco de texto com papel introducao.

Use todas as imagens fornecidas somente como apoio ao texto.

Posicione cada imagem junto da seção funcionalmente compatível.

Não invente relação entre imagem e conteúdo.

Não crie marcações e retorne imagens.marcacoes vazio.

Nunca altere, substitua ou omita uma imagem.

Quando não houver relação segura, deixe a imagem sem associação. O sistema aplicará o
fallback local seguindo a ordem de upload.

Retorne uma única estrutura NoveltyPlan."""


REVIEWER_PROMPT = """Compare rigorosamente o documento final com a fonte fornecida e a lista
de imagens.
""" + COMMON_FIDELITY_RULES + """

Verifique também:

- invenções;
- afirmações de implementação não confirmadas;
- alterações de nomes técnicos;
- conteúdo comercial;
- data;
- hora;
- autor;
- tempo de leitura;
- duplicações;
- imagens omitidas;
- IDs inválidos;
- associações incoerentes de imagens;
- Markdown nos textos;
- mistura de informações pertencentes a telas, rotinas ou seções diferentes;
- inclusão indevida de conteúdo interno, investigativo ou de teste.

Faça uma verificação de cobertura informacional item a item.

Confirme que nenhuma informação funcional relacionada às alterações efetivamente
implementadas foi omitida, resumida de forma que perdeu detalhes ou substituída por
descrição genérica.

Verifique especialmente a preservação de:

- funcionalidades;
- telas;
- campos;
- menus;
- botões;
- abas;
- parâmetros;
- caminhos do sistema;
- opções;
- listas funcionais;
- regras de comportamento;
- observações funcionais importantes.

Não exija a preservação de informações utilizadas apenas para:

- testes;
- simulações;
- cálculos de conferência;
- validações;
- hipóteses;
- diagnósticos;
- logs;
- XML;
- consultas ao banco de dados;
- versões testadas;
- exemplos contendo somente dados de teste;
- números de IC, OC, chamados ou tarefas internas;
- comentários de desenvolvimento;
- investigações ainda não concluídas.

A remoção dessas informações não deve ser considerada omissão funcional.

Reprove a matéria quando ela publicar indevidamente informações internas, investigativas,
de teste ou validação que não sejam necessárias para explicar o funcionamento da alteração
ao usuário.

Não reprove exemplos que representem diretamente uma opção, regra ou comportamento
funcional disponível ao usuário. Nesse caso, verifique se foram preservados apenas os
elementos funcionais necessários, sem dados internos ou de teste desnecessários.

Todo caminho de navegação do sistema presente na entrada e relacionado a uma alteração
implementada deve aparecer no documento com a expressão:

Acesse através do caminho:

Não trate URLs, diretórios do Windows, caminhos de arquivos ou endereços técnicos como
caminhos de navegação do sistema.

Quando houver omissão funcional real, registre-a objetivamente em
correcoes_necessarias para que a correção automática restaure somente a informação
funcional ausente.

Quando houver informação interna publicada indevidamente, registre em
correcoes_necessarias que o conteúdo deverá ser removido.

A matéria somente pode ser aprovada quando:

1. Preservar integralmente as alterações funcionais confirmadas.
2. Não apresentar solicitações, hipóteses ou implementações futuras como concluídas.
3. Não publicar informações internas desnecessárias.
4. Manter corretamente os caminhos das rotinas.
5. Preservar todas as imagens fornecidas.
6. Não misturar alterações de telas, rotinas, caminhos ou seções diferentes.

Verifique ainda se cada caminho foi mantido isolado em seu próprio bloco, sem unir rotas
distintas.

Não reescreva a matéria.

Retorne somente FidelityReview estruturado, preenchendo:

aprovada
avisos
correcoes_necessarias
imagens_nao_utilizadas
informacoes_possivelmente_inventadas
nomes_tecnicos_alterados
"""


COVER_PROMPT = """Crie uma imagem editorial de abertura corporativa, limpa e moderna para
uma matéria do Portal de Novidades da EMC.

A arte deve ser ilustrativa e não pode parecer uma captura do sistema.

Não crie interface, tela, campo, menu ou botão fictício.

Não inclua:

- data;
- hora;
- autor;
- tempo de leitura;
- textos longos;
- benefícios laterais;
- promessas comerciais;
- dados sensíveis;
- informações que não tenham relação com o tema.

Use somente o tema e o título fornecidos."""


WRITER_ORGANIZER_PROMPT += """
Cada rotina ou tela deve manter o secao_id correspondente ao inventário estruturado.

Preencha rotina e caminho nos blocos correspondentes.

Não mova informações entre secao_id diferentes.

Retorne enumerações exclusivamente como blocos do tipo lista, usando itens com texto e
destaques sem Markdown.

Os valores informados em destaques devem ser substrings literais existentes no texto do
respectivo item ou bloco.

Não transforme parágrafos comuns em listas sem necessidade.

Preserve bloco_id quando ele for fornecido durante uma correção localizada.
"""


REVIEWER_PROMPT += """
Identifique objetivamente os blocos e as seções que precisam de correção nos campos
blocos_para_corrigir e secoes_para_corrigir.

Selecione somente os blocos ou seções diretamente relacionados às correções necessárias.

Não solicite a restauração de informações corretamente removidas por serem testes,
simulações, cálculos, diagnósticos, hipóteses, logs ou informações internas.

Depois de uma correção, revise novamente o documento completo.

Somente aprove quando:

- caminhos funcionais estiverem íntegros;
- listas funcionais estiverem completas;
- destaques forem substrings literais válidas;
- unidades funcionais estiverem preservadas;
- informações internas indevidas tiverem sido removidas;
- nenhuma informação funcional confirmada tiver sido perdida;
- nenhuma alteração não confirmada tiver sido apresentada como concluída.

Considere como formato canônico correto a expressão:

Acesse através do caminho:

Considere corretas as rotas com níveis separados por:

 /

Exemplo:

Fiscal / Cadastros / Perfis

Entradas com o separador ">" são normalizadas localmente para esse formato.

Não registre essa normalização de separador ou acentuação como alteração de nome técnico,
invenção, omissão ou correção necessária.
"""

def _review_dict(review: FidelityReview) -> dict[str, Any]:
    return {
        **review.model_dump(),
        'status': 'concluida',
        'correcao_automatica_realizada': False,
    }


def _compact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item for key, item in value.items()
        if item not in ('', None, [], {})
    }


def _compact_block(block: ContentBlock | dict[str, Any]) -> dict[str, Any]:
    value = block.to_dict() if isinstance(block, ContentBlock) else dict(block)
    return _compact_mapping(value)


def _compact_document(
    document: CompositionDocument, blocks: list[ContentBlock] | None = None,
) -> dict[str, Any]:
    selected = list(blocks if blocks is not None else document.blocks)
    return {
        'origem': document.source,
        'titulo': document.title,
        'imagens_fallback': list(document.fallback_image_ids),
        'blocos': [
            {'ordem': document.blocks.index(block) + 1, **_compact_block(block)}
            for block in selected
        ],
    }


def _compact_unit(unit: EditorialUnit) -> dict[str, Any]:
    return _compact_mapping(unit.to_dict())


def _block_metadata(planned: Any) -> dict[str, str]:
    return {
        'secao_id': str(getattr(planned, 'secao_id', '') or ''),
        'rotina': str(getattr(planned, 'rotina', '') or ''),
        'caminho': str(getattr(planned, 'caminho', '') or ''),
    }


def _section_text(blocks: list[ContentBlock]) -> str:
    values: list[str] = []
    for block in blocks:
        values.append(block.conteudo)
        values.extend(str(item.get('texto') or '') for item in block.itens)
    return '\n'.join(values).casefold()


def _ensure_source_structure(
    document: CompositionDocument, units: list[EditorialUnit],
) -> None:
    for unit in units:
        indices = [
            index for index, block in enumerate(document.blocks)
            if block.secao_id == unit.id
        ]
        if not indices and unit.caminho:
            indices = [
                index for index, block in enumerate(document.blocks)
                if block.caminho == unit.caminho
                or (block.papel == 'caminho' and unit.caminho in block.conteudo)
            ]
        if not indices:
            insert_at = len(document.blocks)
            if unit.nome:
                document.blocks.insert(insert_at, ContentBlock(
                    tipo='subtitulo', conteudo=unit.nome,
                    secao_id=unit.id, rotina=unit.nome, caminho=unit.caminho,
                ))
                insert_at += 1
            indices = [insert_at - 1] if insert_at else [0]
        section_blocks = [
            block for block in document.blocks if block.secao_id == unit.id
        ]
        insert_at = max(indices) + 1
        if unit.caminho and not any(
            block.papel == 'caminho' and unit.caminho in block.conteudo
            for block in section_blocks
        ):
            document.blocks.insert(insert_at, ContentBlock(
                tipo='texto', papel='caminho', secao_id=unit.id,
                rotina=unit.nome, caminho=unit.caminho,
                conteudo=f'{PATH_LABEL}\n{unit.caminho}',
            ))
            insert_at += 1
        section_blocks = [
            block for block in document.blocks if block.secao_id == unit.id
        ]
        section_text = _section_text(section_blocks)
        labelled = (
            ('Campos', unit.campos), ('Botões', unit.botoes),
            ('Parâmetros', unit.parametros), ('Opções', unit.opcoes),
            ('Exemplos', unit.exemplos), ('Observações', unit.observacoes),
        )
        for label, values in labelled:
            missing = [value for value in values if value.casefold() not in section_text]
            if not missing:
                continue
            document.blocks.insert(insert_at, ContentBlock(
                tipo='lista', papel=label.casefold(), secao_id=unit.id,
                rotina=unit.nome, caminho=unit.caminho,
                itens=[{'texto': value, 'destaques': [value]} for value in missing],
            ))
            insert_at += 1
            section_text += '\n' + '\n'.join(value.casefold() for value in missing)
        for source_list in unit.listas:
            missing_items = [
                dict(item) for item in source_list
                if str(item.get('texto') or '').casefold() not in section_text
            ]
            if missing_items:
                document.blocks.insert(insert_at, ContentBlock(
                    tipo='lista', papel='lista', secao_id=unit.id,
                    rotina=unit.nome, caminho=unit.caminho, itens=missing_items,
                ))
                insert_at += 1
                section_text += '\n' + '\n'.join(
                    str(item.get('texto') or '').casefold() for item in missing_items
                )


def document_from_editorial_plan(
    plan: NoveltyPlan,
    images: list[dict[str, Any]],
    source_units: list[EditorialUnit] | None = None,
) -> CompositionDocument:
    by_id = {str(item['id']): item for item in images}
    blocks: list[ContentBlock] = []
    used: set[str] = set()
    for planned in plan.blocos:
        metadata = _block_metadata(planned)
        block_id = str(getattr(planned, 'bloco_id', '') or '')
        if planned.tipo == 'lista':
            blocks.append(ContentBlock(
                tipo='lista', id=block_id or ContentBlock(tipo='lista', itens=[]).id,
                itens=[item.model_dump() for item in planned.itens],
                papel=planned.papel or 'lista', **metadata,
            ))
            continue
        if planned.tipo != 'imagem':
            raw_content = planned.conteudo
            highlights = [
                *markdown_highlights(raw_content),
                *[str(value) for value in planned.destaques],
            ]
            content = strip_markdown(format_system_navigation(raw_content))
            if planned.tipo != 'texto':
                blocks.append(ContentBlock(
                    tipo=planned.tipo, id=block_id or ContentBlock(tipo=planned.tipo).id,
                    conteudo=content, destaques=list(dict.fromkeys(highlights)),
                    papel=planned.papel, **metadata,
                ))
                continue
            lines = content.splitlines()
            pending: list[str] = []
            pending_list: list[dict[str, Any]] = []
            text_role = planned.papel

            def flush_pending() -> None:
                nonlocal text_role
                value = '\n'.join(pending).strip()
                pending.clear()
                if value:
                    blocks.append(ContentBlock(
                        tipo='texto', conteudo=value, papel=text_role,
                        destaques=list(dict.fromkeys(highlights)), **metadata,
                    ))
                    text_role = ''

            def flush_list() -> None:
                nonlocal text_role
                if pending_list:
                    blocks.append(ContentBlock(
                        tipo='lista', itens=list(pending_list), papel='lista', **metadata,
                    ))
                    pending_list.clear()
                    text_role = ''

            index = 0
            while index < len(lines):
                line = lines[index]
                list_match = __import__('re').match(
                    r'^\s*(?:[-+*•]|\d+[.)])\s+(.+)$', line,
                )
                if list_match:
                    flush_pending()
                    item_raw = list_match.group(1)
                    pending_list.append({
                        'texto': strip_markdown(item_raw),
                        'destaques': markdown_highlights(item_raw),
                    })
                    index += 1
                    continue
                flush_list()
                if (
                    line.strip().casefold() == PATH_LABEL.casefold()
                    and index + 1 < len(lines)
                ):
                    flush_pending()
                    path = lines[index + 1].strip()
                    blocks.append(ContentBlock(
                        tipo='texto', papel='caminho', conteudo=f'{PATH_LABEL}\n{path}',
                        caminho=path, secao_id=metadata['secao_id'],
                        rotina=metadata['rotina'],
                    ))
                    index += 2
                    continue
                pending.append(line)
                index += 1
            flush_list()
            flush_pending()
            continue
        valid_ids = [
            str(image_id) for image_id in planned.imagem_ids
            if str(image_id) in by_id and str(image_id) not in used
        ][:2]
        if not valid_ids:
            continue
        mode = planned.modo_exibicao
        if mode == 'lado_a_lado' and len(valid_ids) != 2:
            mode = 'normal'
        blocks.append(ContentBlock(
            tipo='imagem', id=block_id or ContentBlock(tipo='imagem', imagem_id=valid_ids[0]).id,
            imagem_id=valid_ids[0],
            imagem_secundaria_id=valid_ids[1] if len(valid_ids) > 1 else None,
            legenda=strip_markdown(planned.legenda), modo_exibicao=mode,
            confianca=planned.confianca, motivo=planned.motivo, **metadata,
        ))
        used.update(valid_ids)

    document = CompositionDocument(blocks=blocks, source='manual')
    missing = [str(item['id']) for item in images if str(item['id']) not in used]
    for image_id in missing:
        document.blocks.append(ContentBlock(tipo='imagem', imagem_id=image_id))
    document.fallback_image_ids = missing
    _ensure_source_structure(document, list(source_units or []))
    document._undo.clear()
    if not document.title or not document.introduction:
        raise ProviderError(
            'Não foi possível organizar o texto da novidade. '
            'O conteúdo original e as imagens foram preservados.',
            code='incomplete_editorial_text', stage='redator_tecnico',
        )
    return document


def _append_missing_paths(
    document: CompositionDocument, paths: list[str], units: list[EditorialUnit] | None = None,
) -> None:
    by_path = {unit.caminho: unit for unit in units or [] if unit.caminho}
    for path in paths:
        unit = by_path.get(path)
        if unit:
            _ensure_source_structure(document, [unit])
            continue
        document.blocks.append(ContentBlock(
            tipo='texto', papel='caminho', conteudo=f'{PATH_LABEL}\n{path}', caminho=path,
        ))


def _merge_corrected_document(
    original: CompositionDocument,
    corrected: CompositionDocument,
    review: FidelityReview,
) -> CompositionDocument:
    target_ids = set(review.blocos_para_corrigir)
    target_sections = set(review.secoes_para_corrigir)
    replacements_by_id = {block.id: block for block in corrected.blocks if block.id in target_ids}
    corrected_sections = {
        section: [block for block in corrected.blocks if block.secao_id == section]
        for section in target_sections
    }
    if not replacements_by_id and not any(corrected_sections.values()):
        return original
    result: list[ContentBlock] = []
    inserted_sections: set[str] = set()
    for block in original.blocks:
        if block.secao_id in target_sections:
            if block.secao_id not in inserted_sections:
                result.extend(corrected_sections.get(block.secao_id) or [block])
                inserted_sections.add(block.secao_id)
            continue
        result.append(replacements_by_id.get(block.id, block))
    merged = CompositionDocument(
        blocks=result, source=original.source,
        fallback_image_ids=original.fallback_image_ids,
        cover=original.cover, review=original.review,
    )
    return merged

class EditorialOrchestrator:
    IMAGE_DESCRIPTION_INSTRUCTION = """
Para cada imagem recebida nesta chamada, preencha imagens com imagem_id, descricao objetiva,
texto_visivel e elementos. A descricao deve registrar somente o que estiver visivel, sem inventar.
Esses dados serão armazenados por hash e reutilizados; não repita Base64 nem solicite nova análise.
"""

    LOCALIZED_CORRECTION_PROMPT = """Corrija somente os blocos_alvo fornecidos.
Retorne exclusivamente os blocos corrigidos em NoveltyPlan.blocos.
Preserve bloco_id e secao_id. Não devolva o documento completo, imagens ou blocos não solicitados.
Use apenas correcoes e trechos_fonte do payload. Não invente informações.
"""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self._image_cache = ImageAnalysisCache()

    def _key(
        self,
        provider: AIProvider,
        model: str,
        raw_text: str,
        image_fingerprints: list[tuple[str, str]],
        cover_requested: bool,
    ) -> str:
        digest = sha256()
        digest.update(getattr(provider, 'name', provider.__class__.__name__).encode())
        digest.update(model.encode())
        digest.update(raw_text.encode('utf-8'))
        digest.update(str(cover_requested).encode())
        for image_id, file_hash in image_fingerprints:
            digest.update(image_id.encode())
            digest.update(file_hash.encode())
        return digest.hexdigest()

    def _prepare_images(
        self, images: list[dict[str, Any]], workspace: SessionWorkspace,
    ) -> tuple[list[dict[str, Any]], dict[str, str], list[tuple[str, str]]]:
        unique: list[dict[str, Any]] = []
        id_to_hash: dict[str, str] = {}
        fingerprints: list[tuple[str, str]] = []
        seen_files: set[str] = set()
        self._image_cache.root.mkdir(parents=True, exist_ok=True)
        self._image_cache.thumbnail_dir.mkdir(parents=True, exist_ok=True)
        for item in images:
            image_id = str(item['id'])
            source = safe_path(Path(item['original']))
            source_bytes = source.read_bytes()
            file_hash = sha256(source_bytes).hexdigest()
            id_to_hash[image_id] = file_hash
            fingerprints.append((image_id, file_hash))
            item.update(
                treated=source,
                final=source,
                annotated=None,
                status='Preservada sem alterações',
                error='',
            )
            if file_hash in seen_files:
                continue
            seen_files.add(file_hash)
            thumbnail_path = self._image_cache.thumbnail_path(file_hash)
            if not thumbnail_path.exists():
                with Image.open(source) as opened:
                    thumbnail = opened.convert('RGB')
                    thumbnail.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                    thumbnail.save(thumbnail_path, 'PNG', optimize=True)
            unique.append({
                'id': image_id,
                'source': source,
                'thumbnail': thumbnail_path,
                'file_hash': file_hash,
            })
        return unique, id_to_hash, fingerprints

    @staticmethod
    def _description_from_plan(
        plan: NoveltyPlan, image_id: str, file_hash: str,
    ) -> StructuredImageDescription:
        planned = next(
            (item for item in plan.imagens if str(item.imagem_id) == image_id), None,
        )
        block = next(
            (item for item in plan.blocos if image_id in item.imagem_ids), None,
        )
        description = str(getattr(planned, 'descricao', '') or '').strip()
        if not description and block:
            description = str(block.legenda or block.motivo or '').strip()
        if not description:
            description = 'Captura de tela vinculada à novidade, sem descrição textual adicional.'
        return StructuredImageDescription(
            file_hash=file_hash,
            description=description,
            visible_text=[str(value) for value in getattr(planned, 'texto_visivel', [])],
            elements=[str(value) for value in getattr(planned, 'elementos', [])],
        )

    @staticmethod
    def _minimal_source_excerpt(raw_text: str, corrections: list[str], limit: int = 1800) -> str:
        terms = {
            word.casefold() for word in __import__('re').findall(r'[\wÀ-ÿ-]{4,}', ' '.join(corrections))
        }
        candidates = [line.strip() for line in raw_text.splitlines() if line.strip()]
        ranked = sorted(
            enumerate(candidates),
            key=lambda pair: (
                -sum(term in pair[1].casefold() for term in terms), pair[0]
            ),
        )
        selected: list[str] = []
        size = 0
        for _, line in ranked:
            if selected and not any(term in line.casefold() for term in terms):
                continue
            if size + len(line) > limit:
                continue
            selected.append(line)
            size += len(line) + 1
            if size >= limit:
                break
        if not selected and candidates:
            selected.append(candidates[0][:limit])
        return '\n'.join(selected)

    @staticmethod
    def _target_blocks(
        document: CompositionDocument, review: FidelityReview, maximum: int = 8,
    ) -> list[ContentBlock]:
        target_ids = set(review.blocos_para_corrigir)
        target_sections = set(review.secoes_para_corrigir)
        selected = [
            block for block in document.blocks
            if block.id in target_ids or (block.secao_id and block.secao_id in target_sections)
        ]
        if selected:
            return selected[:maximum]
        correction_terms = {
            word.casefold()
            for word in __import__('re').findall(
                r'[\wÀ-ÿ-]{4,}', ' '.join(review.correcoes_necessarias)
            )
        }
        ranked = sorted(
            (
                (
                    sum(term in json.dumps(block.to_dict(), ensure_ascii=False).casefold()
                        for term in correction_terms),
                    index,
                    block,
                )
                for index, block in enumerate(document.blocks)
                if block.tipo != 'imagem'
            ),
            key=lambda value: (-value[0], value[1]),
        )
        return [value[2] for value in ranked[:maximum]]

    async def generate(
        self,
        provider: AIProvider,
        *,
        api_key: str,
        text_model: str,
        image_model: str,
        raw_text: str,
        images: list[dict[str, Any]],
        workspace: SessionWorkspace,
        cover_requested: bool,
        run_sync: Callable[..., Awaitable[Any]],
        on_progress: Callable[[str], None] | None = None,
        generation_id: str = '',
        idempotency_key: str = '',
    ) -> CompositionDocument:
        provider_name = getattr(provider, 'name', provider.__class__.__name__).lower()
        text_model = configured_text_model(provider_name, text_model)
        image_model = configured_image_model(provider_name, image_model)
        routed_models = f'text={text_model}|image={image_model}'
        generation_id = generation_id or uuid4().hex
        preparation_started = time.perf_counter()
        prepared_images, id_to_hash, fingerprints = await run_sync(
            self._prepare_images, images=images, workspace=workspace,
        )
        cache_key = self._key(
            provider, routed_models, raw_text, fingerprints, cover_requested,
        )
        idempotency_key = idempotency_key or cache_key
        audit = GenerationApiAudit(generation_id, idempotency_key)
        audit.started = preparation_started
        audit.record_stage('preparacao', time.perf_counter() - preparation_started)
        processing_started = time.perf_counter()
        stage_started = processing_started
        active_stage = ''
        ai_calls = 0
        logger.info(
            '[PROCESSAMENTO] Início; geracao=%s idempotencia=%s sessao=%s provider=%s '
            'texto_bytes=%s imagens=%s capa=%s',
            generation_id, idempotency_key, workspace.session_id, provider_name,
            len(raw_text.encode('utf-8')), len(images), bool(cover_requested),
        )
        logger.info('[MODELOS] Texto: Modelo: %s', text_model)
        logger.info('[MODELOS] Imagem: Modelo: %s', image_model)

        def progress(stage: str) -> None:
            nonlocal active_stage, stage_started
            now = time.perf_counter()
            if active_stage:
                duration = now - stage_started
                logger.info('[PROCESSAMENTO] %s concluída em %.2f s', active_stage, duration)
                audit.record_stage(active_stage, duration)
            active_stage = stage
            stage_started = now
            logger.info('[PROCESSAMENTO] %s iniciada', stage)
            if on_progress:
                on_progress(stage)

        async def execute_ai(operation: str, function: Callable[..., Any], **kwargs: Any) -> Any:
            nonlocal ai_calls
            ai_calls += 1
            endpoint = (
                '/v1/images/generations' if operation == 'capa'
                else '/v1/responses'
            )
            model = str(kwargs.get('model') or '')
            audit.start_call(ai_calls, operation, endpoint, model)
            provider.set_api_audit_context(audit, ai_calls)
            text_bytes = sum(
                len(str(kwargs.get(key) or '').encode('utf-8'))
                for key in ('raw_text', 'prompt')
            )
            if kwargs.get('document') is not None:
                text_bytes += len(json.dumps(
                    kwargs['document'], ensure_ascii=False, separators=(',', ':'),
                ).encode('utf-8'))
            image_bytes = sum(
                path.stat().st_size for _, path in kwargs.get('images', []) if path.is_file()
            )
            request_started = time.perf_counter()
            logger.info(
                '[PROCESSAMENTO] Chamada IA %s iniciada; geracao=%s operacao=%s modelo=%s '
                'endpoint=%s tentativas_maximas=2 texto_bytes=%s imagens_bytes=%s',
                ai_calls, generation_id, operation, model, endpoint, text_bytes, image_bytes,
            )
            error: BaseException | None = None
            try:
                result = await run_sync(function, **kwargs)
                received_bytes = (
                    result.stat().st_size
                    if isinstance(result, Path) and result.is_file()
                    else 0
                )
                logger.info(
                    '[PROCESSAMENTO] Chamada IA %s concluída em %.2f s; operacao=%s '
                    'endpoint=%s recebido_bytes=%s',
                    ai_calls, time.perf_counter() - request_started,
                    operation, endpoint, received_bytes,
                )
                return result
            except Exception as exc:
                error = exc
                logger.error(
                    '[PROCESSAMENTO] Chamada IA %s falhou em %.2f s; operacao=%s '
                    'endpoint=%s timeout=%s excecao=%s',
                    ai_calls, time.perf_counter() - request_started, operation, endpoint,
                    exc.__class__.__name__ == 'APITimeoutError'
                    or 'timeout' in str(exc).casefold(),
                    exc.__class__.__name__,
                )
                raise
            finally:
                provider.clear_api_audit_context()
                audit.finish_call(ai_calls, error)

        try:
            if cache_key in self._cache:
                logger.info(
                    '[PROCESSAMENTO] Cache concluído em %.2f s; chamadas_ia=0',
                    time.perf_counter() - processing_started,
                )
                return CompositionDocument.from_dict(self._cache[cache_key])

            source_units = extract_editorial_units(raw_text)
            editorial_input = structured_source_prompt(raw_text, source_units)
            descriptions_by_hash = {
                prepared['file_hash']: self._image_cache.load(prepared['file_hash'])
                for prepared in prepared_images
            }
            uncached = [
                prepared for prepared in prepared_images
                if descriptions_by_hash[prepared['file_hash']] is None
            ]
            descriptions_by_id: dict[str, StructuredImageDescription] = {}
            for image_id, file_hash in id_to_hash.items():
                cached = descriptions_by_hash.get(file_hash)
                if cached:
                    descriptions_by_id[image_id] = cached
            cached_prompt = [
                description.to_prompt_dict(image_id)
                for image_id, description in descriptions_by_id.items()
            ]
            if cached_prompt:
                editorial_input += '\n\nDESCRIÇÕES DE IMAGENS JÁ ANALISADAS:\n' + json.dumps(
                    cached_prompt, ensure_ascii=False, separators=(',', ':'),
                )
            organization_prompt = WRITER_ORGANIZER_PROMPT
            if uncached:
                organization_prompt += self.IMAGE_DESCRIPTION_INSTRUCTION

            progress('organizacao')
            try:
                plan = await execute_ai(
                    'organizacao', provider.generate_novelty_plan,
                    api_key=api_key, model=text_model, raw_text=editorial_input,
                    images=[(item['id'], item['thumbnail']) for item in uncached],
                    prompt=organization_prompt,
                    max_output_tokens=PLAN_MAX_OUTPUT_TOKENS,
                )
                for prepared in uncached:
                    description = self._description_from_plan(
                        plan, prepared['id'], prepared['file_hash'],
                    )
                    self._image_cache.save(description)
                    descriptions_by_hash[prepared['file_hash']] = description
                descriptions_by_id = {
                    image_id: descriptions_by_hash[file_hash]
                    for image_id, file_hash in id_to_hash.items()
                    if descriptions_by_hash.get(file_hash) is not None
                }
                document = document_from_editorial_plan(plan, images, source_units)
                required_path_corrections = missing_system_paths(raw_text, document.text)
            except Exception as exc:
                if is_model_availability_error(exc):
                    raise text_model_unavailable_error(provider_name, text_model, exc) from exc
                detail = sanitized_exception_chain(exc, api_key)
                raise ProviderError(
                    'Não foi possível organizar o texto da novidade. '
                    'O conteúdo original e as imagens foram preservados.',
                    code=getattr(exc, 'code', 'editorial_generation_failed'),
                    technical_detail=sanitize_technical_detail(detail, api_key),
                    provider=getattr(provider, 'name', ''), model=text_model,
                    stage='redator_organizador',
                    status_http=getattr(exc, 'status_http', None),
                    exception_type=exc.__class__.__name__,
                ) from exc

            image_description_payload = [
                description.to_prompt_dict(image_id)
                for image_id, description in descriptions_by_id.items()
            ]
            progress('revisao')
            try:
                review = await execute_ai(
                    'revisao', provider.review_novelty,
                    api_key=api_key, model=text_model, raw_text=raw_text,
                    document=_compact_document(document),
                    image_ids=[str(item['id']) for item in images],
                    **({'image_descriptions': image_description_payload} if provider_name == 'openai' else {}),
                    prompt=REVIEWER_PROMPT,
                    max_output_tokens=REVIEW_MAX_OUTPUT_TOKENS,
                )
                if required_path_corrections:
                    path_sections = [
                        unit.id for unit in source_units
                        if unit.caminho in required_path_corrections
                    ]
                    review = review.model_copy(update={
                        'aprovada': False,
                        'correcoes_necessarias': [
                            *review.correcoes_necessarias,
                            *[
                                f'Restaurar junto da rotina correta: {PATH_LABEL} {path}'
                                for path in required_path_corrections
                            ],
                        ],
                        'secoes_para_corrigir': list(dict.fromkeys([
                            *review.secoes_para_corrigir, *path_sections,
                        ])),
                    })
                document.review = _review_dict(review)
                if not review.aprovada and review.correcoes_necessarias:
                    targets = self._target_blocks(document, review)
                    target_ids = [block.id for block in targets]
                    target_sections = list(dict.fromkeys(
                        block.secao_id for block in targets if block.secao_id
                    ))
                    effective_review = review.model_copy(update={
                        'blocos_para_corrigir': list(dict.fromkeys([
                            *review.blocos_para_corrigir, *target_ids,
                        ])),
                        'secoes_para_corrigir': list(dict.fromkeys([
                            *review.secoes_para_corrigir, *target_sections,
                        ])),
                    })
                    correction_payload = {
                        'correcoes': list(effective_review.correcoes_necessarias),
                        'blocos_alvo': [_compact_block(block) for block in targets],
                        'trechos_fonte': self._minimal_source_excerpt(
                            raw_text, effective_review.correcoes_necessarias,
                        ),
                    }
                    progress('correcao')
                    corrected_plan = await execute_ai(
                        'correcao', provider.generate_novelty_plan,
                        api_key=api_key, model=text_model,
                        raw_text=json.dumps(
                            correction_payload, ensure_ascii=False, separators=(',', ':'),
                        ),
                        images=[], prompt=self.LOCALIZED_CORRECTION_PROMPT,
                        max_output_tokens=CORRECTION_MAX_OUTPUT_TOKENS,
                    )
                    validation_plan = NoveltyPlan(blocos=[
                        PlannedBlock(tipo='titulo', conteudo=document.title),
                        PlannedBlock(
                            tipo='texto', conteudo=document.introduction or 'Conteúdo em correção.',
                            papel='introducao',
                        ),
                        *corrected_plan.blocos,
                    ])
                    corrected_candidate = document_from_editorial_plan(
                        validation_plan, [], None,
                    )
                    document = _merge_corrected_document(
                        document, corrected_candidate, effective_review,
                    )
                    _ensure_source_structure(document, source_units)
                    _append_missing_paths(
                        document, missing_system_paths(raw_text, document.text), source_units,
                    )
                    progress('segunda_revisao')
                    second_review = await execute_ai(
                        'segunda_revisao', provider.review_novelty,
                        api_key=api_key, model=text_model, raw_text=raw_text,
                        document=document.to_dict(),
                        image_ids=[str(item['id']) for item in images],
                        **({'image_descriptions': image_description_payload} if provider_name == 'openai' else {}),
                        prompt=REVIEWER_PROMPT,
                        max_output_tokens=REVIEW_MAX_OUTPUT_TOKENS,
                    )
                    document.review = {
                        **_review_dict(second_review),
                        'status': (
                            'concluida' if second_review.aprovada
                            else 'revisao_manual_necessaria'
                        ),
                        'correcao_automatica_realizada': True,
                        'avisos': [
                            *second_review.avisos,
                            *([] if second_review.aprovada else [
                                'A correção foi revisada novamente e ainda exige revisão manual.'
                            ]),
                        ],
                    }
            except Exception as exc:
                if is_model_availability_error(exc):
                    raise text_model_unavailable_error(provider_name, text_model, exc) from exc
                _append_missing_paths(
                    document, missing_system_paths(raw_text, document.text), source_units,
                )
                logger.warning(
                    'Editorial review unavailable; provider=%s exception=%s',
                    getattr(provider, 'name', ''), exc.__class__.__name__,
                )
                document.review = {
                    'aprovada': False,
                    'status': 'nao_concluida',
                    'correcao_automatica_realizada': False,
                    'avisos': [
                        'A matéria foi gerada, mas a revisão automática não pôde ser '
                        'concluída. Revise o conteúdo antes de aprovar.'
                    ],
                }

            document.cover = {
                'solicitada': bool(cover_requested),
                'status': 'nao_gerada',
                'tema': document.title,
                'arquivo': '',
                'mensagem': '',
            }
            if cover_requested:
                progress('capa')
                try:
                    cover_path = await execute_ai(
                        'capa', provider.generate_cover,
                        api_key=api_key, model=image_model,
                        prompt=f'{COVER_PROMPT}\nTEMA: {document.title}\nTITULO: {document.title}',
                        destination=workspace.export_dir / 'capa' / 'capa.png',
                    )
                    document.cover.update(status='gerada', arquivo=str(cover_path))
                except Exception as exc:
                    logger.warning(
                        'Cover generation unavailable; provider=%s exception=%s',
                        getattr(provider, 'name', ''), exc.__class__.__name__,
                    )
                    document.cover.update(
                        status='falhou',
                        mensagem='A matéria foi gerada, mas não foi possível criar a imagem de capa.',
                    )

            progress('preparacao_visualizacao')
            document._undo.clear()
            self._cache[cache_key] = document.to_dict()
            now = time.perf_counter()
            if active_stage:
                duration = now - stage_started
                logger.info('[PROCESSAMENTO] %s concluída em %.2f s', active_stage, duration)
                audit.record_stage(active_stage, duration)
                active_stage = ''
            logger.info(
                '[PROCESSAMENTO] Total concluído em %.2f s; chamadas_ia=%s',
                now - processing_started, ai_calls,
            )
            return document
        finally:
            if active_stage:
                audit.record_stage(active_stage, time.perf_counter() - stage_started)
                active_stage = ''
            audit.log_summary()