"""
Словарь действий над замечанием и вычисление состава изменения.

Журнал `remark_history` до появления этого модуля отвечал на вопрос «как была
сделана запись» (`create` / `update` / `delete` / `revert` / `import` /
`backfill`), но не на вопрос «что изменилось»: одно сохранение через
`PUT /api/editor/...` записывалось как `update` независимо от того, поправили ли
текст, опубликовали ли черновик или сменили категорию. Отфильтровать «только
правки текста» — то, что нужно при приёмке параграфа, — было нельзя.

Состав изменения вычисляется на записи (`db._insert_history`), потому что одно
сохранение может менять несколько вещей сразу, и потому что направление
перехода статуса (`publish` против `unpublish`) на записи известно, а на чтении
за прошлым состоянием пришлось бы ходить отдельным запросом.

Ярлык для человека считается здесь же, на сервере, и отдаётся в API готовым:
JS-двойник словаря (как у `annotation_categories.py` ↔ `redpen-categories.js`)
пришлось бы держать синхронным руками.
"""

from typing import Any, Dict, List, Optional, Sequence

#: Полный словарь типов действия. Порядок значим: он задаёт порядок токенов в
#: `changes` и приоритет при выборе главного ярлыка.
ACTIONS = (
    "create",      # первая ревизия замечания
    "text",        # изменён текст
    "coords",      # передвинут маркер на скане
    "kind",        # major ⇄ minor
    "publish",     # draft → published
    "unpublish",   # published → draft
    "archive",     # → archived (обратимо, доступно редактору)
    "delete",      # → deleted (легаси: до 2026-09 так называлась архивация)
    "restore",     # archived/deleted → published/draft
    "purge",       # строка стёрта навсегда; остаётся только эта запись
    "category",    # сменена категория
    "tags",        # изменён набор тегов
    "revert",      # ревизия получена откатом
    "rate",        # проставлена или изменена оценка
    "note",        # оставлен комментарий участника
)

#: Токены, которые пишутся в `remark_history.changes`. `rate` и `note` не меняют
#: строку в `remarks` и живут в собственных таблицах — в ленту они попадают
#: только на чтении, ревизией не становятся (иначе `rev_no` сдвигался бы от
#: того, что кто-то поставил оценку, и «версия 3» перестала бы быть ссылкой).
REVISION_ACTIONS = tuple(a for a in ACTIONS if a not in ("rate", "note"))

#: Действия, означающие правку содержания замечания, а не служебный переход.
#: Фильтр «только правки текста» реализован по токену — на сервере в
#: `main.list_history` (параметр `changed=`), в рабочем месте — в
#: `templates/work/remarks.js` (лента событий карточки). Список и
#: `is_content_edit()` ниже — эталон этого разбиения; на них опираются тесты, а
#: не рабочий путь запроса.
CONTENT_ACTIONS = ("text", "coords", "kind")

LABELS = {
    "create": "создание",
    "text": "правка текста",
    "coords": "перенос маркера",
    "kind": "смена вида",
    "publish": "публикация",
    "unpublish": "возврат в черновики",
    "archive": "в архив",
    "delete": "удаление",
    "restore": "восстановление",
    "purge": "удалено навсегда",
    "category": "смена категории",
    "tags": "правка тегов",
    "revert": "откат",
    "rate": "оценка",
    "note": "комментарий",
}

#: Ярлык для ревизий, записанных до появления `changes` (колонка NULL). Врать о
#: составе изменения нельзя: старый журнал его не сохранил.
UNKNOWN_LABEL = "изменение"


def is_known(action: str) -> bool:
    return action in ACTIONS


def _coords(snapshot: Dict[str, Any]) -> tuple:
    return (snapshot.get("coordX"), snapshot.get("coordY"))


def _tags(snapshot: Dict[str, Any]) -> frozenset:
    tags = snapshot.get("tags")
    return frozenset(tags) if isinstance(tags, (list, tuple, set, frozenset)) else frozenset()


def diff_snapshots(prev: Optional[Dict[str, Any]], cur: Dict[str, Any]) -> List[str]:
    """Вернуть токены действий, описывающие переход `prev` → `cur`.

    `prev is None` означает первую ревизию замечания — это `create`.

    Снимки должны быть уже приведены к текущим именам полей
    (`db.normalize_snapshot`): в базе лежат ревизии с ключами `annId`/`annType`
    и значениями `main`/`comment`, записанные до переименования сущности.

    Пустой список — законный результат: сохранение, ничего не изменившее
    (повторный импорт, апсерт с теми же значениями), честно не содержит
    действий.
    """
    if not isinstance(cur, dict):
        return []
    if prev is None:
        return ["create"]
    if not isinstance(prev, dict):
        prev = {}

    found = set()

    if prev.get("text") != cur.get("text"):
        found.add("text")
    if _coords(prev) != _coords(cur):
        found.add("coords")
    if prev.get("kind") != cur.get("kind"):
        found.add("kind")
    if prev.get("category") != cur.get("category"):
        found.add("category")
    if _tags(prev) != _tags(cur):
        found.add("tags")

    old_status, new_status = prev.get("status"), cur.get("status")
    if old_status != new_status:
        if new_status == "archived":
            found.add("archive")
        elif new_status == "deleted":
            # Легаси: в снапшотах до 2026-09 архивация записана как 'deleted'.
            found.add("delete")
        elif old_status in ("archived", "deleted"):
            # Из архива возвращаются и в публикацию, и в черновики; сам факт
            # возврата важнее того, куда именно, — куда, видно по снимку.
            found.add("restore")
        elif new_status == "published":
            found.add("publish")
        elif new_status == "draft":
            found.add("unpublish")

    return [a for a in ACTIONS if a in found]


def with_provenance(action: str, changes: Sequence[str]) -> List[str]:
    """Добавить к составу изменения токен происхождения записи, если он сам по
    себе является действием (сейчас это только `revert`).

    `create` попадает в состав из `diff_snapshots`, а `import` и `backfill`
    описывают, каким инструментом сделана запись, а не что изменилось: они
    остаются в колонке `action` и в токены не превращаются.
    """
    out = list(changes)
    if action == "revert" and "revert" not in out:
        out.append("revert")
    return [a for a in ACTIONS if a in out]


def label(changes: Optional[Sequence[str]]) -> str:
    """Ярлык для человека. `None` — состав не вычислен (старая ревизия)."""
    if changes is None:
        return UNKNOWN_LABEL
    known = [a for a in ACTIONS if a in set(changes)]
    if not known:
        return "без изменений"
    return ", ".join(LABELS[a] for a in known)


def is_content_edit(changes: Optional[Sequence[str]]) -> bool:
    """Правка ли это содержания. `None` (старая ревизия) — неизвестно, а значит
    не «да»: фильтр «только правки текста» не должен показывать догадки."""
    if not changes:
        return False
    return any(a in CONTENT_ACTIONS for a in changes)
