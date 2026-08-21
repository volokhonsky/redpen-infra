"""Категория аннотации — один из шести приёмов пропаганды либо «Прочее».

Источник правды для цветового разделения в просмотрщике. Дизайн и обоснование —
`docs/annotation-classification-2026-08.md`.

Тот же самый список продублирован в `templates/js/redpen-categories.js`, потому что
просмотрщик обязан работать без сети и без сборки (см. docs/README.md, «Ключевое
ограничение») — общий модуль тянуть неоткуда. Расхождение между двумя копиями
ловит `tests/test_annotation_categories.py`; правя одну, правь обе.

Категория — **собственное поле аннотации**, ровно одно значение, по умолчанию
`other` («Прочее»). Она не выводится из тегов на лету: теги описывают, что не так
с фрагментом, и их может быть сколько угодно; категория отвечает на другой вопрос
— «каким одним приёмом это сделано» — и потому хранится отдельной колонкой
(`annotations.category`).

В опубликованный JSON категория попадает и полем `category`, и зеркальным тегом
`cat:<slug>` — чтобы даром работала уже существующая фильтрация `?tags=` и фильтр
в кабинете. Зеркало **производное**: авторские теги `cat:*` запрещены
(`normalize_tag` их отвергает), иначе поле и тег разъехались бы и снова встал бы
вопрос «какой из них главный».

`category_for_tags()` ниже — **инструмент разовой миграции**, а не рантайм.
Им проставляются категории существующим 1272 аннотациям (`scripts/api/backfill_categories.py`),
пока их не пересмотрит классифицирующий агент. Просмотрщик читает готовое поле.
"""

from typing import Any, Dict, Iterable, List, Optional

CAT_PREFIX = "cat:"

#: Порядок = приоритет. Раньше в списке — сильнее.
PRECEDENCE: List[str] = [
    "today",      # 6. История под сегодняшнюю политику
    "apparatus",  # 5. Ответ подсказан заранее
    "sides",      # 3. Двойной стандарт
    "language",   # 2. Обтекаемый язык
    "evidence",   # 4. Нечем подтвердить
    "omission",   # 1. Умолчание
]

#: Категория по умолчанию: всё, чему не назначили приём, — «Прочее».
OTHER = "other"
DEFAULT_CATEGORY = OTHER

#: Человеческие имена — для легенды и подписей.
CATEGORY_TITLES: Dict[str, str] = {
    "omission": "Умолчание",
    "language": "Обтекаемый язык",
    "sides": "Двойной стандарт",
    "evidence": "Нечем подтвердить",
    "apparatus": "Подсказанный ответ",
    "today": "Мостик в сегодня",
    OTHER: "Прочее",
}

#: Цвета — палитра из доки. Просмотрщик красит маркер и номер в панели.
CATEGORY_COLORS: Dict[str, str] = {
    "omission": "#1B4F9C",
    "language": "#00695C",
    "sides": "#C08A00",
    "evidence": "#6A1B9A",
    "apparatus": "#C2185B",
    "today": "#8E1B14",
    OTHER: "#546E7A",
}

#: Слабые теги: учитываются, только если сильных не нашлось.
WEAK_TAGS: Dict[str, str] = {
    "framing": "evidence",
}

