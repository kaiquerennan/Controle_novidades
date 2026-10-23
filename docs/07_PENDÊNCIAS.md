# 07 - Pendencias

Itens de evolucao e limitacoes observadas:

- integracao com PGC ainda nao existe no codigo atual;
- nao foi possivel confirmar suporte a multiusuario concorrente fora do controle por sessao da aplicacao;
- a revisao automatica continua dependente da disponibilidade dos providers de IA;
- a qualidade do resultado depende dos modelos configurados;
- o cache de analise de imagem e local e por hash de arquivo;
- a exportacao depende da estrutura local de pastas do workspace.

Limitacoes conhecidas:

- se o provedor nao responder, o sistema mostra erro tecnico sanitizado;
- o editor de imagens opera sobre coordenadas normalizadas, mas ainda depende da renderizacao de Pillow para a saida final;
- nao foi possivel confirmar durante a analise do codigo qualquer integracao nativa com PGC, SSO ou banco remoto;
- cerca de um quarto das novidades de ITENS_NOVIDADES_EMC nao tem vinculo com a ocorrencia (OCO_NUMERO), entao o filtro "apenas as que ainda nao viraram novidade" nao enxerga a materia publicada sem vinculo; a conferencia por semelhanca de texto (`services/novelty_similarity_service.py`) cobre parte desse ponto cego, avisando antes do lancamento, e por ser comparacao de texto pode errar nas duas direcoes.

Melhorias futuras possiveis:

- integrar PGC;
- formalizar contrato de entrada e saida com sistemas externos;
- ampliar validacoes de edicao;
- adicionar mais testes de regressao de exportacao;
- melhorar a observabilidade externa, caso necessario.

