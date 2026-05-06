"""
VK-бот с GPT для сообщества по ремонту квартир.
Запуск: python bot.py
"""

import traceback
from openai import OpenAI
import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType

import config
from prompt import SYSTEM_PROMPT

# ── Инициализация VK ───────────────────────────────
vk_session = vk_api.VkApi(token=config.VK_TOKEN)
vk = vk_session.get_api()
longpoll = VkLongPoll(vk_session)

# ── Инициализация GPT-клиента ──────────────────────
gpt_cfg = config.get_gpt_config()
gpt_client = OpenAI(
    api_key=gpt_cfg["api_key"],
    base_url=gpt_cfg["base_url"],
)
GPT_MODEL = gpt_cfg["model"]

# ── Хранение истории диалогов (in-memory) ──────────
# Ключ — user_id, значение — список сообщений
conversations: dict[int, list[dict]] = {}


def get_history(user_id: int) -> list[dict]:
    """Возвращает историю диалога, создаёт если нет."""
    if user_id not in conversations:
        conversations[user_id] = []
    return conversations[user_id]


def trim_history(history: list[dict]) -> list[dict]:
    """Обрезает историю до MAX_HISTORY последних сообщений."""
    if len(history) > config.MAX_HISTORY:
        history[:] = history[-config.MAX_HISTORY:]
    return history


def ask_gpt(user_id: int, user_message: str) -> str:
    """Отправляет сообщение в GPT и возвращает ответ."""
    history = get_history(user_id)
    history.append({"role": "user", "content": user_message})
    trim_history(history)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    try:
        response = gpt_client.chat.completions.create(
            model=GPT_MODEL,
            messages=messages,
            max_tokens=config.MAX_TOKENS,
            temperature=config.TEMPERATURE,
        )
        answer = response.choices[0].message.content.strip()
    except Exception as e:
        traceback.print_exc()
        answer = (
            "Извините, произошла техническая ошибка. "
            "Попробуйте написать чуть позже или свяжитесь с нами напрямую 🙏"
        )

    history.append({"role": "assistant", "content": answer})
    trim_history(history)
    return answer


def send_message(user_id: int, text: str) -> None:
    """Отправляет сообщение пользователю VK."""
    # VK ограничивает длину сообщения — разбиваем если надо
    MAX_LEN = 4096
    while text:
        chunk = text[:MAX_LEN]
        text = text[MAX_LEN:]
        vk.messages.send(
            user_id=user_id,
            message=chunk,
            random_id=0,
        )


def main() -> None:
    print(f"✅ Бот запущен (провайдер: {config.GPT_PROVIDER}, модель: {GPT_MODEL})")
    print("Ожидание сообщений...")

    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW and event.to_me and event.text:
            user_id = event.user_id
            user_text = event.text.strip()

            if not user_text:
                continue

            print(f"[{user_id}] → {user_text}")

            answer = ask_gpt(user_id, user_text)

            print(f"[{user_id}] ← {answer[:120]}...")

            send_message(user_id, answer)


if __name__ == "__main__":
    main()