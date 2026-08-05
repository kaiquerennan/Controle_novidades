from pathlib import Path

from .environment import load_environment

# O .env do projeto é lido aqui, no unico modulo de configuracao importado por
# todo o restante, para que a chave de IA e o acesso ao banco fiquem disponiveis
# tanto na aplicacao quanto nos scripts de linha de comando.
load_environment()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_IMAGE_PATH = PROJECT_ROOT / 'core' / 'prompts' / 'prompt_imagem.txt'
PROMPT_NOVELTY_PATH = PROJECT_ROOT / 'core' / 'prompts' / 'prompt_geracao_novidade.txt'
HTML_IMAGE_BASE_PATH = '/app_dotnet/AppExtranet/imagens/'

PROVIDER_LABELS = {
    'gemini': 'Gemini',
    'openai': 'OpenAI',
    'deepseek': 'DeepSeek',
}

# Provedor pré-selecionado quando a aplicação abre sem preferência salva.
DEFAULT_PROVIDER = 'deepseek'

# A DeepSeek expoe uma API compativel com a da OpenAI, mas apenas em Chat
# Completions e somente para texto: nao gera nem interpreta imagens.
DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
DEEPSEEK_TEXT_MODEL = 'deepseek-chat'

OPENAI_TEXT_MODEL = 'gpt-5-mini'
OPENAI_IMAGE_MODEL = 'gpt-image-1.5'

DEFAULT_MODELS = {
    'gemini': {
        'text': 'gemini-2.5-pro',
        'image': 'gemini-3.1-flash-image',
    },
    'openai': {
        'text': OPENAI_TEXT_MODEL,
        'image': OPENAI_IMAGE_MODEL,
    },
    'deepseek': {
        'text': DEEPSEEK_TEXT_MODEL,
        # A DeepSeek nao gera imagens; a capa fica indisponivel neste provedor.
        'image': '',
    },
}

TEXT_MODELS = {
    provider: models['text'] for provider, models in DEFAULT_MODELS.items()
}

IMAGE_MODELS = {
    provider: models['image'] for provider, models in DEFAULT_MODELS.items()
}

IMAGE_MODEL_MARKERS = {
    'gemini': ('-image',),
    'openai': ('gpt-image',),
    'deepseek': (),
}

API_KEY_ENVIRONMENTS = {
    'gemini': 'GEMINI_API_KEY',
    'openai': 'OPENAI_API_KEY',
    'deepseek': 'DEEPSEEK_API_KEY',
}

# Banco de ocorrências (SQL Server, via pyodbc). O processo lê as ocorrências
# para sugerir novidades; nada é gravado por esta aplicação.
OCCURRENCE_DB_ENVIRONMENTS = {
    'host': 'EMC_DB_HOST',
    'port': 'EMC_DB_PORT',
    'database': 'EMC_DB_NAME',
    'user': 'EMC_DB_USER',
    'password': 'EMC_DB_PASSWORD',
    'odbc_driver': 'EMC_DB_ODBC_DRIVER',
}

OCCURRENCE_DB_DEFAULT_PORT = 1433
OCCURRENCE_DB_CONNECT_TIMEOUT_SECONDS = 10
OCCURRENCE_DB_QUERY_LIMIT = 500

WHATSAPP_GRAPH_API_VERSION = 'v25.0'
WHATSAPP_TEMPLATE_NAME = 'emc_novidade_eddie'
WHATSAPP_TEMPLATE_LANGUAGE = 'pt_BR'

# Modelos de imagem não produzem a orientação textual; mantenha essa escolha centralizada.
OPENAI_ORIENTATION_MODEL = TEXT_MODELS['openai']
