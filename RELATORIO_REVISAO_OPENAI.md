# Relatório técnico — integração OpenAI

Data da revisão: 29/07/2026 (America/Sao_Paulo)

## Resultado

A integração foi corrigida e validada ponta a ponta. A aplicação está ativa em `http://127.0.0.1:8510`, responde HTTP 200, importa o cliente moderno do SDK e realizou uma geração real com `gpt-image-1.5`.

## Causa raiz

O arquivo `app.py` havia sido aberto diretamente pelo Explorer. A cadeia observada era `Explorer -> py.exe -> Python 3.13 global`, resultando no interpretador:

`C:\Users\sonara.cotta\AppData\Local\Programs\Python\Python313\python.exe`

Esse Python global carregava `openai 0.28.1`, versão que não exporta a classe `OpenAI`. Por isso `from openai import OpenAI` falhava. O launcher `executar.bat` já apontava para a `.venv`, mas o duplo clique em `app.py` contornava o launcher.

## Interpretador e módulo finais

- `sys.executable`: `C:\EMC_Novidade_Visual_IA\.venv\Scripts\python.exe`
- `sys.prefix`: `C:\EMC_Novidade_Visual_IA\.venv`
- `openai.__file__`: `C:\EMC_Novidade_Visual_IA\.venv\Lib\site-packages\openai\__init__.py`
- `openai.__version__`: `2.48.0`
- PID atual da porta 8510: `12300`
- Comando ativo: `C:\EMC_Novidade_Visual_IA\.venv\Scripts\python.exe C:\EMC_Novidade_Visual_IA\app.py`

`dir(openai)` no ambiente utilizado:

```text
['APIConnectionError', 'APIError', 'APIResponse', 'APIResponseValidationError', 'APIStatusError', 'APITimeoutError', 'AssistantEventHandler', 'AsyncAPIResponse', 'AsyncAssistantEventHandler', 'AsyncAzureOpenAI', 'AsyncBedrockOpenAI', 'AsyncClient', 'AsyncOpenAI', 'AsyncStream', 'Audio', 'AuthenticationError', 'AzureOpenAI', 'BadRequestError', 'BaseModel', 'BedrockOpenAI', 'ChatCompletion', 'Client', 'Completion', 'ConflictError', 'ContentFilterFinishReasonError', 'Customer', 'DEFAULT_CONNECTION_LIMITS', 'DEFAULT_MAX_RETRIES', 'DEFAULT_TIMEOUT', 'DefaultAioHttpClient', 'DefaultAsyncHttpx2Client', 'DefaultAsyncHttpxClient', 'DefaultHttpx2Client', 'DefaultHttpxClient', 'Deployment', 'Edit', 'Embedding', 'Engine', 'ErrorObject', 'File', 'FineTune', 'FineTuningJob', 'HttpxBinaryResponseContent', 'Image', 'InternalServerError', 'InvalidWebhookSignatureError', 'LengthFinishReasonError', 'Model', 'Moderation', 'NOT_GIVEN', 'NoneType', 'NotFoundError', 'NotGiven', 'OAuthError', 'Omit', 'OpenAI', 'OpenAIError', 'PermissionDeniedError', 'ProxiesTypes', 'RateLimitError', 'ReconnectingEvent', 'ReconnectingOverrides', 'RequestOptions', 'Stream', 'Timeout', 'Transport', 'UnprocessableEntityError', 'VERSION', 'WebSocketConnectionClosedError', 'WebSocketQueueFullError', '_AmbiguousModuleClientUsageError', '_ApiType', '_AzureModuleClient', '_BedrockModuleClient', '_ModuleClient', '__all__', '__annotations__', '__builtins__', '__cached__', '__doc__', '__file__', '__loader__', '__locals', '__name', '__name__', '__package__', '__path__', '__spec__', '__title__', '__version__', '_azure', '_base_client', '_bedrock', '_bedrock_api_key', '_client', '_compat', '_constants', '_exceptions', '_files', '_has_azure_ad_credentials', '_has_azure_credentials', '_has_openai_credentials', '_httpx', '_httpx2', '_legacy_response', '_load_client', '_models', '_module_client', '_normalize_httpx_url', '_os', '_provider', '_qs', '_reset_client', '_response', '_setup_logging', '_streaming', '_t', '_te', '_types', '_utils', '_version', 'admin', 'admin_api_key', 'annotations', 'api_key', 'api_type', 'api_version', 'audio', 'auth', 'azure_ad_token', 'azure_ad_token_provider', 'azure_endpoint', 'base_url', 'batches', 'bedrock_token_provider', 'beta', 'chat', 'completions', 'containers', 'conversations', 'default_headers', 'default_query', 'embeddings', 'evals', 'file_from_path', 'files', 'fine_tuning', 'http_client', 'images', 'lib', 'max_retries', 'models', 'moderations', 'not_given', 'omit', 'organization', 'override', 'project', 'providers', 'pydantic_function_tool', 'realtime', 'resources', 'responses', 'skills', 'timeout', 'types', 'uploads', 'vector_stores', 'version', 'videos', 'webhook_secret', 'webhooks']
```

