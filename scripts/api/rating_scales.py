"""
Шкалы оценки замечания.

Модуль лежит в `scripts/api/`, а не в `scripts/`, намеренно: оценки — рабочие
данные редактора, они не участвуют в сборке сайта и никогда не попадают в
статику. Каталог `scripts/` читают и сборка, и конвертер, и тесты контента; всё,
что туда положено, рано или поздно норовит просочиться в опубликованные файлы.

Почему шкал несколько. Удалённая 2026-08-21 ревью-подсистема предлагала один
вердикт (`excellent`/`ok`/`bad`), и в него сходились три разных решения:
«факт неинтересен», «замечание неважно» и «так предъявлять нельзя». Решения
разные — первое отправляет замечание в отсев, второе понижает его в очереди,
третье требует переписать формулировку, — а значение выходило одно.

Каждая шкала растёт в сторону названного качества: 5 по «допустимости» означает
«вполне допустимо», а не «недопустимо».
"""

from typing import Any, Dict, List, Optional

MIN_VALUE = 1
MAX_VALUE = 5

#: Порядок словаря задаёт порядок шкал в интерфейсе. Добавление шкалы — правка
#: только здесь: в базе шкала хранится строкой, миграция не нужна.
SCALES: Dict[str, Dict[str, Any]] = {
    "interest": {
        "title": "Интересность факта",
        "hint": "Насколько сам факт способен удивить читателя, безотносительно "
                "того, как о нём написано.",
    },
    "importance": {
        "title": "Важность",
        "hint": "Насколько важно предъявить это замечание: меняет ли оно "
                "понимание параграфа или уточняет частность.",
    },
    "admissibility": {
        "title": "Допустимость",
        "hint": "Насколько правомерно предъявлять это в такой формулировке: "
                "хватает ли оснований и не приписано ли учебнику лишнее.",
    },
}

#: Максимальная длина пояснения к оценке. Оценка — не второй текст замечания:
#: развёрнутый разбор — это комментарий (remark_notes).
MAX_NOTE_LENGTH = 500


class ScaleError(ValueError):
    """Неизвестная шкала или значение вне диапазона."""


def names() -> List[str]:
    return list(SCALES)


def is_known(scale: Any) -> bool:
    return isinstance(scale, str) and scale in SCALES


def normalize_scale(raw: Any) -> str:
    if not is_known(raw):
        raise ScaleError(f"scale must be one of: {', '.join(SCALES)}")
    return raw


def normalize_value(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        # bool — подкласс int, и True прошёл бы как 1: оценка «истина» бессмысленна.
        raise ScaleError(f"value must be an integer {MIN_VALUE}..{MAX_VALUE}")
    if not MIN_VALUE <= raw <= MAX_VALUE:
        raise ScaleError(f"value must be between {MIN_VALUE} and {MAX_VALUE}")
    return raw


def normalize_note(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    note = str(raw).strip()
    if len(note) > MAX_NOTE_LENGTH:
        raise ScaleError(f"note must be at most {MAX_NOTE_LENGTH} characters")
    return note or None


def describe() -> List[Dict[str, Any]]:
    """Описание шкал для интерфейса — единственный источник, из которого
    редактор узнаёт, какие шкалы существуют и как они называются."""
    return [
        {"name": name, "title": meta["title"], "hint": meta["hint"],
         "min": MIN_VALUE, "max": MAX_VALUE}
        for name, meta in SCALES.items()
    ]
