"""In-memory FSM пользователей VK-бота.

mode:
    None              — главное меню (стартовое состояние)
    "ask"             — режим AI-чата с консультантом

    Примерочная «Сделать ремонт на фото»:
    "remont_room"     — ждём фото комнаты
    "remont_menu"     — фото загружено, показано меню операций
    "wait_wallpaper"  — ждём фото обоев (одиночная операция)
    "wait_door"       — ждём фото двери
    "wait_floor"      — ждём фото пола (ламинат/линолеум)
    "wait_ceiling"    — ждём текстовое описание натяжного потолка
    "render"          — одиночная генерация идёт в фоне

    Пошаговый ремонт (визард):
    "step_offer"      — предлагаем текущий шаг («Сделать»/«Пропустить»)
    "step_wait"       — ждём ввод (фото или текст) для текущего шага
    "step_render"     — генерация текущего шага идёт в фоне

Доп. поля состояния (через get/set):
    "room_bytes"   — исходное фото комнаты (база для одиночных операций)
    "work_bytes"   — накопитель результата для пошагового ремонта
    "step_idx"     — индекс текущего шага визарда
    "last_result"  — байты последнего готового результата (для «Продолжить на результате»)
"""

_states: dict[int, dict] = {}


def _new() -> dict:
    return {"mode": None}


def get_state(user_id: int) -> dict:
    if user_id not in _states:
        _states[user_id] = _new()
    return _states[user_id]


# ── mode ───────────────────────────────────────────
def get_mode(user_id: int) -> str | None:
    return get_state(user_id).get("mode")


def set_mode(user_id: int, mode: str | None) -> None:
    get_state(user_id)["mode"] = mode


# ── произвольные поля ──────────────────────────────
def get(user_id: int, key: str, default=None):
    return get_state(user_id).get(key, default)


def set(user_id: int, key: str, value) -> None:
    get_state(user_id)[key] = value


def reset(user_id: int) -> None:
    _states[user_id] = _new()
