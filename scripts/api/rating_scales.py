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

Диапазон у шкал разный (2026-08-31). «Интересен ли сам факт» и «насколько это
важно» — вопросы о мере, на них честно отвечать пятибалльно. «Допустимость» —
вопрос о решении:
публикуем это в текущем виде или нет. Третьего не дано, и промежуточные баллы
там означали лишь нежелание отвечать. Поэтому у шкалы появились собственные
`min`/`max` и подписанные варианты `options`, а интерфейсы (рабочее место
`/work/` и опросник `/survey/`) рисуют кнопки по описанию из `describe()`, а не
по зашитому диапазону.

Подписи концов (`ends`) тоже принадлежат шкале, а не интерфейсу (2026-09-02).
Опросник рисовал под всеми шкалами одну и ту же пару «совсем нет — да, вполне»:
она отвечает на вопрос «да или нет», а шкала спрашивает о мере, и «3» между
такими концами не значило ничего. Теперь у каждой шкалы меры своя пара, и
заголовок сформулирован вопросом, на который эта пара отвечает. У шкалы с
`options` подписей концов нет: варианты уже подписаны словами.
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
        "title": "Интересен ли сам факт?",
        "hint": "Насколько сам факт способен удивить читателя, безотносительно "
                "того, как о нём написано.",
        "ends": {"low": "совсем неинтересно", "high": "очень интересно"},
    },
    "importance": {
        "title": "Насколько это важно?",
        "hint": "Насколько важно предъявить это замечание: меняет ли оно "
                "понимание параграфа или уточняет частность.",
        "ends": {"low": "совсем не важно", "high": "очень важно"},
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

#: Открытые вопросы опроса — ответ свободным текстом, а не цифрой. Отдельны от
#: SCALES намеренно: `describe()`/`names()` остаются «списком шкал», их читает
#: редакторский путь (`remark_ratings`, где значение обязательно и текст уже
#: есть колонкой `note`). Открытый вопрос нужен только опросу — самое ценное во
#: взгляде со стороны это формулировка возражения, а не среднее по шкале.
#: Порядок словаря задаёт порядок в опроснике (после шкал).
OPEN_QUESTIONS: Dict[str, Dict[str, Any]] = {
    "comment": {
        "title": "Что бы вы сказали автору замечания",
        "hint": "Необязательно. Если замечание кажется вам неубедительным или "
                "задевающим — напишите, что именно не так.",
        "maxLength": 1000,
    },
}


class ScaleError(ValueError):
    """Неизвестная шкала/вопрос или значение вне диапазона."""


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
            "ends": meta.get("ends"),
        })
    return out


# --- открытые вопросы (только опрос) -------------------------------------


def open_names() -> List[str]:
    return list(OPEN_QUESTIONS)


def is_open_question(name: Any) -> bool:
    return isinstance(name, str) and name in OPEN_QUESTIONS


def normalize_text(question: Any, raw: Any) -> Optional[str]:
    """Открытый ответ: обрезка по краям, пустое → None, длиннее предела →
    ScaleError. `None` на выходе означает «ответа нет» (вызывающий трактует
    это как «удалить», если ключ в запросе был)."""
    if not is_open_question(question):
        raise ScaleError(f"unknown open question: {question}")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    limit = OPEN_QUESTIONS[question]["maxLength"]
    if len(text) > limit:
        raise ScaleError(f"{question} must be at most {limit} characters")
    return text


def describe_open() -> List[Dict[str, Any]]:
    return [
        {
            "name": name,
            "title": meta["title"],
            "hint": meta["hint"],
            "maxLength": meta["maxLength"],
            "answer": "text",
        }
        for name, meta in OPEN_QUESTIONS.items()
    ]


def describe_survey() -> List[Dict[str, Any]]:
    """Шкалы и открытые вопросы одним списком — чтобы опросник рисовал карточку
    по описанию: у каждого пункта `answer` — `"value"` (кнопки) или `"text"`
    (поле). Шкалы идут первыми."""
    out = [dict(scale, answer="value") for scale in describe()]
    out.extend(describe_open())
    return out
