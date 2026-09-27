"""
Tests for scripts/section_pages.py -- страница параграфа: краткое содержание,
пропагандистские тезисы, вопросы учителю.

Что сторожим:
1. исходник разбирается в три блока, опечатка в заголовке -- ошибка сборки;
2. ссылки `p:` становятся относительными адресами (сайт работает с флешки),
   ссылка на несуществующую страницу -- ошибка;
3. признак `hasPage` попадает в metadata.json, и по нему оглавление и
   страницы учебника ставят ссылку на страницу параграфа;
4. на странице параграфа нет ни одного сетевого запроса.
"""

import json
import os

import pytest

import page_html
import section_pages

SOURCE = """~~~meta
updated: 2026-09-27
~~~

## Краткое содержание
Параграф о послевоенных годах.

## Пропагандистские тезисы
### Успех бесспорен
Главный вопрос уже содержит ответ ([стр. 6](p:6/ann-1)).

### Виноват Запад
См. [стр. 7](p:7).

## Вопросы учителю
### Сколько людей умерло от голода?
Оценки расходятся.
"""


def _site(tmp_path, source=SOURCE, remarks=None):
    doc = tmp_path / "site" / "testdoc"
    doc.mkdir(parents=True)
    pages = [{"file": f"page_{n:03d}", "label": str(n)} for n in range(5, 10)]
    metadata = {
        "title": "Тестовый учебник",
        "pages": pages,
        "chapters": [{
            "id": "chapter_I", "name": "Глава I. Тест", "startPage": 5, "endPage": 9,
            "sections": [
                {"id": "1", "name": "§ 1. Первый", "startPage": 6, "endPage": 7},
                {"id": "2", "name": "§ 2. Второй", "startPage": 8, "endPage": 9},
            ],
        }],
    }
    (doc / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    (doc / "remarks").mkdir()
    for n in range(5, 10):
        items = (remarks or {}).get(n, [])
        (doc / "remarks" / f"page_{n:03d}.json").write_text(json.dumps(items), encoding="utf-8")
    src = tmp_path / "content" / "sections"
    src.mkdir(parents=True)
    if source is not None:
        (src / "1.md").write_text(source, encoding="utf-8")
    return str(doc), str(src)


PUBLISHED_ON_6 = {6: [{"id": "ann-1", "text": "x", "kind": "major", "coords": [1, 1], "tags": []}]}


def test_parse_splits_three_blocks():
    parsed = section_pages.parse_source(SOURCE)
    assert parsed["meta"] == {"updated": "2026-09-27"}
    assert parsed["summary"] == "Параграф о послевоенных годах."
    assert [t["title"] for t in parsed["theses"]] == ["Успех бесспорен", "Виноват Запад"]
    assert parsed["questions"][0]["title"] == "Сколько людей умерло от голода?"
    assert parsed["questions"][0]["body"] == "Оценки расходятся."


def test_unknown_heading_fails_loudly():
    with pytest.raises(section_pages.SectionPageError, match="незнакомый заголовок"):
        section_pages.parse_source("## Кратко\nтекст\n")


def test_summary_is_required():
    with pytest.raises(section_pages.SectionPageError, match="Краткое содержание"):
        section_pages.parse_source("## Вопросы учителю\n### Вопрос?\n")


def test_page_links_become_relative():
    labels = {"6": "page_006"}
    out = section_pages.rewrite_page_links("[a](p:6) [b](p:6/ann-1)", labels, "../../")
    assert "(../../pages/6/index.html)" in out
    assert "(../../pages/6/index.html?only=ann-1)" in out


def test_link_to_unknown_page_fails():
    with pytest.raises(section_pages.SectionPageError, match="несуществующую страницу"):
        section_pages.rewrite_page_links("[a](p:999)", {"6": "page_006"}, "../../")


def test_unknown_remark_id_is_a_warning_not_an_error():
    warnings = []
    section_pages.rewrite_page_links("[a](p:6/nope)", {"6": "page_006"}, "../../",
                                     {"6": {"ann-1"}}, warnings)
    assert warnings and "nope" in warnings[0]


def test_build_writes_page_and_marks_metadata(tmp_path):
    doc, src = _site(tmp_path, remarks=PUBLISHED_ON_6)
    written = section_pages.build_section_pages(doc, src, "27.09.2026")
    assert written == [os.path.join(doc, "sections", "1", "index.html")]

    meta = json.load(open(os.path.join(doc, "metadata.json"), encoding="utf-8"))
    sections = meta["chapters"][0]["sections"]
    assert sections[0].get("hasPage") is True
    assert "hasPage" not in sections[1]

    html_text = open(written[0], encoding="utf-8").read()
    assert "Пропагандистские тезисы" in html_text
    assert "Вопросы учителю" in html_text
    assert 'id="q-1"' in html_text
    assert 'href="../../pages/6/index.html?only=ann-1"' in html_text
    # следующего параграфа со страницей нет -- ведём на его первую страницу
    assert 'href="../../pages/8/index.html"' in html_text


def test_section_page_never_talks_to_the_network(tmp_path):
    doc, src = _site(tmp_path, remarks=PUBLISHED_ON_6)
    html_text = open(section_pages.build_section_pages(doc, src, "27.09.2026")[0], encoding="utf-8").read()
    assert "<script" not in html_text
    assert "/api/" not in html_text
    assert 'href="https://' not in html_text.split("</head>")[1]


def test_orphan_source_fails(tmp_path):
    doc, src = _site(tmp_path)
    open(os.path.join(src, "99.md"), "w", encoding="utf-8").write(SOURCE)
    with pytest.raises(section_pages.SectionPageError, match="99"):
        section_pages.build_section_pages(doc, src, "27.09.2026")


def test_removed_source_removes_page_and_mark(tmp_path):
    doc, src = _site(tmp_path, remarks=PUBLISHED_ON_6)
    section_pages.build_section_pages(doc, src, "27.09.2026")
    os.remove(os.path.join(src, "1.md"))
    section_pages.build_section_pages(doc, src, "27.09.2026")
    assert not os.path.exists(os.path.join(doc, "sections", "1"))
    meta = json.load(open(os.path.join(doc, "metadata.json"), encoding="utf-8"))
    assert "hasPage" not in meta["chapters"][0]["sections"][0]


def test_toc_and_reader_page_link_to_the_section_page(tmp_path):
    doc, src = _site(tmp_path, remarks=PUBLISHED_ON_6)
    section_pages.build_section_pages(doc, src, "27.09.2026")
    page_html.build_pages(doc, "27.09.2026")

    toc = open(os.path.join(doc, "index.html"), encoding="utf-8").read()
    assert 'href="sections/1/"' in toc
    assert 'href="sections/2/"' not in toc

    page6 = open(os.path.join(doc, "pages", "6", "index.html"), encoding="utf-8").read()
    assert 'href="../../sections/1/"' in page6
    page8 = open(os.path.join(doc, "pages", "8", "index.html"), encoding="utf-8").read()
    assert "sections/" not in page8


def test_real_sources_build(tmp_path):
    """Исходники 11 класса разбираются и ссылаются только на существующие страницы."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_dir = os.path.join(root, "redpen-content", "medinsky11klass", "sections")
    if not os.path.isdir(src_dir):
        pytest.skip("redpen-content не рядом")
    labels = {str(n): f"page_{n:03d}" for n in range(3, 449)}
    for name in os.listdir(src_dir):
        if not name.endswith(".md"):
            continue
        parsed = section_pages.parse_source(open(os.path.join(src_dir, name), encoding="utf-8").read())
        assert parsed["theses"] and parsed["questions"], name
        for item in parsed["theses"] + parsed["questions"]:
            section_pages.rewrite_page_links(item["body"], labels, "../../")
