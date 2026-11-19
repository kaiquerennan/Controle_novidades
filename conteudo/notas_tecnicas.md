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
