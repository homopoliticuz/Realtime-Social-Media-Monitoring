"""Импорт материалов, правомерно предоставленных пользователем.

Поддерживаются JSON (список объектов или ``{"items": [...]}``), CSV с
заголовком и простой текст (материалы разделяются пустой строкой).
Поля: ``text`` (обязательно), ``url``, ``platform``, ``published_at``,
``source_name``, ``context``. Основание получения (``legal_basis``)
указывается для всей партии и сохраняется в каждой карточке.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json

from .connectors.base import RawItem, to_iso

MAX_ITEMS = 1000


class ImportError_(ValueError):
    pass


def _item(obj: dict, legal_basis: str, uploader: str) -> RawItem | None:
    text = str(obj.get("text") or "").strip()
    if not text:
        return None
    url = str(obj.get("url") or "").strip()
    platform = str(obj.get("platform") or "Импорт").strip()[:40]
    ext = hashlib.sha1((url or text).encode("utf-8")).hexdigest()[:20]
    return RawItem(
        platform=platform,
        external_id=f"import:{ext}",
        url=url or f"import://{ext}",
        text=text[:20000],
        published_at=to_iso(obj.get("published_at")) if obj.get("published_at") else None,
        source_name=str(obj.get("source_name") or "Материал предоставлен пользователем")[:200],
        source_kind="import",
        author_kind="provided",
        context=(str(obj.get("context"))[:2000] if obj.get("context") else None),
        provenance="user_provided",
        legal_basis=legal_basis,
        meta={"uploaded_by": uploader},
    )


def parse(content: str, fmt: str, legal_basis: str, uploader: str) -> list[RawItem]:
    if not legal_basis or len(legal_basis.strip()) < 10:
        raise ImportError_("Укажите основание получения материалов (не менее 10 символов): жалоба, запрос, договор и т. п.")
    fmt = (fmt or "").lower()
    rows: list[dict] = []
    if fmt == "json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ImportError_(f"Некорректный JSON: {exc}") from exc
        if isinstance(data, dict):
            data = data.get("items", [data])
        if not isinstance(data, list):
            raise ImportError_("Ожидается список материалов")
        rows = [r for r in data if isinstance(r, dict)]
    elif fmt == "csv":
        rows = list(csv.DictReader(io.StringIO(content)))
    else:
        rows = [{"text": block} for block in content.split("\n\n") if block.strip()]
    if len(rows) > MAX_ITEMS:
        raise ImportError_(f"Слишком много материалов за один раз (максимум {MAX_ITEMS})")
    items = [i for i in (_item(r, legal_basis.strip(), uploader) for r in rows) if i]
    if not items:
        raise ImportError_("Не найдено ни одного материала с текстом")
    return items
