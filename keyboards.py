"""VK keyboards в формате JSON для messages.send."""

import json


def _kb(rows: list[list[dict]], one_time: bool = False) -> str:
    return json.dumps(
        {"one_time": one_time, "buttons": rows},
        ensure_ascii=False,
    )


def _btn(label: str, color: str = "secondary") -> dict:
    return {
        "action": {"type": "text", "label": label, "payload": json.dumps({"button": label})},
        "color": color,
    }


# ── Главное меню ───────────────────────────────────
MAIN_MENU = _kb([
    [_btn("Задать вопрос", "primary"), _btn("Сделать ремонт на фото", "primary")],
])

# ── Меню операций примерочной (после загрузки фото комнаты) ─
REMONT_MENU = _kb([
    [_btn("Начать ремонт пошагово", "primary")],
    [_btn("Убрать мебель и вещи")],
    [_btn("Заменить обои"), _btn("Заменить дверь")],
    [_btn("Натяжной потолок"), _btn("Ламинат / линолеум")],
    [_btn("Другое фото"), _btn("Главное меню")],
])

# ── То же меню + «Продолжить на результате» (после готовой генерации) ─
REMONT_RESULT_MENU = _kb([
    [_btn("Повторить", "primary"), _btn("Продолжить на результате", "positive")],
    [_btn("Начать ремонт пошагово", "primary")],
    [_btn("Убрать мебель и вещи")],
    [_btn("Заменить обои"), _btn("Заменить дверь")],
    [_btn("Натяжной потолок"), _btn("Ламинат / линолеум")],
    [_btn("Другое фото"), _btn("Главное меню")],
])

# ── Пошаговый ремонт ───────────────────────────────
STEP_OFFER = _kb([
    [_btn("Сделать", "positive"), _btn("Пропустить")],
    [_btn("Отмена", "negative")],
])

STEP_PHOTO_INPUT = _kb([
    [_btn("Пропустить")],
    [_btn("Отмена", "negative")],
])

STEP_CEILING_INPUT = _kb([
    [_btn("Стандартный потолок")],
    [_btn("Пропустить")],
    [_btn("Отмена", "negative")],
])

# ── Ввод для одиночных операций ────────────────────
CEILING_INPUT = _kb([
    [_btn("Стандартный потолок")],
    [_btn("Отмена", "negative")],
])

BACK_ONLY = _kb([
    [_btn("Главное меню")],
])

CANCEL_ONLY = _kb([
    [_btn("Отмена", "negative")],
])

EMPTY = json.dumps({"buttons": [], "one_time": True}, ensure_ascii=False)
