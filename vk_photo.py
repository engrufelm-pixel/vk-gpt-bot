"""Скачивание фото из VK-сообщений и загрузка готовых фото обратно."""

import io
import logging
from PIL import Image
import requests

logger = logging.getLogger(__name__)


def _normalize_to_jpeg(image_bytes: bytes) -> bytes:
    """VK upload иногда отвергает PNG/неизвестный формат — приводим к чистому JPEG."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def download_photo(attachment: dict) -> bytes:
    """Берёт самый большой размер из attachment['photo']['sizes'] и качает байты."""
    if attachment.get("type") != "photo":
        raise ValueError(f"Ожидался type=photo, получен: {attachment.get('type')}")

    sizes = attachment["photo"]["sizes"]
    # выбираем максимальный по площади
    best = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
    url = best["url"]

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.content


def upload_photo_to_messages(vk, user_id: int, image_bytes: bytes) -> str:
    """Загружает картинку как фото в личку и возвращает attachment-строку.

    Возвращает 'photo<owner_id>_<photo_id>' для поля attachment в messages.send.
    """
    upload_server = vk.photos.getMessagesUploadServer(peer_id=user_id)
    upload_url = upload_server["upload_url"]

    jpeg_bytes = _normalize_to_jpeg(image_bytes)
    logger.info("Загружаю в VK: %.1fKB JPEG", len(jpeg_bytes) / 1024)

    files = {"photo": ("result.jpg", jpeg_bytes, "image/jpeg")}
    raw = requests.post(upload_url, files=files, timeout=60)
    try:
        upload_resp = raw.json()
    except Exception as e:
        snippet = raw.text[:300] if raw.text else "<empty>"
        raise RuntimeError(
            f"VK upload вернул не-JSON (status={raw.status_code}): {snippet}"
        ) from e

    if "error" in upload_resp or not upload_resp.get("photo"):
        raise RuntimeError(f"VK upload error: {upload_resp}")

    saved = vk.photos.saveMessagesPhoto(
        photo=upload_resp["photo"],
        server=upload_resp["server"],
        hash=upload_resp["hash"],
    )
    item = saved[0]
    return f"photo{item['owner_id']}_{item['id']}"
