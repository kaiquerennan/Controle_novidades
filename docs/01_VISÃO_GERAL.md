# 01 - Visao Geral

O EMC Central de Novidades e uma aplicacao web em Python, baseada em NiceGUI, para transformar texto bruto e imagens em uma novidade editorial pronta para visualizacao, revisao e exportacao.

O problema que o sistema resolve e a organizacao de conteudo tecnico ou operacional em uma estrutura editorial coerente. A aplicacao recebe texto, imagens e configuracoes de IA, organiza o material, aplica revisao de fidelidade, permite correcao manual e exporta os resultados em formatos reutilizaveis.

Funcionalidades existentes observadas no codigo:

- upload de uma ou mais imagens;
- armazenamento local da sessao;
- geracao automatica da novidade por IA;
- revisao automatica de fidelidade;
- correcao automatica quando a revisao pede ajustes;
- segunda revisao apos correcao;
- possibilidade de gerar capa ilustrativa;
- pre-visualizacao da novidade;
- editor de texto e de posicao de blocos;
- editor manual de imagens com anotacoes;
- exportacao para HTML, XML, JSON e ZIP;
- persistencia de estado de tela;
- controle de concorrencia por sessao.

Funcionalidades futuras previstas no pedido de handover:

- integracao com PGC.

Nao foi possivel confirmar durante a analise do codigo qualquer implementacao de PGC, entao isso deve ser tratado como evolucao externa ao projeto atual.

Situacao atual do projeto:

- o fluxo principal esta funcional e organizado em etapas;
- ha suporte para OpenAI e Gemini;
- os providers estao isolados em uma fabrica;
- existe persistencia local de sessao e cache de analise de imagem;
- a exportacao gera pacote final com arquivos auxiliares;
- os testes cobrem os fluxos principais de edicao, exportacao e integracao.

