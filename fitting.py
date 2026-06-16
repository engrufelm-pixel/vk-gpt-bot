"""Примерочная: фотореалистичная визуализация ремонта через
gemini-2.5-flash-image (AiTunnel).

Операции (kind):
    "furniture" — убрать всю мебель и вещи (без материала)
    "wallpaper" — заменить обои (нужно фото обоев)
    "door"      — заменить дверь (нужно фото двери)
    "floor"     — положить ламинат/линолеум + плинтусы в тон (нужно фото пола)
    "ceiling"   — натяжной потолок по текстовому описанию (без материала)
"""

import ast
import base64
import io
import logging
from PIL import Image
from openai import OpenAI

import config

logger = logging.getLogger(__name__)

_client = OpenAI(
    api_key=config.AITUNNEL_API_KEY,
    base_url=config.AITUNNEL_BASE_URL,
    timeout=config.IMAGE_TIMEOUT,
    max_retries=2,
)


# ─────────────────────────────────────────────────────
# Промпты
# ─────────────────────────────────────────────────────

FURNITURE_REMOVAL_PROMPT = (
    "You have one image: a room interior photo.\n\n"
    "Generate a photorealistic image of the EXACT same room, but completely EMPTY — "
    "with ALL movable furniture and objects removed.\n\n"
    "REMOVE: all furniture (sofas, beds, tables, chairs, wardrobes, cabinets, shelves), "
    "rugs and carpets, decorations, plants, floor lamps, curtains, pictures and posters, "
    "TVs and appliances that are not built-in, and any clutter or personal items.\n\n"
    "KEEP UNCHANGED: walls and their finish, floor surface and material, ceiling, "
    "windows (with frames), doors, built-in radiators, baseboards, room geometry, "
    "perspective, camera angle and natural lighting.\n\n"
    "Realistically reconstruct the wall, floor and ceiling areas that were hidden behind "
    "the removed furniture, matching the surrounding surfaces.\n"
    "Result must look like a real photo of the same empty room, ready for renovation."
)

WALLPAPER_PROMPT = (
    "You have two images:\n"
    "• Image 1: a room interior photo\n"
    "• Image 2: a wallpaper sample (may be a close-up of the pattern)\n\n"
    "Generate a photorealistic image of the EXACT same room from Image 1, "
    "but with the wallpaper from Image 2 applied to all wall surfaces.\n\n"
    "MUST preserve unchanged:\n"
    "- Every piece of furniture (exact position, shape, color, texture)\n"
    "- Floor surface (exact material and color)\n"
    "- Ceiling (NOT a wall — do NOT apply wallpaper to ceiling)\n"
    "- Windows, doors, baseboards, trim\n"
    "- Lighting, shadows, camera angle, perspective\n\n"
    "ONLY change: flat vertical wall surfaces.\n"
    "Apply the EXACT pattern and colors from Image 2 to all visible walls.\n"
    "Result must look photorealistic, like a real interior photograph."
)

DOOR_PROMPT = (
    "You have two images:\n"
    "• Image 1: a room interior photo\n"
    "• Image 2: a door sample (a photo or close-up of a door design and color)\n\n"
    "Generate a photorealistic image of the EXACT same room from Image 1, "
    "but with the interior door replaced by the door from Image 2.\n\n"
    "Replace ONLY the existing door leaf and, if visible, its frame/casing — "
    "matching the style, material and color of Image 2.\n"
    "Fit the new door into the EXISTING doorway with correct size, perspective and realistic shadows.\n\n"
    "MUST preserve unchanged:\n"
    "- Walls and wallpaper, floor, ceiling\n"
    "- Every piece of furniture\n"
    "- Windows, baseboards, trim\n"
    "- Lighting, shadows, camera angle, perspective\n\n"
    "If there is no clearly visible door in Image 1, keep the image essentially unchanged.\n"
    "Result must look photorealistic, like a real interior photograph."
)

# Ламинат/линолеум + плинтусы автоматически в тон полу
LAMINATE_PROMPT = (
    "You have two images:\n"
    "• Image 1: a room interior photo\n"
    "• Image 2: a flooring sample (laminate or linoleum — possibly a close-up of the pattern)\n\n"
    "Generate a photorealistic image of the EXACT same room from Image 1, "
    "but with the floor covered by the flooring from Image 2, AND with the baseboards "
    "(skirting boards) replaced to MATCH the same material and color as the new floor.\n\n"
    "Apply the EXACT pattern, color, plank/strip direction and material from Image 2 to the "
    "entire visible floor, with realistic perspective, scale, lighting and contact shadows under furniture.\n"
    "Make the baseboards along the bottom of the walls match the new floor's material and color "
    "(same wood tone / shade).\n\n"
    "MUST preserve unchanged:\n"
    "- Walls and wallpaper, ceiling\n"
    "- Every piece of furniture (including legs and contact shadows)\n"
    "- Windows, doors, lighting, camera angle, perspective\n\n"
    "ONLY change: the floor surface and the baseboards.\n"
    "Result must look photorealistic, like a real interior photograph."
)