## Instalações encontradas

| Ambiente | SDK OpenAI | Exporta `OpenAI` | Utilização |
|---|---:|---:|---|
| `C:\EMC_Novidade_Visual_IA\.venv` | 2.48.0 | Sim | Ambiente final da aplicação |
| Python 3.13 global | 0.28.1 | Não | Causou o erro; não foi alterado |
| `C:\Automacao\.venv313` | 2.8.1 | Sim | Ambiente de outro projeto; não utilizado |

O comando nu `python` no PATH apontava para `C:\Automacao\.venv313\Scripts\python.exe`, portanto também não deve ser usado neste projeto.

## Shadowing e imports legados

- Nenhum `openai.py` ou diretório `openai/` foi encontrado no projeto fora da `.venv`.
- Nenhum uso legado de `openai.ChatCompletion.create`, `openai.Image`, `openai.Completion` ou equivalente do SDK 0.x foi encontrado.
- O único provider OpenAI usa `from openai import OpenAI`, `OpenAI(api_key=...)`, Responses API, `client.images.edit(...)` e `client.images.generate(...)`.
- Todos os usos produtivos estão no padrão do SDK 2.x.

## Dependências e launchers

- `requirements.txt` agora exige exatamente `openai==2.48.0`.
- Não existem `pyproject.toml`, `setup.py`, `setup.cfg`, `Pipfile` ou `poetry.lock` no projeto.
- `executar.bat` usa somente `.venv\Scripts\python.exe` e agora valida interpretador, import e versão 2.48.0 antes de iniciar.
- `instalar_dependencias.bat` instala somente na `.venv` e valida o SDK após a instalação.
- `verificar_ambiente.bat` exibe `sys.executable`, versão e origem do módulo OpenAI.
- `app.py` redireciona automaticamente uma abertura direta para o Python da `.venv`, eliminando a rota que reproduzia o erro.

## Arquivos analisados

- `app.py`, `README.md`, `requirements.txt`, `.venv/pyvenv.cfg`
- `executar.bat`, `instalar_dependencias.bat`, `verificar_ambiente.bat`
- `services/providers/openai_provider.py`, `provider_factory.py`, `base_provider.py`, `exceptions.py`
- `services/model_routing.py`, `services/novelty_generation_service.py`
- `core/config/settings.py`, `core/config/__init__.py`
- Os 13 módulos em `tests/`
- `logs/aplicativo.log`
- Instalações `site-packages` do projeto, do Python global e de `C:\Automacao\.venv313`

## Arquivos modificados

- `requirements.txt`
- `executar.bat`
- `instalar_dependencias.bat`
- `verificar_ambiente.bat`
- `app.py`
- `README.md`

O provider OpenAI não precisou ser modificado, pois sua implementação já estava correta.

## Validações executadas

- `py_compile`: aprovado para `app.py` e `openai_provider.py`.
- `pip check`: nenhuma dependência quebrada.
- Suíte de regressão: 103 testes executados, 103 aprovados.
- Import real: `from openai import OpenAI` aprovado.
- Criação real do cliente: `OpenAI(api_key=...)` aprovada sem chamada de rede.
- Assinaturas locais do SDK confirmaram os parâmetros usados em `images.edit` e `images.generate`, incluindo `input_fidelity`, `output_format`, `size` e `gpt-image-1.5`.
- Aplicação reiniciada: HTTP 200 em `127.0.0.1:8510`.
- Geração externa real: uma imagem PNG 1024x1024 foi retornada por `gpt-image-1.5`; 126.613 bytes decodificados; `REAL_IMAGE_GENERATION=OK`. A imagem foi validada somente em memória e não persistida.

## Confirmações finais

- Versão final: `openai==2.48.0`.
- Interpretador final: `C:\EMC_Novidade_Visual_IA\.venv\Scripts\python.exe`.
- `from openai import OpenAI`: funciona corretamente.
- Criação do cliente OpenAI: funciona corretamente.
- Geração com GPT Image 1.5: funciona corretamente em teste real.
- Nenhuma biblioteca global foi instalada, removida ou atualizada.
- Nenhuma outra dependência foi atualizada.

Referências oficiais consultadas: [SDK Python oficial](https://github.com/openai/openai-python) e [GPT Image 1.5](https://developers.openai.com/api/docs/models/gpt-image-1.5).