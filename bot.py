"""
VK-бот для сообщества по ремонту квартир.
- Главное меню: «Задать вопрос» (AI-консультант) и «Сделать ремонт на фото» (примерочная).
- Примерочная: фото комнаты -> меню операций (мебель, обои, дверь, потолок, пол)
  или пошаговый ремонт, накапливающий результат.
Запуск: python bot.py
"""

import threading
import traceback
import logging

from openai import OpenAI
import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType

import config
import states
import keyboards
import fitting
from prompt import SYSTEM_PROMPT
from vk_photo import download_photo, upload_photo_to_messages

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Инициализация VK ───────────────────────────────
vk_session = vk_api.VkApi(token=config.VK_TOKEN)
vk = vk_session.get_api()
longpoll = VkLongPoll(vk_session)

# ── Инициализация GPT-клиента (текстовый ассистент) ─
gpt_cfg = config.get_gpt_config()
gpt_client = OpenAI(
    api_key=gpt_cfg["api_key"],
    base_url=gpt_cfg["base_url"],
)
GPT_MODEL = gpt_cfg["model"]

# ── История диалогов AI-ассистента (in-memory) ─────
conversations: dict[int, list[dict]] = {}


# ─────────────────────────────────────────────────────
# AI-ассистент (текстовый чат) — НЕ ТРОГАЕМ ЛОГИКУ
# ─────────────────────────────────────────────────────

def get_history(user_id: int) -> list[dict]:
    if user_id not in conversations:
        conversations[user_id] = []
    return conversations[user_id]


def trim_history(history: list[dict]) -> list[dict]:
    if len(history) > config.MAX_HISTORY:
        history[:] = history[-config.MAX_HISTORY:]
    return history


def ask_gpt(user_id: int, user_message: str) -> str:
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
    except Exception:
        traceback.print_exc()
        answer = (
            "Извините, произошла техническая ошибка. "
            "Попробуйте написать чуть позже или свяжитесь с нами напрямую."
        )

    history.append({"role": "assistant", "content": answer})
    trim_history(history)
    return answer


# ─────────────────────────────────────────────────────
# Отправка сообщений
# ─────────────────────────────────────────────────────

MAX_LEN = 4096


def send(user_id: int, text: str, keyboard: str | None = None, attachment: str | None = None) -> None:
    """Отправляет текст (с разбиением по 4096) + опц. клавиатуру/вложение."""
    if not text:
        text = " "
    chunks = [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)]
    for i, chunk in enumerate(chunks):
        kwargs = {"user_id": user_id, "message": chunk, "random_id": 0}
        # Клавиатура и вложение прикрепляем к последнему чанку
        if i == len(chunks) - 1:
            if keyboard is not None:
                kwargs["keyboard"] = keyboard
            if attachment is not None:
                kwargs["attachment"] = attachment
        vk.messages.send(**kwargs)


# ─────────────────────────────────────────────────────
# Конфигурация операций примерочной
# ─────────────────────────────────────────────────────

# kind -> описание операции
#   input: None  — генерация без доп. ввода
#          "photo" — нужно фото материала
#          "text"  — нужно текстовое описание
#
# TODO (Ozon): для "wallpaper" и "floor" на шаге запроса фото материала
# добавить альтернативу «Выбрать из каталога Ozon» — спарсенные варианты
# обоев/ламината отдавать пользователю на выбор, а выбранную картинку
# использовать как material_bytes. Парсятся только обои и ламинат.
OPS = {
    "furniture": {
        "label": "Убрать мебель и вещи",
        "input": None,
        "doing": "Убираю мебель и вещи...",
    },
    "wallpaper": {
        "label": "Заменить обои",
        "input": "photo",
        "ask": "Пришлите фото обоев, которые хотите примерить.",
        "doing": "Меняю обои...",
    },
    "door": {
        "label": "Заменить дверь",
        "input": "photo",
        "ask": "Пришлите фото двери, которую хотите примерить.",
        "doing": "Меняю дверь...",
    },
    "floor": {
        "label": "Ламинат / линолеум",
        "input": "photo",
        "ask": (
            "Пришлите фото напольного покрытия (ламинат или линолеум).\n"
            "Плинтусы подберу автоматически в тон полу."
        ),
        "doing": "Укладываю пол и плинтусы...",
    },
    "ceiling": {
        "label": "Натяжной потолок",
        "input": "text",
        "ask": (
            "Опишите натяжной потолок: цвет, материал (матовый, сатиновый или глянцевый), "
            "светильники или люстра и их количество.\n"
            "Например: «белый матовый, 6 точечных светильников».\n"
            "Или нажмите «Стандартный потолок»."
        ),
        "doing": "Натягиваю потолок...",
    },
}

