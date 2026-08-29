"""
Renders published (status='published') remarks from the SQLite store
(db.py) into the static "bare array" JSON files the viewer reads
(<PUBLISH_DIR>/<docId>/remarks/page_<NNN>.json), and computes the sha256
used as serverPageSha for optimistic locking.

PUBLISH_DIR is empty by default (publication disabled) -- see config.py.
"""

import hashlib
import json
import logging
import os
import sys
import tempfile
from typing import Any, Dict, List

# Общий модуль категорий лежит в scripts/, а на sys.path у контейнера только
# scripts/api (см. scripts/api/Dockerfile). Репозиторий скопирован целиком,
# поэтому каталог достаточно добавить руками.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import annotation_categories  # noqa: E402
import config
import db

logger = logging.getLogger("redpen.api")

_SHA_JSON_KWARGS = dict(ensure_ascii=False, separators=(",", ":"), sort_keys=True)


#: Обратное отображение вида замечания в имена, действовавшие до
#: переименования сущности. Нужно ровно в одном месте — во входе
#: compute_page_sha (см. render_page).
_LEGACY_KIND_BY_KIND = {v: k for k, v in db.LEGACY_KINDS.items()}


def legacy_kind(kind: str) -> str:
    """`major`/`minor` в прежние `main`/`comment`."""
    return _LEGACY_KIND_BY_KIND.get(kind, kind)


def _render_item(ann: Dict[str, Any], draft: bool = False, with_tags: bool = True) -> Dict[str, Any]:
    item: Dict[str, Any] = {"id": ann["remarkId"], "text": ann["text"]}
    if with_tags:
        item["kind"] = ann["kind"]
    else:
        # Вход compute_page_sha: заморожен в прежних именах, см. render_page.
        item["annType"] = legacy_kind(ann["kind"])
    if ann["coordX"] is not None and ann["coordY"] is not None:
        item["coords"] = [ann["coordX"], ann["coordY"]]
    if not with_tags:
        # render_page() path: the sha input stays frozen, category included.
        return item
    # `status` is canonical in the DB; the static file mirrors it as a tag so
    # the viewer has one uniform thing to filter on (?tags= / ?notags=).
    tags = list(ann.get("tags") or [])
    if draft:
        item["draft"] = True
        tags = ["draft"] + tags
    # Категория — своё поле: ровно одно значение на аннотацию, всегда присутствует.
    # Дополнительно зеркалим её тегом `cat:<slug>`, чтобы даром работали
    # ?tags=/?notags= и фильтр в кабинете. Тег производный: авторские `cat:*`
    # запрещены в db.normalize_tag.
    #
    # Дефолтную категорию тегом НЕ зеркалим: 'other' — это «приём не назначен»,
    # и сегодня такой тег стоял бы на всех аннотациях сразу, то есть не отбирал
    # бы ничего. Заодно сохраняется смысл «тегов нет вовсе» (tags: [] в PUT).
    category = annotation_categories.normalize_category(ann.get("category"))
    item["category"] = category
    tags = [t for t in tags if not t.startswith(annotation_categories.CAT_PREFIX)]
    if category != annotation_categories.DEFAULT_CATEGORY:
        tags.append(annotation_categories.category_tag(category))
    if tags:
        item["tags"] = tags
    return item


def render_page(doc_id: str, page_num: str) -> List[Dict[str, Any]]:
    """Bare array of published remarks for a page, in the legacy format:
    {id, text, annType[, coords]} -- no tags, no drafts.

    This is deliberately frozen: compute_page_sha() runs on it, and that hash is
    the editor's optimistic lock (main._current_page_sha). Adding fields here
    would 409 every editor session open across a deploy, and make draft/tag
    edits collide with unrelated ones. The file on disk comes from
    render_page_static() instead.

    Отсюда же и легаси-имена `annType`/`main`/`comment`: они намеренно пережили
    переименование сущности в «замечание». Сменить их — значит сдвинуть хеш и
    выдать 409 всем открытым сессиям редактора разом, ничего не дав взамен:
    наружу этот массив не отдаётся, его видит только блокировка."""
    return [
        _render_item(ann, with_tags=False)
        for ann in db.list_page_remarks(doc_id, page_num, include_deleted=False)
    ]


def render_page_static(doc_id: str, page_num: str) -> List[Dict[str, Any]]:
    """What actually gets written to page_<NNN>.json: published AND draft
    remarks in one array, each carrying its tags. Drafts additionally get
    "draft": true (kept for older viewers) and a leading "draft" tag.

    The viewer hides drafts by default and reveals them per URL parameter; see
    getTagFilter() in templates/js/main.js."""
    rendered = [
        _render_item(ann, draft=False)
        for ann in db.list_page_remarks(doc_id, page_num, include_deleted=False)
    ]
    rendered += [_render_item(ann, draft=True) for ann in db.list_page_drafts(doc_id, page_num)]
    return rendered


