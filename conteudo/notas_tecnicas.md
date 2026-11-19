# Notas técnicas

Este arquivo alimenta a tela "Notas técnicas" do aplicativo.
Tudo o que estiver ANTES da primeira linha que começa com "## " é ignorado
(inclusive estas instruções), então pode deixá-las aqui.

Como escrever uma nota:

    ## NT 2025.002 - Reforma Tributária (IBS/CBS)
    Data: 01/10/2025
    Link: https://www.nfe.fazenda.gov.br/
    Sistema: NF-e / NFC-e
    Impacto: Alto

    Texto livre da nota. Aceita **negrito**, *itálico*, listas com "-"
    e links como [este](https://www.emcsistemas.com.br).

Regras:
- Cada nota começa com "## " no início da linha, seguido do título.
- As linhas logo abaixo do título, até a primeira linha em branco, são campos
  no formato "Campo: valor". Todos são opcionais.
  - Data: dd/mm/aaaa. Ordena a lista, a mais recente primeiro.
  - Link: vira o botão "Abrir nota".
  - Qualquer outro campo (Sistema, Impacto, Vigência...) aparece como etiqueta.
- Depois da linha em branco vem o texto da nota.
- Salve o arquivo em UTF-8 (o padrão do Bloco de Notas e do VS Code).

Escreva as notas abaixo desta linha, sem os 4 espaços do exemplo.

## TESTE - 🔴 NT 2026.005 v1.00 – NF-e (modelo 55), NFC-e (modelo 65): Reforma Tributária do Consumo – Simples Nacional (atualizações na NF-e e NFC-e)
Data: 19/11/2026
Link: https://www.nfe.fazenda.gov.br/portal/exibirArquivo.aspx?conteudo=HInG7WD5Sj8=
Impacto: Afeta
Homologação: Até 26/10/2026 (campos/grupos/regras do Simples Nacional)
Produção: 16/11/2026 (já vigente)

**Resumo:** NT cria campos e grupos para contribuintes do Simples Nacional na NF-e/NFC-e no contexto da Reforma Tributária (tpRegimeIBSCBS, cAtivSN, gTribSN/gIBSSN/gCBSSN e totais), com 4 novas regras de rejeição, e um evento fiscal (412140) gerado automaticamente pelo Fisco com implementação futura sem data definida. A produção dos campos/regras já entrou em vigor em 16/11/2026, antes da data de hoje.

**O que afeta a EMC:**

- **C21a (tpRegimeIBSCBS)** – NF-e, NFC-e, Cadastro (CRT). Ação: incluir C21a na emissão conforme CRT e situação de opção/pendência pelo regime regular do IBS/CBS
- **I17d (cAtivSN)** – NF-e, NFC-e, Cadastro de produtos. Ação: incluir campo por item e permitir seleção do código de atividade SN
- **Grupo UB (UB115a gTribSN; UB115b gIBSSN com UB115c pIBSSN/UB115d vIBSSN; UB115e gCBSSN com UB115f pCBSSN/UB115g vCBSSN)** – NF-e, NFC-e, Posto (exceção monofásico). Ação: implementar matriz de preenchimento por regime/CRT/tpRegimeIBSCBS; tratar exceção CST 620 (gIBSCBSMono, sem gTribSN) e MEI (sem grupo IBSCBS)
- **Totalizadores W59h (gTribSN), W59i (vIBSSN), W59j (vCBSSN)** – NF-e, NFC-e. Ação: somar vIBSSN/vCBSSN dos itens nos totais W59i/W59j e validar consistência item-total antes de transmitir, sob risco das rejeições 1275, 1276, 1277, 1284
- **Devolução/crédito/débito (finNFe=4; finNFe=5 tpNFCredito=03/06; finNFe=6 tpNFDebito=09)** – NF-e (devolução, notas de débito/crédito). Ação: copiar grupo IBSCBS/gTribSN do item da nota referenciada ao gerar devolução/crédito/débito por recusa, sem recalcular

**Para verificar:**

- **Evento 412140 (Tributação do Simples Nacional, tpAutor=5)** – confirmar se a EMC precisa consultar/exibir esse evento gerado automaticamente pelo Fisco em alguma tela; não há emissão pela EMC (autor é sempre o Fisco); implementação futura, sem data definida
