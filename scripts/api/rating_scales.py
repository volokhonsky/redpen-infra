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

Каждая шкала растёт в сторону названного качества: старшее значение
«допустимости» означает «вполне допустимо», а не «недопустимо».

Диапазон у шкал разный (2026-08-31). «Интересность» и «важность» — вопросы о
мере, на них честно отвечать пятибалльно. «Допустимость» — вопрос о решении:
публикуем это в текущем виде или нет. Третьего не дано, и промежуточные баллы
там означали лишь нежелание отвечать. Поэтому у шкалы появились собственные
`min`/`max` и подписанные варианты `options`, а интерфейсы (редактор `/app/` и
опросник `/survey/`) рисуют кнопки по описанию из `describe()`, а не по
зашитому диапазону.
"""

from typing import Any, Dict, List, Optional

#: Диапазон по умолчанию — шкала меры. Шкала с собственными границами
#: перекрывает их своими ключами `min`/`max`.
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
        "title": "Можно ли публиковать в текущем виде",
        "hint": "Правомерно ли предъявлять это в такой формулировке: хватает ли "
                "оснований и не приписано ли учебнику лишнее.",
        "min": 1,
        "max": 2,
        # Порядок тот же, что у остальных шкал: старшее значение — в сторону
        # названного качества.
        "options": [
            {"value": 1, "label": "Нет"},
            {"value": 2, "label": "Да"},
        ],
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


def bounds(scale: Any) -> tuple:
    """Границы конкретной шкалы. Шкала без собственных — шкала меры 1..5."""
    meta = SCALES[normalize_scale(scale)]
    return meta.get("min", MIN_VALUE), meta.get("max", MAX_VALUE)


def options(scale: Any) -> Optional[List[Dict[str, Any]]]:
    """Подписанные варианты — только там, где цифра сама по себе непонятна."""
    return SCALES[normalize_scale(scale)].get("options")


def normalize_value(scale: Any, raw: Any) -> int:
    """Значение проверяется по границам своей шкалы: у «допустимости» их две,
    у остальных пять, и общего диапазона на всех больше нет."""
    low, high = bounds(scale)
    if isinstance(raw, bool) or not isinstance(raw, int):
        # bool — подкласс int, и True прошёл бы как 1: оценка «истина» бессмысленна.
        raise ScaleError(f"value must be an integer {low}..{high}")
    if not low <= raw <= high:
        raise ScaleError(f"value must be between {low} and {high}")
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
    редактор и опросник узнают, какие шкалы существуют, как называются и какие
    у них границы."""
    out: List[Dict[str, Any]] = []
    for name, meta in SCALES.items():
        low, high = bounds(name)
        out.append({
            "name": name,
            "title": meta["title"],
            "hint": meta["hint"],
            "min": low,
            "max": high,
            "options": meta.get("options"),
        })
    return out