# Натяжной потолок по текстовому описанию ({desc})
CEILING_TEXT_PROMPT = (
    "You have one image: a room interior photo.\n\n"
    "Generate a photorealistic image of the EXACT same room, but with a new STRETCH CEILING "
    "(натяжной потолок) installed, described by the client as follows:\n"
    "\"{desc}\"\n\n"
    "Apply the described ceiling to the entire visible ceiling surface: respect the requested "
    "color, material/finish (matte, satin or glossy) and the requested lighting "
    "(recessed spotlights and/or a chandelier, and their approximate count and placement). "
    "If glossy is requested, add subtle realistic reflections. Integrate the lighting fixtures "
    "cleanly and flush with the ceiling, with realistic illumination and soft shadows in the room.\n\n"
    "MUST preserve unchanged:\n"
    "- Walls and wallpaper, floor\n"
    "- Every piece of furniture\n"
    "- Windows, doors, baseboards\n"
    "- Camera angle and perspective\n\n"
    "ONLY change: the ceiling surface and its lighting fixtures.\n"
    "If the description is vague, default to a clean white matte stretch ceiling with a few "
    "evenly spaced recessed spotlights.\n"
    "Result must look photorealistic, like a real interior photograph."
)

DEFAULT_CEILING_DESC = "белый матовый натяжной потолок с несколькими ровно расположенными точечными светильниками"

# Операции, которым нужно фото материала
PHOTO_PROMPTS: dict[str, str] = {
    "wallpaper": WALLPAPER_PROMPT,
    "door": DOOR_PROMPT,
    "floor": LAMINATE_PROMPT,
}


# ─────────────────────────────────────────────────────
# Вспомогательные функции
# ─────────────────────────────────────────────────────

def _to_jpeg(image_bytes: bytes, max_side: int) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    logger.info("JPEG %dx%d → %.1fKB", *img.size, len(buf.getvalue()) / 1024)
    return buf.getvalue()


def _data_url(image_bytes: bytes) -> dict:
    b64 = base64.b64encode(_to_jpeg(image_bytes, config.IMAGE_MAX_SIDE)).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def _extract_image(msg) -> bytes:
    """Достаёт сгенерированную картинку из msg.images (формат AiTunnel)."""
    raw = getattr(msg, "images", None)
    if raw is None:
        raise ValueError("msg.images отсутствует в ответе модели")
    items = raw if isinstance(raw, list) else ast.literal_eval(raw)
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("image_url", {})
        if isinstance(url, dict):
            url = url.get("url", "")
        if url.startswith("data:"):
            return base64.b64decode(url.split(",", 1)[1])
    raise ValueError(f"Нет image_url в msg.images. Получено: {str(items)[:200]}")


def _generate(base_bytes: bytes, prompt: str, material_bytes: bytes | None = None) -> bytes:
    """Базовая генерация: фото комнаты (+опц. фото материала) + текстовый промпт."""
    content: list[dict] = [_data_url(base_bytes)]
    if material_bytes is not None:
        content.append(_data_url(material_bytes))
    content.append({"type": "text", "text": prompt})

    logger.info("Отправляю в %s...", config.AITUNNEL_IMAGE_MODEL)
    response = _client.chat.completions.create(
        model=config.AITUNNEL_IMAGE_MODEL,
        messages=[{"role": "user", "content": content}],
        max_tokens=4096,
    )
    result = _extract_image(response.choices[0].message)
    logger.info("Готово, %.1fKB", len(result) / 1024)
    return result


# ─────────────────────────────────────────────────────
# Публичный API
# ─────────────────────────────────────────────────────

def run(kind: str, base_bytes: bytes,
        material_bytes: bytes | None = None,
        description: str | None = None) -> bytes:
    """Выполняет операцию ремонта над base_bytes и возвращает байты результата.

    kind ∈ {"furniture", "wallpaper", "door", "floor", "ceiling"}
    """
    if kind == "furniture":
        return _generate(base_bytes, FURNITURE_REMOVAL_PROMPT)

    if kind == "ceiling":
        desc = (description or "").strip() or DEFAULT_CEILING_DESC
        return _generate(base_bytes, CEILING_TEXT_PROMPT.format(desc=desc))

    if kind in PHOTO_PROMPTS:
        if material_bytes is None:
            raise ValueError(f"Для операции '{kind}' нужно фото материала")
        return _generate(base_bytes, PHOTO_PROMPTS[kind], material_bytes)

    raise ValueError(f"Неизвестная операция: {kind}")


# Обратная совместимость со старым кодом (фото-материальные операции)
def apply_material(room_bytes: bytes, material_bytes: bytes, kind: str) -> bytes:
    return run(kind, room_bytes, material_bytes=material_bytes)
