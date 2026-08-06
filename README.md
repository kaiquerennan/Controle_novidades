# EMC Central de Novidades

Aplicativo em Python e NiceGUI que transforma ocorrências de mudança em matérias
prontas para o Portal de Novidades da EMC.

O sistema lê as ocorrências direto do banco, monta o contexto da alteração, gera o
texto com IA seguindo o padrão editorial da EMC e entrega a matéria pronta para
revisão, edição e exportação.

---

## Requisitos

- Windows
- Python 3.13 ou superior
- Um driver ODBC de SQL Server instalado
- Chave de API do Gemini ou da OpenAI

---

## Instalação

```powershell
cd C:\EMC_Novidade_Visual_IA
python -m venv .venv
.\instalar_dependencias.bat
```

Depois copie `.env.example` para `.env` e preencha os valores:

```ini
GEMINI_API_KEY=sua-chave
OPENAI_API_KEY=sua-chave

EMC_DB_HOST=servidor
EMC_DB_PORT=1433
EMC_DB_NAME=banco
EMC_DB_USER=usuario
EMC_DB_PASSWORD=senha
```

O `.env` está no `.gitignore`. Variáveis definidas no terminal têm precedência
sobre o arquivo, e ele é lido **uma vez, na inicialização** — ao alterá-lo,
reinicie o servidor.

Para conferir o ambiente sem alterar nada:

```powershell
.\verificar_ambiente.bat
```

---

## Execução

```powershell
.\executar.bat
```

Acesse `http://127.0.0.1:8510`.

O `executar.bat` valida a `.venv`, as dependências e a porta antes de subir.
Também é possível rodar `.venv\Scripts\python.exe app.py` — o `app.py` se
redireciona sozinho para a `.venv` do projeto se for aberto com outro Python.

O projeto usa exclusivamente `C:\EMC_Novidade_Visual_IA`, a porta `8510` e uma
`.venv` própria. Não reutiliza ambientes, pacotes, serviços ou portas de outros
sistemas.

---

## Como funciona

A tela inicial é o **painel de ocorrências**: as ocorrências do tipo MUDANÇA dos
últimos 3 meses, lidas da view `VW_OCORRENCIAS_MUDANCA`. Cada uma mostra
solicitação e justificativa, e a busca filtra por número ou por qualquer texto da
ocorrência.

A partir daí:

1. **Selecione uma ocorrência.** O contexto da mudança é carregado
   automaticamente — sistema, módulo, caminho, solicitação, justificativa e
   atendimento.
2. **Adicione as imagens** (PNG, JPG, JPEG ou WEBP, até 15 MB cada).
3. **Clique em Gerar Novidade.** A IA escreve a matéria seguindo o padrão
   editorial e uma segunda passada revisa a fidelidade ao conteúdo original.
4. **Revise nas três abas**: Pré-visualização, Texto e HTML.
5. **Envie pelo WhatsApp** ou gere novamente, se necessário.

Ocorrências que não devem virar novidade podem ser excluídas do painel, com
confirmação. A exclusão é local: o banco nunca é alterado.

### As três abas do editor

| Aba | O que faz |
| --- | --- |
| Pré-visualização | a matéria como será publicada; imagens são reposicionadas arrastando |
| Texto | a matéria em texto corrido, editável — título na primeira linha, parágrafos separados por linha em branco, listas com `- ` |
| HTML | o código gerado, editável; quando editado, é ele que vai para a exportação |

### Menu lateral

- **Ocorrências de mudança** — o painel inicial
- **Novidades lançadas** — histórico do que já foi exportado, com download do HTML e do ZIP
- **WhatsApp** — vínculo da conta usada nos envios
- **Configurações** — provedor de IA, chave e modelos

---

## WhatsApp