# Подпись кнопки меню -> kind
OP_BY_BUTTON = {meta["label"]: kind for kind, meta in OPS.items()}

# Порядок шагов пошагового ремонта: уборка -> стены -> полы -> двери -> потолки
STEP_ORDER = ["furniture", "wallpaper", "floor", "door", "ceiling"]

CEILING_STD_WORD = "стандартный потолок"
SKIP_WORD = "пропустить"

GREETING = (
    "Здравствуйте! Это бот компании «ГладкоСтелимОбои».\n\n"
    "Выберите, чем помочь:\n"
    "• «Задать вопрос» — отвечу как мастер: цены, сроки, технология.\n"
    "• «Сделать ремонт на фото» — пришлите фото комнаты, и я покажу, "
    "как она будет выглядеть после ремонта: новые обои, пол, дверь, потолок "
    "или всё сразу пошагово."
)


# ─────────────────────────────────────────────────────
# Меню и переходы
# ─────────────────────────────────────────────────────

def show_main_menu(user_id: int, text: str = GREETING) -> None:
    states.reset(user_id)
    send(user_id, text, keyboard=keyboards.MAIN_MENU)


def start_remont(user_id: int) -> None:
    """Старт примерочной — просим фото комнаты."""
    states.reset(user_id)
    states.set_mode(user_id, "remont_room")
    send(
        user_id,
        "Пришлите фото комнаты, в которой хотите сделать ремонт "
        "(чтобы хорошо были видны стены, пол и потолок).",
        keyboard=keyboards.CANCEL_ONLY,
    )


def show_operations_menu(user_id: int, text: str, result: bool = False) -> None:
    states.set_mode(user_id, "remont_menu")
    kb = keyboards.REMONT_RESULT_MENU if result else keyboards.REMONT_MENU
    send(user_id, text, keyboard=kb)


# ─────────────────────────────────────────────────────
# Одиночные операции
# ─────────────────────────────────────────────────────

def begin_single_op(user_id: int, kind: str) -> None:
    op = OPS[kind]
    base = states.get(user_id, "room_bytes")
    if not base:
        show_main_menu(user_id, "Сначала пришлите фото комнаты. Нажмите «Сделать ремонт на фото».")
        return

    if op["input"] is None:
        # Без доп. ввода — сразу генерируем
        states.set_mode(user_id, "render")
        send(user_id, f"{op['doing']} Это займёт около 30 секунд.")
        threading.Thread(
            target=run_single_job, args=(user_id, kind, base),
            kwargs={}, daemon=True,
        ).start()
        return

    if op["input"] == "photo":
        states.set_mode(user_id, f"wait_{kind}")
        send(user_id, op["ask"], keyboard=keyboards.CANCEL_ONLY)
        return

    if op["input"] == "text":
        states.set_mode(user_id, "wait_ceiling")
        send(user_id, op["ask"], keyboard=keyboards.CEILING_INPUT)


def run_single_job(user_id: int, kind: str, base: bytes,
                   material: bytes | None = None, description: str | None = None) -> None:
    """Одиночная генерация в фоне. Результат применяется к исходному фото."""
    op = OPS[kind]
    try:
        result = fitting.run(kind, base, material_bytes=material, description=description)
        attachment = upload_photo_to_messages(vk, user_id, result)
        states.set(user_id, "last_result", result)
        show_after_result(
            user_id,
            f"Готово! «{op['label']}».\n\n"
            "Можно продолжить на этом результате, выбрать другую операцию "
            "или загрузить новое фото.",
            attachment,
        )
    except Exception as e:
        logger.exception("Ошибка одиночной операции kind=%s user=%s", kind, user_id)
        show_operations_menu(
            user_id,
            f"Не удалось выполнить «{op['label']}»: {type(e).__name__}.\n"
            "Попробуйте ещё раз или выберите другую операцию.",
        )


