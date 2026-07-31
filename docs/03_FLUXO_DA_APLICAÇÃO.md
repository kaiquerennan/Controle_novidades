# 03 - Fluxo da Aplicacao

## Visao geral

O fluxo observado no codigo e:

entrada

↓

criação da novidade

↓

organização

↓

revisão

↓

correção

↓

segunda revisão

↓

associação automática das imagens

↓

geração opcional da capa

↓

visualização

↓

HTML

↓

exportação

## Etapas e classes envolvidas

### 1. Entrada

Responsabilidade principal em `app.py`.

Componentes:

- upload de imagens;
- textarea de texto bruto;
- selecao de provedor;
- selecao de modelos de texto e imagem;
- opcao de capa.

Classes e funcoes envolvidas:

- `SessionWorkspace.save_original()`;
- `ScreenStateStore.save()`;
- `generation_fingerprint()`;
- `persist_screen_state()`.

### 2. Criação da novidade

Acontece em `EditorialOrchestrator.generate()`.

Etapas:

- prepara imagens;
- calcula cache e idempotencia;
- extrai unidades editoriais com `extract_editorial_units()`;
- monta `editorial_input` com `structured_source_prompt()`;
- chama o provider para organizar o texto;
- converte o retorno em `CompositionDocument`.

### 3. Organização

Executada pelo provider via `generate_novelty_plan()`.

Para o fluxo editorial, o plano resultante passa por:

- `document_from_editorial_plan()`;
- `_ensure_source_structure()`;
- `missing_system_paths()`.

### 4. Revisão

Executada por `review_novelty()`.

O resultado e armazenado em `CompositionDocument.review`.

Se a revisao aponta problemas, o sistema monta um payload de correcao com:

- blocos alvo;
- trechos fonte;
- correcoes necessarias.

### 5. Correcao

Quando a revisao nao aprova, `EditorialOrchestrator.generate()` chama novamente o provider em modo de correcao.

O texto corrigido e mesclado por `_merge_corrected_document()`.

### 6. Segunda revisão

Apos a correcao, o documento e revisado novamente por `review_novelty()`.

### 7. Associação automática das imagens

O sistema usa:

- `ImageAnalysisCache`;
- `planned.imagens`;
- `document.used_image_ids()`;
- fallback de imagens nao posicionadas.

Quando alguma imagem nao entra naturalmente no documento, ela e adicionada ao final como fallback.

### 8. Geração opcional da capa

Se o usuario marcar a opcao de capa:

- `EditorialOrchestrator.generate()` chama `provider.generate_cover()`;
- o arquivo vai para `workspace.export_dir / 'capa' / 'capa.png'`.

### 9. Visualização

Responsavel principal:

- `render_novelty_preview()` em `components/novelty_preview.py`.

A visualizacao permite:

- rearranjo de blocos e imagens;
- abertura do editor de imagens;
- edicao textual e manual da composicao.

### 10. HTML

O HTML final e gerado por:

- `build_novelty_html()` em `services/html_export_service.py`.

### 11. Exportação

O pacote final e montado por:

- `build_composition_exports()` em `services/image_service.py`.

Arquivos gerados no pacote:

- `novidade.json`;
- `novidade.html`;
- `novidade.xml`;
- `manifest.xml`;
- `prompt_utilizado.txt`;
- imagens tratadas e finais;
- anotacoes por imagem;
- opcionalmente a capa;
- `emc_novidade_visual_ia.zip`.

