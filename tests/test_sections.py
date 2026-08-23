"""Параграфы: заливка из манифеста, привязка страниц, сводка по аннотациям.

Параграф — единица работы редактора (задание агенту, приёмка, доска работ).
Разметка приезжает из `metadata.json` (chapters[].sections[]); API её не
вычисляет на лету, потому что контент-файлы ему недоступны.
"""

import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import import_sections  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_path = os.path.join(tmp_path, "redpen.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db()
    yield
    db._conn.close()
    db._conn = None


MANIFEST = {
    "chapters": [
        {"id": "intro", "name": "Введение", "sections": []},
        {"id": "chapter_I", "name": "Глава I", "sections": [
            {"id": "1", "name": "§ 1. Восстановление", "startPage": 6, "endPage": 20},
            {"id": "2", "name": "§ 2. Политическая система", "startPage": 21, "endPage": 28},
        ]},
        {"name": None, "sections": [
            {"name": "Словарь терминов", "startPage": 423, "endPage": 429},
        ]},
    ]
}


def _seed_sections():
    sections = import_sections.sections_from_manifest(MANIFEST)
    db.replace_sections("doc1", sections)
    return sections


# --- разбор манифеста ---------------------------------------------------


def test_manifest_gives_a_flat_list_of_sections():
    sections = import_sections.sections_from_manifest(MANIFEST)
    assert [s["sectionId"] for s in sections] == ["1", "2", "p423"]
    assert sections[0]["chapterTitle"] == "Глава I"
    assert sections[0]["pageStart"] == 6 and sections[0]["pageEnd"] == 20


def test_chapter_without_sections_is_not_a_section():
    # «Введение» — глава без параграфов: ни номера, ни задания агенту.
    sections = import_sections.sections_from_manifest(MANIFEST)
    assert all(s["sectionId"] != "intro" for s in sections)


def test_section_without_id_is_kept_under_a_page_key():
    # Безномерные разделы конца книги — тоже единицы работы, терять их нельзя.
    sections = import_sections.sections_from_manifest(MANIFEST)
    assert sections[-1]["sectionId"] == "p423"
    assert sections[-1]["title"] == "Словарь терминов"


def test_gaps_between_sections_are_reported():
    # Страницы между параграфами (аппарат главы) конвейером не покрываются;
    # молчать о них нельзя — именно так они и теряются.
    assert import_sections._page_gaps(
        [{"pageStart": 6, "pageEnd": 20}, {"pageStart": 25, "pageEnd": 30}]
    ) == [(21, 24)]


# --- заливка ------------------------------------------------------------


def test_replace_is_wholesale():
    _seed_sections()
    assert len(db.list_sections("doc1")) == 3
    # Параграф, исчезнувший из манифеста, исчезает и в БД.
    db.replace_sections("doc1", [{"sectionId": "1", "title": "§ 1",
                                  "pageStart": 6, "pageEnd": 20}])
    assert [s["sectionId"] for s in db.list_sections("doc1")] == ["1"]


# --- привязка страницы --------------------------------------------------


def test_page_maps_to_its_section():
    _seed_sections()
    assert db.find_section_for_page("doc1", "006")["sectionId"] == "1"
    assert db.find_section_for_page("doc1", "020")["sectionId"] == "1"
    assert db.find_section_for_page("doc1", "021")["sectionId"] == "2"


def test_front_matter_belongs_to_no_section():
    # Обложка и титул — законное «ни к какому параграфу», а не ошибка.
    _seed_sections()
    assert db.find_section_for_page("doc1", "000") is None
    assert db.find_section_for_page("doc1", "-01") is None
    assert db.find_section_for_page("doc1", "100") is None


# --- сводка -------------------------------------------------------------


def test_counts_are_scoped_to_the_page_range():
    _seed_sections()
    db.upsert_annotation_db("doc1", "006", "a1", "main", "t", action="create")
    db.upsert_annotation_db("doc1", "020", "a2", "main", "t", action="create",
                            status="draft")
    db.upsert_annotation_db("doc1", "021", "a3", "main", "t", action="create")

    by_id = {s["sectionId"]: s for s in db.list_sections("doc1")}
    assert by_id["1"]["counts"]["total"] == 2
    assert by_id["1"]["counts"]["published"] == 1
    assert by_id["1"]["counts"]["draft"] == 1
    assert by_id["2"]["counts"]["total"] == 1
    assert by_id["p423"]["counts"]["total"] == 0


def test_unclassified_count_drives_the_work_board():
    _seed_sections()
    db.upsert_annotation_db("doc1", "006", "a1", "main", "t", action="create")
    db.upsert_annotation_db("doc1", "007", "a2", "main", "t", action="create",
                            category="other", author_id=1)
    by_id = {s["sectionId"]: s for s in db.list_sections("doc1")}
    # Обе аннотации в категории 'other', но разобрана только одна.
    assert by_id["1"]["counts"]["total"] == 2
    assert by_id["1"]["counts"]["unclassified"] == 1


def test_deleted_annotations_are_out_of_the_counts():
    _seed_sections()
    db.upsert_annotation_db("doc1", "006", "a1", "main", "t", action="create")
    db.soft_delete_annotation("doc1", "006", "a1")
    by_id = {s["sectionId"]: s for s in db.list_sections("doc1")}
    assert by_id["1"]["counts"]["total"] == 0


# --- выборка комментариев параграфа -------------------------------------


def test_annotations_can_be_filtered_by_section():
    """Параграф — диапазон страниц, а не колонка: связь выводится, а не хранится."""
    _seed_sections()
    db.upsert_annotation_db("doc1", "006", "a1", "main", "в §1", action="create")
    db.upsert_annotation_db("doc1", "020", "a2", "main", "тоже §1", action="create")
    db.upsert_annotation_db("doc1", "021", "b1", "main", "уже §2", action="create")
    db.upsert_annotation_db("doc1", "000", "c1", "main", "обложка", action="create")

    ids = [a["annId"] for a in db.list_annotations(doc_id="doc1", section_id="1")]
    assert sorted(ids) == ["a1", "a2"]
    assert db.count_annotations(doc_id="doc1", section_id="1") == 2
    assert [a["annId"] for a in db.list_annotations(doc_id="doc1", section_id="2")] == ["b1"]


def test_section_filter_combines_with_other_filters():
    _seed_sections()
    db.upsert_annotation_db("doc1", "006", "a1", "main", "черновик", action="create",
                            status="draft")
    db.upsert_annotation_db("doc1", "007", "a2", "main", "опубликован", action="create")
    items = db.list_annotations(doc_id="doc1", section_id="1", status="draft")
    assert [a["annId"] for a in items] == ["a1"]


def test_unknown_section_gives_nothing():
    _seed_sections()
    db.upsert_annotation_db("doc1", "006", "a1", "main", "текст", action="create")
    assert db.list_annotations(doc_id="doc1", section_id="нет-такого") == []
