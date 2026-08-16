"""
Tests for scripts/chapters.py -- building metadata.json's `chapters` from
redpen-content/<docId>/paragraphs_list.txt.

The regression this guards: the hand-maintained chapters had lost §10-§23, so
~170 pages resolved to no section at all, and sections carried only startPage,
which forced callers to guess the owning paragraph.
"""

import json
import os

import pytest

import chapters as ch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SAMPLE = """\
# Список параграфов
# Формат: номер, название, начало, конец

intro, Введение, 3, 4

chapter_I, Глава I. СССР в 1945—1991 гг., 5, 5
1, Восстановление экономики, 6, 20
2, Политическая система, 21, 28
summary_I, Итоги главы, 29, 29

chapter_II, Глава II. Российская Федерация, 30, 30
32-33, Культура и наука, 31, 40
"""


@pytest.fixture()
def sample_list(tmp_path):
    path = tmp_path / "paragraphs_list.txt"
    path.write_text(SAMPLE, encoding="utf-8")
    return str(path)


@pytest.fixture()
def sample_chapters(sample_list):
    return ch.build_chapters(ch.parse_paragraphs_list(sample_list))


def test_parse_skips_comments_and_blank_lines(sample_list):
    entries = ch.parse_paragraphs_list(sample_list)
    assert [e["id"] for e in entries] == [
        "intro", "chapter_I", "1", "2", "summary_I", "chapter_II", "32-33",
    ]


def test_name_with_commas_is_preserved(tmp_path):
    path = tmp_path / "p.txt"
    path.write_text("4, Место и роль СССР, внешняя политика, 38, 58\n", encoding="utf-8")
    entry = ch.parse_paragraphs_list(str(path))[0]
    assert entry["name"] == "Место и роль СССР, внешняя политика"
    assert (entry["startPage"], entry["endPage"]) == (38, 58)


def test_entries_before_first_chapter_become_their_own_chapter(sample_chapters):
    assert sample_chapters[0]["name"] == "Введение"
    assert sample_chapters[0]["sections"] == []


def test_chapter_end_extends_to_cover_its_sections(sample_chapters):
    chapter = sample_chapters[1]
    assert chapter["name"].startswith("Глава I")
    # The chapter's own entry is 5,5 -- the шмуцтитул -- but it must span its
    # sections, otherwise pages 6..29 would resolve to no chapter.
    assert (chapter["startPage"], chapter["endPage"]) == (5, 29)


def test_numbered_paragraphs_get_section_titles(sample_chapters):
    names = [s["name"] for s in sample_chapters[1]["sections"]]
    assert names == ["§ 1. Восстановление экономики", "§ 2. Политическая система", "Итоги главы"]


def test_combined_paragraph_range_is_titled_with_an_em_dash(sample_chapters):
    assert sample_chapters[2]["sections"][0]["name"] == "§ 32—33. Культура и наука"


@pytest.mark.parametrize(
    "page,chapter_name,section_name",
    [
        (3, "Введение", None),
        (5, "Глава I. СССР в 1945—1991 гг.", None),
        (6, "Глава I. СССР в 1945—1991 гг.", "§ 1. Восстановление экономики"),
        (20, "Глава I. СССР в 1945—1991 гг.", "§ 1. Восстановление экономики"),
        (21, "Глава I. СССР в 1945—1991 гг.", "§ 2. Политическая система"),
        (29, "Глава I. СССР в 1945—1991 гг.", "Итоги главы"),
        (35, "Глава II. Российская Федерация", "§ 32—33. Культура и наука"),
    ],
)
def test_locate_page(sample_chapters, page, chapter_name, section_name):
    found = ch.locate_page(sample_chapters, page)
    assert found["chapter"]["name"] == chapter_name
    assert (found["section"]["name"] if found["section"] else None) == section_name


def test_locate_page_outside_any_chapter(sample_chapters):
    assert ch.locate_page(sample_chapters, 1)["chapter"] is None


def test_tail_chapters_are_preserved_and_given_bounds(sample_chapters):
    existing = [
        {"name": "Приложения", "startPage": 41, "sections": [
            {"name": "Хронология", "startPage": 41},
            {"name": "Словарь", "startPage": 45},
        ]},
        {"name": "Указатель", "startPage": 50, "sections": []},
    ]
    merged = ch.merge_tail_chapters(sample_chapters, existing)
    appendix, index = merged[-2], merged[-1]
    assert appendix["name"] == "Приложения"
    assert appendix["endPage"] == 49  # derived from the next tail chapter
    assert [s["endPage"] for s in appendix["sections"]] == [44, 49]
    # The last chapter stays open-ended: it runs to the end of the book.
    assert "endPage" not in index
    assert ch.locate_page(merged, 999)["chapter"]["name"] == "Указатель"


def test_tail_chapters_overlapping_the_list_are_dropped(sample_chapters):
    # A stale hand-written chapter covering pages the list already owns must
    # not shadow the generated ones.
    merged = ch.merge_tail_chapters(sample_chapters, [{"name": "Стар", "startPage": 6}])
    assert [c["name"] for c in merged] == [c["name"] for c in sample_chapters]


@pytest.mark.parametrize("bad", [
    "1, Название, 20, 10\n",     # end before start
    "1, Название, 6\n",          # missing a bound
    "1, Название, six, ten\n",   # non-integer bounds
])
def test_invalid_entries_fail_loudly(tmp_path, bad):
    path = tmp_path / "p.txt"
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(ch.ChaptersError):
        ch.parse_paragraphs_list(str(path))


def test_empty_list_fails(tmp_path):
    path = tmp_path / "p.txt"
    path.write_text("# only a comment\n", encoding="utf-8")
    with pytest.raises(ch.ChaptersError):
        ch.parse_paragraphs_list(str(path))


def test_generate_writes_chapters_and_keeps_other_metadata(tmp_path, sample_list):
    doc_dir = tmp_path / "doc"
    doc_dir.mkdir()
    (doc_dir / "metadata.json").write_text(
        json.dumps({"title": "Книга", "chapters": [{"name": "Старое", "startPage": 1}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    written = ch.generate(str(doc_dir), sample_list)
    assert written is not None

    metadata = json.loads((doc_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["title"] == "Книга"
    assert [c["name"] for c in metadata["chapters"]][:1] == ["Введение"]


def test_generate_without_a_paragraphs_list_is_a_noop(tmp_path):
    doc_dir = tmp_path / "doc"
    doc_dir.mkdir()
    assert ch.generate(str(doc_dir), str(tmp_path / "missing.txt")) is None


def test_real_document_covers_every_printed_page():
    """The regression test proper: §10-§23 used to be missing entirely."""
    paragraphs = os.path.join(ROOT, "redpen-content", "medinsky11klass", "paragraphs_list.txt")
    metadata = os.path.join(ROOT, "redpen-publish", "medinsky11klass", "metadata.json")
    if not (os.path.exists(paragraphs) and os.path.exists(metadata)):
        pytest.skip("content/publish checkout not present")

    with open(metadata, encoding="utf-8") as f:
        existing = json.load(f).get("chapters")
    built = ch.merge_tail_chapters(ch.build_chapters(ch.parse_paragraphs_list(paragraphs)), existing)

    uncovered = [p for p in range(3, 448) if ch.locate_page(built, p)["chapter"] is None]
    assert uncovered == []

    # Spot-check the range that was missing before.
    assert ch.locate_page(built, 122)["section"]["name"].startswith("§ 10.")
    assert ch.locate_page(built, 157)["section"]["name"].startswith("§ 13.")
    assert ch.locate_page(built, 164)["section"]["name"].startswith("§ 14.")
