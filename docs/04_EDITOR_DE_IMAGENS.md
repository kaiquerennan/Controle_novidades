# 04 - Editor de Imagens

## Visao geral

O editor manual de imagens e composto por `ImageEditor` e `AdvancedImageAdjuster`.

Ele permite criar e editar anotacoes sobre a imagem tratada, com suporte a:

- seta;
- circulo;
- retangulo;
- texto;
- numero;
- destaque.

## Funcionalidades locais

### `components/image_editor.py`

`ImageEditor` controla:

- rascunho atual;
- primeiro ponto para ferramentas de dois cliques;
- confirmacao da anotacao;
- cancelamento do rascunho;
- selecao e movimentacao de anotacoes;
- entrega da lista de itens para preview.

Ferramentas de dois pontos:

- `arrow`;
- `rectangle`;
- `circle`;
- `highlight`.

### `models/annotation.py`

`Annotation` normaliza:

- coordenadas entre 0 e 1;
- espessura;
- tamanho de fonte;
- opacidade;
- numero.

`AnnotationDocument` guarda:

- itens atuais;
- pilha de undo;
- pilha de redo.

### `services/image_service.py`

`render_annotations()` usa Pillow para desenhar:

- `draw.rectangle()`;
- `draw.ellipse()`;
- `draw.rounded_rectangle()`;
- `draw.line()`;
- `draw.text()`;
- `draw.polygon()`.

O helper `_normalized_box()` centraliza a normalizacao do bounding box antes das formas com dois pontos.

## Fluxo do editor

1. O usuario escolhe a imagem em `AdvancedImageAdjuster`.
2. A interface apresenta a imagem por `ui.interactive_image()`.
3. O clique chama `ImageEditor.click()`.
4. Para ferramentas de dois pontos, o primeiro clique guarda o ponto inicial.
5. O segundo clique fecha a anotacao e cria o rascunho.
6. O usuario confirma com `confirm()`.
7. A anotacao vai para `AnnotationDocument`.
8. `save()` chama `render_annotations()` e grava a imagem final.

## Como salvar

`AdvancedImageAdjuster.save()`:

- renderiza a imagem com `render_annotations()`;
- atualiza `annotated` e `final`;
- salva snapshot da sessao da imagem;
- chama `on_change()`.

## Como desfazer e refazer

`ImageEditor.document.undo()` e `redo()` operam sobre snapshots em memoria.

No componente de interface:

- o botao `Desfazer` chama `document.undo()`;
- o botao `Refazer` chama `document.redo()`.

## Como as anotacoes sao armazenadas

Cada anotacao e salva como `Annotation` e serializada por:

- `Annotation.to_dict()`;
- `AnnotationDocument.to_list()`.

Quando o sistema persiste o estado da tela ou exporta a novidade, as anotacoes vao em JSON.

## Separacao entre funcionalidades locais e com IA

Funcionalidades locais:

- desenho manual;
- movimentacao;
- confirmacao;
- cancelamento;
- undo/redo;
- salvamento;
- exportacao das anotacoes.

Funcionalidades com IA:

- nao existe uso de IA dentro do editor manual em si.