def compute_page_sha(rendered: List[Dict[str, Any]]) -> str:
    payload = json.dumps(rendered, **_SHA_JSON_KWARGS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _page_file_path(doc_id: str, page_num: str, dirname: str = "remarks") -> str:
    return os.path.join(config.PUBLISH_DIR, doc_id, dirname, f"page_{page_num}.json")


#: Каталог, куда замечания писались до переименования. Дублирующая запись живёт,
#: пока в ходу старые адреса и уже розданные офлайн-копии; снимается в фазе 6
#: (тогда же включается 301 в nginx и каталог удаляется с тома).
LEGACY_PAGE_DIRNAME = "annotations"


def _atomic_write_json(target: str, rendered: List[Dict[str, Any]]) -> None:
    """Atomically write `rendered` as pretty JSON to `target`, world-readable."""
    target_dir = os.path.dirname(target)
    os.makedirs(target_dir, exist_ok=True)
    data_bytes = json.dumps(rendered, ensure_ascii=False, indent=2).encode("utf-8")
    fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix="._tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data_bytes)
            tmp.flush()
            os.fsync(tmp.fileno())
        # mkstemp() creates the file mode 0600 (owner-only); this directory
        # is served directly by nginx (a different uid), so it must be
        # world-readable like a normal checked-out file.
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, target)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def render_page_html(doc_id: str, page_num: str) -> bool:
    """Перерисовать HTML страницы читателя. True, если файл записан.

    Без этого шага публикация не доходит до читателя: постраничный просмотрщик
    ничего не загружает (инвариант офлайна) и берёт аннотации из инлайнового
    блока `redpen-page-data`, вшитого в HTML на сборке. JSON рядом с ним читает
    только старый SPA и офлайн-бандл.

    Тихо возвращает False, когда рисовать нечем: в `PUBLISH_DIR` нет собранного
    сайта (dev без тома, тесты) или страницы нет в манифесте. Публикация из-за
    этого падать не должна — запись в БД уже состоялась.

    `page_html` импортируется лениво: он тянет за собой сборочные модули
    (`blog`, `chapters`), и API не должен от них зависеть на старте."""
    try:
        import page_html
    except Exception:
        logger.warning("render_page_html: page_html is unavailable; "
                       "reader pages will stay stale until the next site build")
        return False

    doc_dir = os.path.join(config.PUBLISH_DIR, doc_id)
    try:
        written = page_html.build_single_page(doc_dir, f"page_{page_num}")
    except Exception:
        # Никакая ошибка рендера не должна ронять публикацию: JSON — канон, он
        # уже записан. Живой пример, ради которого это написано: каталог
        # <doc>/pages/ принадлежит root (его создаёт rsync content-sync), а API
        # работает под uid 10001 — PermissionError здесь обрушивал publish_all
        # целиком, хотя JSON записывался нормально.
        logger.error("render_page_html failed doc_id=%s page_num=%s",
                     doc_id, page_num, exc_info=True)
        return False
    return written is not None


def publish_page(doc_id: str, page_num: str) -> bool:
    """Atomically write the rendered bare array for a page to PUBLISH_DIR, and
    re-render the reader's HTML for that page.
    Returns False (without raising) if publication is disabled or fails -- the
    DB write already succeeded, and the volume can be repaired later via
    publish_all()."""
    if not config.PUBLISH_DIR:
        return False

    try:
        rendered = render_page_static(doc_id, page_num)
        _atomic_write_json(_page_file_path(doc_id, page_num), rendered)
        _atomic_write_json(
            _page_file_path(doc_id, page_num, LEGACY_PAGE_DIRNAME), rendered)

        # HTML рисуется после JSON и намеренно не влияет на возвращаемое
        # значение: JSON — канон публикации, а страница читателя может
        # отсутствовать (нет собранного сайта в томе). Ошибку здесь мы хотим
        # видеть в логе, но не хотим ронять ею публикацию.
        render_page_html(doc_id, page_num)
    except Exception:
        logger.error("publish_page failed doc_id=%s page_num=%s", doc_id, page_num, exc_info=True)
        return False
    return True


def publish_all() -> Dict[str, int]:
    """Republish every page that has at least one remark row. Used by the
    admin endpoint and on startup to self-heal the volume."""
    pages = db.list_pages()
    failed = 0
    for doc_id, page_num in pages:
        if not publish_page(doc_id, page_num):
            failed += 1
    return {"pages": len(pages), "failed": failed}
