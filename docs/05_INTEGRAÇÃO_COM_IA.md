# 05 - Integracao com IA

## Providers disponiveis

O projeto suporta dois providers:

- OpenAI;
- Gemini.

Eles sao instanciados por `ProviderFactory.create()`.

## Modelos utilizados

Configuracao central em `core/config/settings.py`:

- OpenAI texto: `gpt-5-mini`;
- OpenAI imagem: `gpt-image-1.5`;
- Gemini texto: `gemini-2.5-pro`;
- Gemini imagem: `gemini-3.1-flash-image`.

O roteamento e validacao adicional passam por `services/model_routing.py`.

## Acoes que usam IA

### Prompts

Arquivo: `services/editorial_prompts.py`

Os tres prompts do fluxo editorial (`WRITER_ORGANIZER_PROMPT`,
`REVIEWER_PROMPT` e `LOCALIZED_CORRECTION_PROMPT`) sao montados a partir das
mesmas secoes de regra, no fim do arquivo. Cada exigencia aparece em um lugar so
e tem um id: `F` de fidelidade, `P` de proibicao, `E` de escrita, `S` de saida e
`V` de verificacao. O revisor cita o id da regra violada em
`correcoes_necessarias`, e o corretor recebe esse texto junto das mesmas regras.

Para alterar uma regra, altere a secao correspondente: a mudanca vale para o
redator, para o revisor e para o corretor de uma vez.

O ZIP exportado leva o prompt do redator em `prompt_utilizado.txt`.

### Geracao da novidade

Responsavel: `EditorialOrchestrator.generate()`

Chama o provider para:

- organizar o texto;
- analisar o conjunto texto + imagens;
- produzir `NoveltyPlan`.

### Revisao de fidelidade

Responsavel: `provider.review_novelty()`

Produz `FidelityReview`.

### Correcao automatica

Quando a revisao pede ajustes, o sistema chama novamente o provider para gerar um plano de correcao.

### Geracao de capa

Responsavel: `provider.generate_cover()`.

### Tratamento de imagem

O fluxo de anonimização/tratamento de imagem usa:

- `provider.treat_image()`.

## Acoes que nao usam IA

- persistencia de estado;
- rearranjo manual de blocos;
- undo/redo;
- exportacao;
- HTML;
- XML;
- ZIP;
- preview;
- editor manual de imagens;
- controle de concorrencia;
- cache local;
- sanitizacao de markdown;
- normalizacao de caminhos.

## OpenAI

Arquivo: `services/providers/openai_provider.py`

Capacidades observadas:

- `treat_image()`;
- `suggest_orientation()`;
- `generate_novelty_plan()`;
- `review_novelty()`;
- `generate_cover()`.

Observacoes:

- usa `responses.parse()` para saidas estruturadas;
- usa `images.edit()` para tratamento de imagem;
- usa `images.generate()` para capa;
- registra auditoria de chamadas via `GenerationApiAudit`.

## Gemini

Arquivo: `services/providers/gemini_provider.py`

Capacidades observadas:

- `treat_image()`;
- `suggest_orientation()`;
- `generate_novelty_plan()`;
- `review_novelty()`;
- `generate_cover()`.

Observacoes:

- usa `genai.Client`;
- valida chave da API de forma mais restritiva;
- retorna JSON estruturado quando esperado.

## Custos possiveis

As acoes que podem gerar custo sao:

- organizacao textual;
- revisao de fidelidade;
- correcao automatica;
- analise multimodal;
- tratamento de imagem;
- geracao de capa.

`GenerationApiAudit` em `services/api_usage_service.py` calcula estimativa aproximada com base em tokens e, no caso de imagem, em fallback quando a resposta nao expõe uso detalhado.

## Seguranca

`services/providers/exceptions.py` sanitiza mensagens tecnicas e remove:

- chaves de API;
- bearer tokens;
- dados de imagem em base64;
- e-mails;
- CPF;
- CNPJ;
- telefones.

