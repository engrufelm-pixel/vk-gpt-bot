import os
from dotenv import load_dotenv

load_dotenv()

# ── VK ──────────────────────────────────────────────
VK_TOKEN = os.getenv("VK_TOKEN")

# ── GPT провайдер ───────────────────────────────────
GPT_PROVIDER = os.getenv("GPT_PROVIDER", "aitunnel")  # "aitunnel" или "openai"

# ── AI Tunnel ───────────────────────────────────────
AITUNNEL_API_KEY = os.getenv("AITUNNEL_API_KEY", "")
AITUNNEL_BASE_URL = os.getenv("AITUNNEL_BASE_URL", "https://api.aitunnel.ru/v1")
AITUNNEL_MODEL = os.getenv("AITUNNEL_MODEL", "gpt-4o-mini")

# ── OpenAI (резерв) ────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ── Общие настройки ────────────────────────────────
MAX_HISTORY = 20  # сколько сообщений помнить в диалоге
MAX_TOKENS = 1500
TEMPERATURE = 0.7


def get_gpt_config() -> dict:
    """Возвращает конфиг для текущего провайдера."""
    if GPT_PROVIDER == "openai":
        return {
            "api_key": OPENAI_API_KEY,
            "base_url": OPENAI_BASE_URL,
            "model": OPENAI_MODEL,
        }
    # по умолчанию — aitunnel
    return {
        "api_key": AITUNNEL_API_KEY,
        "base_url": AITUNNEL_BASE_URL,
        "model": AITUNNEL_MODEL,
    }