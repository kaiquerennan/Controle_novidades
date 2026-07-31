# 09 - Configuracao

## Arquivos de configuracao

- `core/config/settings.py`;
- `requirements.txt`;
- `executar.bat`;
- `instalar_dependencias.bat`;
- `verificar_ambiente.bat`.

## Variaveis e parametros principais

### Porta

- `PORT = 8510` em `app.py`.

### Base de imagens do HTML

- `HTML_IMAGE_BASE_PATH = '/app_dotnet/AppExtranet/imagens/'`

### Variaveis de ambiente de API

- `GEMINI_API_KEY`
- `OPENAI_API_KEY`

Essas chaves sao lidas na tela de configuracao de IA e nao sao persistidas no `screen_state.json`.

### Banco de ocorrencias (SQL Server)

O acesso ao banco que contem as ocorrencias usa `pyodbc` e e **somente
leitura**: a conexao e aberta com `readonly=True` e `OccurrenceDatabase` recusa
qualquer instrucao que nao comece com `SELECT` ou `WITH`.

Parametros aceitos, com precedencia para as variaveis de ambiente:

| Variavel | Campo | Observacao |
| --- | --- | --- |
| `EMC_DB_HOST` | `host` | Servidor SQL Server |
| `EMC_DB_PORT` | `port` | Padrao `1433` |
| `EMC_DB_NAME` | `database` | Nome do banco |
| `EMC_DB_USER` | `user` | Usuario com permissao de leitura |
| `EMC_DB_PASSWORD` | `password` | Fica somente em memoria quando vem do ambiente |
| `EMC_DB_ODBC_DRIVER` | `odbc_driver` | Em branco usa o driver mais recente instalado |

Sem variaveis de ambiente, a configuracao vem de `data/occurrence_db.local.json`,
gravado por `configurar_banco.py`. A senha e protegida pelo DPAPI da maquina,
como o token do WhatsApp, e o arquivo esta no `.gitignore`.

Comandos de apoio (sempre pelo Python da `.venv`):

```
.venv\Scripts\python.exe configurar_banco.py configurar --host SERVIDOR --banco BANCO --usuario USUARIO
.venv\Scripts\python.exe configurar_banco.py testar
.venv\Scripts\python.exe configurar_banco.py tabelas
.venv\Scripts\python.exe configurar_banco.py colunas NOME_DA_TABELA
.venv\Scripts\python.exe configurar_banco.py amostra NOME_DA_TABELA --linhas 5
```

## Pastas importantes

- `data/originals/` - imagens originais por sessao;
- `data/processed/` - imagens tratadas;
- `data/exports/` - exportacoes finais;
- `data/temp/` - previews e artefatos temporarios;
- `logs/` - logs da aplicacao;
- `core/prompts/` - prompts-base da IA.

## Dependencias

Declaradas em `requirements.txt`:

- `nicegui`;
- `Pillow`;
- `google-genai`;
- `openai`;
- `pyodbc`.

## Observacoes de configuracao

- `app.py` exige a `.venv` do projeto e reinicia a execucao dentro dela quando necessario;
- `ScreenStateStore` remove campos sensiveis antes de salvar;
- `ProviderFactory` escolhe o provider por nome;
- `model_routing.py` impede uso cruzado de modelos de texto e imagem.