def show_after_result(user_id: int, text: str, attachment: str) -> None:
    states.set_mode(user_id, "remont_menu")
    send(user_id, text, keyboard=keyboards.REMONT_RESULT_MENU, attachment=attachment)


# ─────────────────────────────────────────────────────
# Пошаговый ремонт (визард)
# ─────────────────────────────────────────────────────

def start_wizard(user_id: int) -> None:
    base = states.get(user_id, "room_bytes")
    if not base:
        show_main_menu(user_id, "Сначала пришлите фото комнаты. Нажмите «Сделать ремонт на фото».")
        return
    states.set(user_id, "work_bytes", base)
    states.set(user_id, "step_idx", 0)
    send(
        user_id,
        "Запускаю пошаговый ремонт. На каждом шаге можно «Сделать» или «Пропустить».\n"
        "Каждый шаг применяется к результату предыдущего — как настоящий ремонт.",
    )
    offer_step(user_id)


def offer_step(user_id: int) -> None:
    idx = states.get(user_id, "step_idx", 0)
    if idx >= len(STEP_ORDER):
        finish_wizard(user_id)
        return
    kind = STEP_ORDER[idx]
    op = OPS[kind]
    states.set_mode(user_id, "step_offer")
    send(
        user_id,
        f"Шаг {idx + 1}/{len(STEP_ORDER)}: {op['label']}.\nСделать или пропустить?",
        keyboard=keyboards.STEP_OFFER,
    )


def begin_step_input(user_id: int) -> None:
    """Пользователь нажал «Сделать» на текущем шаге."""
    idx = states.get(user_id, "step_idx", 0)
    kind = STEP_ORDER[idx]
    op = OPS[kind]
    base = states.get(user_id, "work_bytes")

    if op["input"] is None:
        states.set_mode(user_id, "step_render")
        send(user_id, f"{op['doing']}")
        threading.Thread(
            target=run_step_job, args=(user_id, kind, base), daemon=True,
        ).start()
        return

    if op["input"] == "photo":
        states.set_mode(user_id, "step_wait")
        send(user_id, op["ask"], keyboard=keyboards.STEP_PHOTO_INPUT)
        return

    if op["input"] == "text":
        states.set_mode(user_id, "step_wait")
        send(user_id, op["ask"], keyboard=keyboards.STEP_CEILING_INPUT)


def run_step_job(user_id: int, kind: str, base: bytes,
                 material: bytes | None = None, description: str | None = None) -> None:
    """Генерация одного шага визарда. Результат становится новой базой."""
    idx = states.get(user_id, "step_idx", 0)
    op = OPS[kind]
    try:
        result = fitting.run(kind, base, material_bytes=material, description=description)
        states.set(user_id, "work_bytes", result)
        attachment = upload_photo_to_messages(vk, user_id, result)
        send(user_id, f"Шаг {idx + 1} готов: «{op['label']}».", attachment=attachment)
    except Exception as e:
        logger.exception("Ошибка шага kind=%s user=%s", kind, user_id)
        send(user_id, f"Шаг «{op['label']}» не удался ({type(e).__name__}), пропускаю.")
    finally:
        states.set(user_id, "step_idx", idx + 1)
        offer_step(user_id)


def finish_wizard(user_id: int) -> None:
    result = states.get(user_id, "work_bytes")
    if result:
        states.set(user_id, "last_result", result)
    show_operations_menu(
        user_id,
        "Пошаговый ремонт завершён. Итог — на фото выше.\n\n"
        "Можно продолжить на результате, повторить отдельную операцию "
        "или загрузить новое фото.",
        result=True,
    )


# ─────────────────────────────────────────────────────
# Обработка фото
# ─────────────────────────────────────────────────────

def extract_photo_attachment(event) -> dict | None:
    """Достаёт первое фото из вложений события Long Poll."""
    attachments = event.attachments
    if not attachments:
        return None
    if attachments.get("attach1_type") != "photo":
        return None

    msg = vk.messages.getById(message_ids=event.message_id, extended=0)
    if not msg.get("items"):
        return None
    for att in msg["items"][0].get("attachments", []):
        if att.get("type") == "photo":
            return att
    return None


