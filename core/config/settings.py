from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_IMAGE_PATH = PROJECT_ROOT / 'core' / 'prompts' / 'prompt_imagem.txt'
PROMPT_NOVELTY_PATH = PROJECT_ROOT / 'core' / 'prompts' / 'prompt_geracao_novidade.txt'
HTML_IMAGE_BASE_PATH = '/app_dotnet/AppExtranet/imagens/'

PROVIDER_LABELS = {
    'gemini': 'Gemini',
    'openai': 'OpenAI',
}

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
}

API_KEY_ENVIRONMENTS = {
    'gemini': 'GEMINI_API_KEY',
    'openai': 'OPENAI_API_KEY',
}

WHATSAPP_GRAPH_API_VERSION = 'v25.0'
WHATSAPP_TEMPLATE_NAME = 'emc_novidade_eddie'
WHATSAPP_TEMPLATE_LANGUAGE = 'pt_BR'

# Modelos de imagem não produzem a orientação textual; mantenha essa escolha centralizada.
OPENAI_ORIENTATION_MODEL = TEXT_MODELS['openai']