O envio usa a biblioteca **Baileys**, que conversa com o WhatsApp pelo mesmo
protocolo do WhatsApp Web. Como o Baileys é uma biblioteca Node.js, ele roda em um
processo separado (`whatsapp_gateway/`) que ouve apenas em `127.0.0.1`. A aplicação
sobe esse processo sozinha quando necessário.

Instale as dependências uma única vez:

```powershell
.\instalar_whatsapp.bat
```

Depois abra **WhatsApp** no menu lateral e leia o QR com o celular, em
**Aparelhos conectados**. O vínculo fica salvo em `data/whatsapp_session/`, então
só é preciso repetir se a conta for desconectada no aparelho.

Com a conta vinculada, o botão **Enviar pelo WhatsApp** abre uma tela com a
mensagem já preenchida com o título da novidade e editável antes do envio. O
destino pode ser:

- **Contato** — o número do destinatário. Sem código do país, o número é tratado
  como brasileiro.
- **Grupo** — a lista dos grupos dos quais a conta participa, com busca pelo nome.
  A lista é lida do WhatsApp na primeira vez que a aba é aberta e fica guardada por
  um minuto; **Recarregar** força uma releitura. Grupos em que só administradores
  podem escrever aparecem marcados e o envio é bloqueado antes da tentativa.

---

## Banco de ocorrências

As ocorrências vêm de um **SQL Server** externo, acessado por `pyodbc` em modo
**somente leitura**: a conexão é aberta com `readonly=True` e qualquer instrução
que não comece com `SELECT` ou `WITH` é recusada antes de chegar ao servidor.

Além do `.env`, o acesso pode ser configurado por linha de comando, com a senha
protegida pelo DPAPI da máquina em `data/occurrence_db.local.json`:

```powershell
.venv\Scripts\python.exe configurar_banco.py configurar --host SERVIDOR --banco BANCO --usuario USUARIO
.venv\Scripts\python.exe configurar_banco.py testar
.venv\Scripts\python.exe configurar_banco.py tabelas
.venv\Scripts\python.exe configurar_banco.py colunas NOME_DA_TABELA
```

Quando o driver ODBC não é informado, o serviço usa o mais recente encontrado na
máquina. As variáveis `EMC_DB_*` têm precedência sobre o arquivo local.

---

## Sessões

Cada navegador tem a própria sessão, identificada por um cookie assinado. Texto,
imagens e matéria de um usuário não interferem nos de outro, o que permite mais de
uma pessoa usando o mesmo servidor.

O segredo que assina o cookie vem de `EMC_STORAGE_SECRET` ou é gerado uma vez em
`data/storage_secret.local`. Ele precisa ser estável entre reinícios.

---

## Estrutura

```
app.py                    interface NiceGUI e fluxo da tela
configurar_banco.py       CLI de configuração e diagnóstico do banco
components/               componentes de interface
core/config/              configurações e carregamento do .env
core/prompts/             prompts legados (não utilizados)
models/                   composição da matéria e contratos da IA
services/                 acesso ao banco, orquestração editorial e exportação
tests/                    testes automatizados
data/                     artefatos de sessão (fora do repositório)
```

O prompt efetivamente usado na geração está em
`services/editorial_orchestration_service.py`, nas constantes
`WRITER_ORGANIZER_PROMPT` e `REVIEWER_PROMPT`.

---

## Testes

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest
```

Os testes de interface usam a fixture `User` do NiceGUI e rodam sem navegador.

---

## Problemas comuns

| Sintoma | Causa provável |
| --- | --- |
| `did not find executable at ...python.exe` | a `.venv` foi criada em outra máquina; apague a pasta e recrie |
| `A porta 8510 está ocupada` | já há uma instância rodando; encerre antes de subir outra |
| Erro de cota ao gerar | o modelo escolhido não tem cota na chave; troque o Modelo de Texto em Configurações |
| Campo da chave vazio | o `.env` foi alterado depois que o servidor subiu; reinicie |
| Nenhuma ocorrência no painel | acesso ao banco não configurado; rode `configurar_banco.py testar` |