TAG_CATEGORIES: Dict[str, str] = {
    # 1. Умолчание
    "omission": "omission",
    "tc-censorship-unnamed": "omission",
    "tc-censorship-invisible": "omission",
    "tc-record-not-result": "omission",
    "tc-victim-hero": "omission",
    "tc-thaw-no-ending": "omission",
    "tc-hidden-disasters": "omission",
    "tc-antisemitism-hidden": "omission",
    "tc-famine-1946": "omission",
    "tc-key-concept-unexplained": "omission",
    "tc-flattering-portrait": "omission",
    "tc-sanitized-chekist": "omission",
    "tc-invisible-gulag": "omission",
    # 2. Обтекаемый язык
    "euphemism": "language",
    "passive-voice": "language",
    "tc-passive-voice": "language",
    "tc-annexation-euphemism": "language",
    "tc-despite-not-because": "language",
    "tc-permission-as-abundance": "language",
    "tc-import-as-cooperation": "language",
    "tc-joke-instead-of-analysis": "language",
    "tc-strangeness-as-explanation": "language",
    # 3. Двойной стандарт
    "false-cause": "sides",
    "double-standard": "sides",
    "false-symmetry": "sides",
    "tc-usa-origin": "sides",
    "tc-whatabout": "sides",
    "tc-defensive-bloc": "sides",
    "tc-west-economic-scapegoat": "sides",
    "tc-west-broken-promises": "sides",
    "tc-destalinization-blamed": "sides",
    "tc-blame-the-consumer": "sides",
    "tc-red-army-invisible": "sides",
    "tc-selective-aggression-label": "sides",
    "tc-reunification-annexation": "sides",
    "tc-invited-intervention": "sides",
    "tc-reluctant-aggressor": "sides",
    "tc-democratization-as-collapse": "sides",
    "tc-crisis-without-mechanism": "sides",
    "tc-reform-as-powergrab": "sides",
    "tc-reform-labeled-radical": "sides",
    "tc-foreign-praise-inverted": "sides",
    # 4. Нечем подтвердить
    "source-selection": "evidence",
    "dubious-number": "evidence",
    "contested-as-settled": "evidence",
    "overclaim": "evidence",
    "tc-agitprop-as-source": "evidence",
    "tc-official-stats": "evidence",
    "tc-only-friendly-witnesses": "evidence",
    "tc-one-sided-ledger": "evidence",
    "tc-foreign-praise": "evidence",
    "tc-anonymous-superlative": "evidence",
    "tc-author-vouches": "evidence",
    "tc-leader-science": "evidence",
    "tc-constitution-as-evidence": "evidence",
    "tc-showcase-photo": "evidence",
    "tc-uneven-portrait": "evidence",
    "tc-moscow-as-country": "evidence",
    "tc-benefit-through-employer": "evidence",
    "tc-single-source-conflict": "evidence",
    # 5. Ответ подсказан заранее
    "loaded-question": "apparatus",
    "contradiction": "apparatus",
    "tc-cheerful-summary": "apparatus",
    "tc-task-without-material": "apparatus",
    "task-without-material": "apparatus",
    # 6. История под сегодняшнюю политику
    "anachronism": "today",
    "tc-nineties-as-foil": "today",
    "tc-modern-grudge": "today",
    "tc-modern-authority": "today",
    "tc-info-sovereignty-anachronism": "today",
    # 0. Прочее — не приём, а дополнение или поправка
    "context": OTHER,
    "fact-error": OTHER,
}


class CategoryError(ValueError):
    """Некорректный слаг категории."""


def is_valid(slug: Any) -> bool:
    return isinstance(slug, str) and slug in CATEGORY_TITLES


def normalize_category(raw: Any) -> str:
    """Приводит значение к валидному слагу или падает.

    `None` и пустая строка — это «Прочее», а не ошибка: аннотация без разбора
    категории должна публиковаться, а не ломать импорт.
    """
    if raw is None:
        return DEFAULT_CATEGORY
    if not isinstance(raw, str):
        raise CategoryError(f"категория должна быть строкой, получено {type(raw).__name__}")
    slug = raw.strip()
    if not slug:
        return DEFAULT_CATEGORY
    if slug.startswith(CAT_PREFIX):
        slug = slug[len(CAT_PREFIX):]
    if slug not in CATEGORY_TITLES:
        known = ", ".join(sorted(CATEGORY_TITLES))
        raise CategoryError(f"неизвестная категория {slug!r}; допустимы: {known}")
    return slug


def category_tag(slug: str) -> str:
    """Зеркальный тег для опубликованного JSON: `cat:<slug>`."""
    return CAT_PREFIX + slug


def category_for_tags(tags: Optional[Iterable[str]]) -> str:
    """РАЗОВАЯ МИГРАЦИЯ: угадать категорию по тегам.

    Не использовать в рантайме — категория есть в самой аннотации. Здесь она
    выводится по таблице приоритетов, потому что теги в БД отсортированы по
    алфавиту (`ORDER BY tag`) и авторский порядок до нас не доезжает. `framing`
    — слабый сигнал (337 аннотаций, четыре разных смысла), учитывается последним.
    """
    if not tags:
        return OTHER

    weak: Optional[str] = None
    found = set()
    for raw in tags:
        tag = (raw or "").strip()
        if not tag:
            continue
        if tag.startswith(CAT_PREFIX):
            slug = tag[len(CAT_PREFIX):]
            if slug in CATEGORY_TITLES:
                return slug
            continue
        if tag in TAG_CATEGORIES:
            found.add(TAG_CATEGORIES[tag])
        elif tag in WEAK_TAGS and weak is None:
            weak = WEAK_TAGS[tag]

    for cat in PRECEDENCE:
        if cat in found:
            return cat
    if OTHER in found:
        return OTHER
    return weak or OTHER


def category_for(ann: Dict[str, Any]) -> str:
    """Категория аннотации в формате опубликованного JSON."""
    return category_for_tags(ann.get("tags"))
