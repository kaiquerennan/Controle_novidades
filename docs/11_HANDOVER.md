# 11 - Handover

Este documento resume o estado real do projeto para transicao entre equipes.

## O que ja esta pronto

- aplicacao web em NiceGUI funcional;
- upload de imagens e entrada de texto bruto;
- organizacao automatica do conteudo por IA;
- revisao de fidelidade;
- correcao automatica quando necessario;
- segunda revisao apos correcao;
- capa opcional;
- visualizacao editorial;
- editor manual de imagens;
- edicao textual e ajuste de posicao dos blocos;
- exportacao para HTML, XML, JSON e ZIP;
- persistencia do estado de tela;
- controle de geracao por sessao;
- cache de analise de imagem;
- suporte a OpenAI e Gemini.

## O que ainda falta

- integracao com PGC;
- contrato externo de troca de dados com PGC;
- validacao de multiusuario alem do isolamento por sessao;
- definicao futura de eventuais consumos externos.

## Decisoes arquiteturais importantes

- a IA fica isolada em `services/providers/`;
- o editor manual de imagens usa um contrato de dominio proprio (`Annotation`);
- a novidade e representada por `CompositionDocument`;
- a exportacao final e separada do fluxo de visualizacao;
- o estado da interface e persistido localmente em `data/screen_state.json`;
- o fluxo de geracao usa idempotencia e controle de concorrencia;
- os caminhos do sistema sao normalizados antes de seguir para exportacao.

## Riscos

- dependencias de IA podem falhar por modelo indisponivel, cota ou chave invalida;
- a exportacao depende de arquivos locais consistentes;
- o cache de analise de imagem pode ficar desatualizado se o arquivo de entrada mudar fora do fluxo esperado;
- a integracao futura com PGC exigira contrato formal antes de ser implementada.

## Cuidados

- nao alterar chamadas de IA sem entender o impacto em custo, formato e revisao;
- nao mexer em `services/model_routing.py` sem revisar validacao de modelos;
- nao remover `safe_path()` nem a sanitizacao de erro;
- nao mudar o formato dos modelos de dominio sem ajustar exportacao e testes.

## Partes criticas

- `EditorialOrchestrator.generate()`;
- `render_annotations()`;
- `build_composition_exports()`;
- `ProviderFactory`;
- `ScreenStateStore`;
- `GenerationApiAudit`;
- `ImageEditor` e `AdvancedImageAdjuster`.

## Responsabilidades do proximo desenvolvedor

- manter a estabilidade do fluxo editorial;
- concluir a integracao futura com PGC;
- preservar o comportamento dos providers e dos modelos;
- ampliar testes de regressao antes de tocar em exportacao ou IA;
- manter o editor manual de imagens e a revisao automatica coerentes com os contratos atuais.

## Checklist final

☐ Ambiente

☐ Dependencias

☐ Configuracoes

☐ Fluxo

☐ IA

☐ Editor

☐ HTML

☐ Exportacao

☐ Testes

☐ Pendencias

☐ Integracao futura

## Encerramento

O sistema atual esta organizado e funcional dentro do escopo observado no codigo. A continuidade deve preservar os contratos de dominio e a separacao entre interface, serviços e providers.