def get_photo_bytes(user_id: int, event) -> bytes | None:
    photo_att = extract_photo_attachment(event)
    if photo_att is None:
        send(user_id, "Пришлите именно фото (не файл и не ссылку).", keyboard=keyboards.CANCEL_ONLY)
        return None
    try:
        return download_photo(photo_att)
    except Exception:
        logger.exception("Не удалось скачать фото от user_id=%s", user_id)
        send(user_id, "Не удалось загрузить фото. Попробуйте ещё раз.", keyboard=keyboards.CANCEL_ONLY)
        return None


def handle_photo(user_id: int, event, mode: str) -> None:
    """Маршрутизация входящего фото в зависимости от режима."""
    # Шаг визарда, ожидающий фото
    if mode == "step_wait":
        idx = states.get(user_id, "step_idx", 0)
        kind = STEP_ORDER[idx]
        if OPS[kind]["input"] != "photo":
            send(user_id, "Для этого шага нужно текстовое описание, а не фото.",
                 keyboard=keyboards.STEP_CEILING_INPUT)
            return
        photo_bytes = get_photo_bytes(user_id, event)
        if photo_bytes is None:
            return
        base = states.get(user_id, "work_bytes")
        states.set_mode(user_id, "step_render")
        send(user_id, f"{OPS[kind]['doing']}")
        threading.Thread(
            target=run_step_job, args=(user_id, kind, base),
            kwargs={"material": photo_bytes}, daemon=True,
        ).start()
        return

    # Фото комнаты
    if mode == "remont_room":
        photo_bytes = get_photo_bytes(user_id, event)
        if photo_bytes is None:
            return
        states.set(user_id, "room_bytes", photo_bytes)
        show_operations_menu(
            user_id,
            "Фото комнаты получено. Что сделаем?\n"
            "Выберите операцию или «Ремонт пошагово».",
        )
        return

    # Фото материала для одиночной операции
    photo_kind = {
        "wait_wallpaper": "wallpaper",
        "wait_door": "door",
        "wait_floor": "floor",
    }.get(mode)
    if photo_kind:
        photo_bytes = get_photo_bytes(user_id, event)
        if photo_bytes is None:
            return
        base = states.get(user_id, "room_bytes")
        states.set_mode(user_id, "render")
        send(user_id, f"{OPS[photo_kind]['doing']} Это займёт около 30 секунд.")
        threading.Thread(
            target=run_single_job, args=(user_id, photo_kind, base),
            kwargs={"material": photo_bytes}, daemon=True,
        ).start()
        return

    # Фото вне контекста — мягко возвращаем в меню
    show_main_menu(user_id, "Чтобы сделать ремонт на фото, нажмите «Сделать ремонт на фото».")


# ─────────────────────────────────────────────────────
# Главный диспетчер
# ─────────────────────────────────────────────────────

START_WORDS = {"начать", "старт", "/start", "меню", "главное меню", "start"}
BACK_WORDS = {"назад", "отмена", "главное меню", "выйти"}


