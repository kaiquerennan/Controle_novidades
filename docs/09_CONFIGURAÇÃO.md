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
- `openai`.

## Observacoes de configuracao

- `app.py` exige a `.venv` do projeto e reinicia a execucao dentro dela quando necessario;
- `ScreenStateStore` remove campos sensiveis antes de salvar;
- `ProviderFactory` escolhe o provider por nome;
- `model_routing.py` impede uso cruzado de modelos de texto e imagem.

