# 06 - Exportacao

## Estrutura de saida

A exportacao final e organizada em `workspace.export_dir`.

Arquivos observados:

- `novidade.json`;
- `novidade.html`;
- `novidade.xml`;
- `manifest.xml`;
- `prompt_utilizado.txt`;
- `emc_novidade_visual_ia.zip`;
- `capa/capa.png` quando solicitada;
- `novidade/imagens/*_original.png`;
- `novidade/imagens/*_tratada.png`;
- `novidade/imagens/*_final.png`;
- `novidade/anotacoes/*_anotacoes.json`.

## HTML legado

`services/html_export_service.py` gera o HTML final com:

- titulo;
- subtitulos;
- textos;
- listas;
- imagens;
- legenda;
- capa, se existente.

O exportador preserva o formato visual baseado em tags simples e filtro de seguranca.

## JSON

`build_composition_exports()` grava `novidade.json` com:

- origem;
- titulo;
- introducao;
- texto_novidade;
- blocos;
- imagens;
- imagens_fallback;
- capa;
- revisao.

`build_exports()` também grava um JSON legado de marcacoes para o caso simples de uma imagem.

## XML

`services/xml_export_service.py` gera:

- `novidade.xml`;
- `manifest.xml`.

`novidade.xml` contem:

- blocos;
- imagens referenciadas;
- capa;
- revisao;
- imagens fallback.

`manifest.xml` lista todos os arquivos exportados com:

- caminho;
- papel;
- mime type;
- tamanho;
- sha256.

## ZIP

O ZIP final e criado em:

- `emc_novidade_visual_ia.zip`

Ele agrupa o pacote completo da novidade.

## Arquivos temporarios

`SessionWorkspace` usa:

- `data/temp/`;
- `data/originals/`;
- `data/processed/`;
- `data/exports/`.

Os arquivos temporarios sao usados para:

- previews;
- cache de imagem;
- outputs intermediarios;
- persistencia por sessao.

