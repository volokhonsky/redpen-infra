"""Категории аннотаций: разбор тегов и синхронность двух копий таблицы.

Таблица «тег → категория» живёт в двух местах — `scripts/annotation_categories.py`
и `templates/js/redpen-categories.js`. Дублирование вынужденное: просмотрщик
обязан работать офлайн и без сборки, тянуть общий модуль неоткуда. Эти тесты
следят, чтобы копии не разъехались.
"""


import os
import re
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

import annotation_categories as ac  # noqa: E402

JS_PATH = os.path.join(PROJECT_ROOT, "templates", "js", "redpen-categories.js")


# --- разбор тегов -------------------------------------------------------


def test_no_tags_is_other():
    assert ac.category_for_tags(None) == ac.OTHER
    assert ac.category_for_tags([]) == ac.OTHER
    assert ac.category_for({}) == ac.OTHER


def test_single_tag():
    assert ac.category_for_tags(["omission"]) == "omission"
    assert ac.category_for_tags(["euphemism"]) == "language"
    assert ac.category_for_tags(["anachronism"]) == "today"
    assert ac.category_for_tags(["context"]) == ac.OTHER


def test_unknown_tag_falls_back_to_other():
    assert ac.category_for_tags(["draft", "confidence:high"]) == ac.OTHER


def test_precedence_beats_alphabetical_order():
    # Теги приезжают из БД отсортированными по алфавиту (ORDER BY tag), поэтому
    # результат обязан зависеть только от приоритета, а не от порядка в списке.
    tags = ["euphemism", "omission"]
    assert ac.category_for_tags(tags) == "language"
    assert ac.category_for_tags(list(reversed(tags))) == "language"

    assert ac.category_for_tags(["omission", "anachronism"]) == "today"
    assert ac.category_for_tags(["source-selection", "loaded-question"]) == "apparatus"


def test_framing_is_weak():
    # framing один — работает; вместе с чем угодно осмысленным — уступает.
    assert ac.category_for_tags(["framing"]) == "evidence"
    assert ac.category_for_tags(["framing", "omission"]) == "omission"
    assert ac.category_for_tags(["framing", "context"]) == ac.OTHER


def test_explicit_cat_tag_wins():
    assert ac.category_for_tags(["cat:today", "omission", "euphemism"]) == "today"
    assert ac.category_for_tags(["cat:other", "anachronism"]) == ac.OTHER
    # Несуществующий слаг игнорируется, а не ломает разбор.
    assert ac.category_for_tags(["cat:nonsense", "omission"]) == "omission"


def test_every_category_has_title_and_color():
    for cat in ac.PRECEDENCE + [ac.OTHER]:
        assert cat in ac.CATEGORY_TITLES
        assert re.fullmatch(r"#[0-9A-F]{6}", ac.CATEGORY_COLORS[cat])


def test_tag_table_maps_only_to_known_categories():
    known = set(ac.PRECEDENCE) | {ac.OTHER}
    for tag, cat in ac.TAG_CATEGORIES.items():
        assert cat in known, f"тег {tag} ведёт в неизвестную категорию {cat}"
    for tag, cat in ac.WEAK_TAGS.items():
        assert cat in known
        assert tag not in ac.TAG_CATEGORIES, f"{tag} не может быть и сильным, и слабым"


# --- синхронность с JS-близнецом ---------------------------------------


def _js_source():
    if not os.path.exists(JS_PATH):
        pytest.skip("нет templates/js/redpen-categories.js")
    with open(JS_PATH, encoding="utf-8") as f:
        return f.read()


def _js_literal(name, src, opener="{", closer="}"):
    """Тело литерала `var <name> = { ... };` — по балансу скобок, а не по regex.

    Regex с фиксированным «хвостом» уже один раз проехал мимо однострочного
    объекта и захватил соседний; считаем скобки.
    """
    match = re.search(r"var\s+" + name + r"\s*=\s*" + re.escape(opener), src)
    assert match, f"не нашёл {name} в redpen-categories.js"
    start = match.end()
    depth = 1
    for i in range(start, len(src)):
        if src[i] == opener:
            depth += 1
        elif src[i] == closer:
            depth -= 1
            if depth == 0:
                return src[start:i]
    raise AssertionError(f"незакрытый литерал {name}")


def _js_object(name, src):
    """Достаёт литерал объекта и превращает в dict."""
    body = _js_literal(name, src)
    body = re.sub(r"//[^\n]*", "", body)  # построчные комментарии
    pairs = re.findall(r"'?([\w:-]+)'?\s*:\s*'([^']*)'", body)
    return dict(pairs)


def _js_array(name, src):
    return re.findall(r"'([\w-]+)'", _js_literal(name, src, "[", "]"))


def test_js_tag_table_matches_python():
    src = _js_source()
    assert _js_object("TAG_CATEGORIES", src) == ac.TAG_CATEGORIES


def test_js_precedence_matches_python():
    assert _js_array("PRECEDENCE", _js_source()) == ac.PRECEDENCE


def test_js_colors_and_titles_match_python():
    src = _js_source()
    assert _js_object("COLORS", src) == ac.CATEGORY_COLORS
    assert _js_object("TITLES", src) == ac.CATEGORY_TITLES


def test_js_weak_tags_match_python():
    assert _js_object("WEAK_TAGS", _js_source()) == ac.WEAK_TAGS


def test_css_defines_every_category_variable():
    css = os.path.join(PROJECT_ROOT, "templates", "css", "main.css")
    with open(css, encoding="utf-8") as f:
        text = f.read()
    for cat, color in ac.CATEGORY_COLORS.items():
        assert f"--cat-{cat}:" in text, f"в main.css нет переменной --cat-{cat}"
        assert color in text, f"в main.css нет цвета {color} для {cat}"


# --- категория как поле, а не догадка -----------------------------------


def test_normalize_category_defaults_to_other():
    assert ac.normalize_category(None) == ac.OTHER
    assert ac.normalize_category("") == ac.OTHER
    assert ac.normalize_category("   ") == ac.OTHER


def test_normalize_category_accepts_slug_and_mirror_tag():
    assert ac.normalize_category("today") == "today"
    assert ac.normalize_category(" today ") == "today"
    assert ac.normalize_category("cat:today") == "today"


def test_normalize_category_rejects_unknown():
    with pytest.raises(ac.CategoryError):
        ac.normalize_category("propaganda")
    with pytest.raises(ac.CategoryError):
        ac.normalize_category(42)


def test_category_tag_roundtrip():
    for slug in ac.CATEGORY_TITLES:
        assert ac.normalize_category(ac.category_tag(slug)) == slug


def test_page_blob_and_panel_agree_on_category(tmp_path):
    """Просмотрщик красит маркер из встроенного JSON, а номер в списке приезжает
    из пре-рендера. Если категория выпадет из одного из двух, кружок и номер
    разъедутся по цвету — так уже случалось с белым списком ключей блоба."""
    import page_html

    ann = {"id": "a-1", "text": "x", "kind": "major", "coords": [1, 2],
           "category": "today", "tags": ["anachronism", "cat:today"]}

    blob = page_html._page_data_blob([ann])
    assert '"category":"today"' in blob

    panel = page_html._panel_list([ann])
    assert "panel-item--cat-today" in panel
    # Зеркальный тег читателю не показываем: категория уже сообщена цветом.
    assert "cat:today" not in panel
    assert "anachronism" in panel