def handle_event(event) -> None:
    user_id = event.user_id
    text = (event.text or "").strip()
    text_lower = text.lower()
    mode = states.get_mode(user_id)

    # Идёт генерация — не обрабатываем входящие, кроме отмены
    if mode in ("render", "step_render"):
        if text_lower in BACK_WORDS:
            show_main_menu(user_id, "Хорошо, возвращаю в главное меню. (Текущая генерация всё ещё может прийти.)")
            return
        send(user_id, "Уже генерирую, пожалуйста, дождитесь результата (около 30 секунд).")
        return

    # Фото
    has_photo = bool(event.attachments) and event.attachments.get("attach1_type") == "photo"
    if has_photo:
        handle_photo(user_id, event, mode or "")
        return

    if not text:
        return

    # Старт / главное меню
    if text_lower in START_WORDS:
        show_main_menu(user_id)
        return

    # Назад / отмена — из любого состояния
    if text_lower in BACK_WORDS:
        show_main_menu(user_id, "Возвращаю в главное меню.")
        return

    # ── Главное меню — выбор раздела ──
    if mode is None:
        if text == "Задать вопрос":
            states.set_mode(user_id, "ask")
            send(
                user_id,
                "Задавайте вопрос — отвечу как мастер по ремонту. "
                "Чтобы вернуться в меню, нажмите «Главное меню».",
                keyboard=keyboards.BACK_ONLY,
            )
            return
        if text == "Сделать ремонт на фото":
            start_remont(user_id)
            return
        show_main_menu(user_id)
        return

    # ── Режим AI-чата ──
    if mode == "ask":
        answer = ask_gpt(user_id, text)
        send(user_id, answer, keyboard=keyboards.BACK_ONLY)
        return

    # ── Меню операций примерочной ──
    if mode == "remont_menu":
        if text == "Продолжить на результате":
            last = states.get(user_id, "last_result")
            if last:
                states.set(user_id, "room_bytes", last)
                show_operations_menu(user_id, "Хорошо, продолжаем на этом результате. Что дальше?")
            else:
                show_operations_menu(user_id, "Нет готового результата. Выберите операцию.")
            return
        if text == "Другое фото":
            start_remont(user_id)
            return
        if text == "Ремонт пошагово":
            start_wizard(user_id)
            return
        kind = OP_BY_BUTTON.get(text)
        if kind:
            begin_single_op(user_id, kind)
            return
        show_operations_menu(user_id, "Выберите операцию кнопкой ниже.")
        return

    # ── Одиночная операция ждёт фото, а пришёл текст ──
    if mode in ("wait_wallpaper", "wait_door", "wait_floor"):
        send(user_id, "Жду фото. Пришлите изображение или нажмите «Отмена».",
             keyboard=keyboards.CANCEL_ONLY)
        return

    # ── Одиночная операция: текстовое описание потолка ──
    if mode == "wait_ceiling":
        base = states.get(user_id, "room_bytes")
        desc = None if text_lower == CEILING_STD_WORD else text
        states.set_mode(user_id, "render")
        send(user_id, f"{OPS['ceiling']['doing']} Это займёт около 30 секунд.")
        threading.Thread(
            target=run_single_job, args=(user_id, "ceiling", base),
            kwargs={"description": desc}, daemon=True,
        ).start()
        return

    # ── Визард: предложение шага ──
    if mode == "step_offer":
        if text == "Сделать":
            begin_step_input(user_id)
            return
        if text_lower == SKIP_WORD:
            idx = states.get(user_id, "step_idx", 0)
            states.set(user_id, "step_idx", idx + 1)
            offer_step(user_id)
            return
        offer_step(user_id)
        return

    # ── Визард: ждём ввод шага, пришёл текст ──
    if mode == "step_wait":
        idx = states.get(user_id, "step_idx", 0)
        kind = STEP_ORDER[idx]
        if text_lower == SKIP_WORD:
            states.set(user_id, "step_idx", idx + 1)
            offer_step(user_id)
            return
        if OPS[kind]["input"] == "text":
            base = states.get(user_id, "work_bytes")
            desc = None if text_lower == CEILING_STD_WORD else text
            states.set_mode(user_id, "step_render")
            send(user_id, f"{OPS[kind]['doing']}")
            threading.Thread(
                target=run_step_job, args=(user_id, kind, base),
                kwargs={"description": desc}, daemon=True,
            ).start()
            return
        # Шаг ждёт фото, а пришёл текст
        send(user_id, "Жду фото. Пришлите изображение, нажмите «Пропустить» или «Отмена».",
             keyboard=keyboards.STEP_PHOTO_INPUT)
        return

    # Неизвестное состояние — сброс
    show_main_menu(user_id)


# ─────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────

def main() -> None:
    logger.info(
        "Бот запущен (текст: %s/%s, картинки: %s)",
        config.GPT_PROVIDER, GPT_MODEL, config.AITUNNEL_IMAGE_MODEL,
    )
    logger.info("Ожидание сообщений...")

    for event in longpoll.listen():
        if event.type != VkEventType.MESSAGE_NEW or not event.to_me:
            continue

        try:
            handle_event(event)
        except Exception:
            logger.exception("Ошибка в обработке события от user_id=%s", getattr(event, "user_id", "?"))
            try:
                send(
                    event.user_id,
                    "Произошла внутренняя ошибка. Возвращаю в главное меню.",
                    keyboard=keyboards.MAIN_MENU,
                )
                states.reset(event.user_id)
            except Exception:
                pass


if __name__ == "__main__":
    main()
