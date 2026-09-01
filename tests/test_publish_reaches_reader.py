"""Публикация должна доезжать до читателя, а не только до JSON.

Постраничный просмотрщик ничего не загружает (инвариант офлайна): аннотации
приезжают к нему инлайновым блоком `redpen-page-data`, вшитым в HTML. Пока
публикатор писал только `remarks/page_NNN.json`, правка через редактор
обновляла файл, который на новых адресах не читает никто.
"""

import json
import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import publisher  # noqa: E402


DOC = "readerdoc"

MANIFEST = {
    "title": "Тестовый учебник",
    "chapters": [{"id": "chapter_I", "name": "Глава I", "sections": [
        {"id": "1", "name": "§ 1. Раздел", "startPage": 6, "endPage": 20}]}],
    "pages": [
        {"file": "page_006", "label": "6"},
        {"file": "page_007", "label": "7"},
        {"file": "page_008", "label": "8"},
    ],
}


@pytest.fixture
def site(tmp_path, monkeypatch):
    """PUBLISH_DIR с собранным сайтом: манифест на месте, страниц ещё нет."""
    if db._conn is not None:
        db._conn.close()
        db._conn = None
    monkeypatch.setattr(config, "DB_PATH", os.path.join(tmp_path, "redpen.db"))
    publish_dir = os.path.join(tmp_path, "site")
    doc_dir = os.path.join(publish_dir, DOC)
    os.makedirs(os.path.join(doc_dir, "remarks"))
    with open(os.path.join(doc_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(MANIFEST, f, ensure_ascii=False)
    monkeypatch.setattr(config, "PUBLISH_DIR", publish_dir)
    db.init_db()
    yield doc_dir
    db._conn.close()
    db._conn = None


def _page_html(doc_dir, label):
    path = os.path.join(doc_dir, "pages", label, "index.html")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


def _inline_data(html):
    """Тот самый блок, из которого просмотрщик берёт аннотации."""
    marker = 'id="redpen-page-data">'
    start = html.index(marker) + len(marker)
    return json.loads(html[start:html.index("</script>", start)])


def test_publishing_writes_the_reader_page(site):
    db.upsert_remark_db(DOC, "006", "a1", "major", "Видно ли это читателю",
                            coord_x=10, coord_y=20, action="create",
                            category="omission")
    assert publisher.publish_page(DOC, "006") is True

    html = _page_html(site, "6")
    assert html is not None, "страница читателя не отрисована"
    assert "Видно ли это читателю" in html


def test_inline_block_matches_the_json(site):
    db.upsert_remark_db(DOC, "006", "a1", "major", "первая", coord_x=1, coord_y=1,
                            action="create", category="today")
    db.upsert_remark_db(DOC, "006", "a2", "minor", "черновик", coord_x=2, coord_y=2,
                            action="create", status="draft")
    publisher.publish_page(DOC, "006")

    inline = _inline_data(_page_html(site, "6"))
    with open(os.path.join(site, "remarks", "page_006.json"), encoding="utf-8") as f:
        on_disk = json.load(f)

    assert [a["id"] for a in inline] == [a["id"] for a in on_disk]
    # Черновик доезжает до страницы вместе с остальными — просмотрщик прячет
    # его сам, по тегу.
    assert any(a.get("draft") for a in inline)


def test_edit_updates_the_reader_page(site):
    db.upsert_remark_db(DOC, "006", "a1", "major", "старый текст",
                            coord_x=1, coord_y=1, action="create")
    publisher.publish_page(DOC, "006")
    assert "старый текст" in _page_html(site, "6")

    db.upsert_remark_db(DOC, "006", "a1", "major", "новый текст",
                            coord_x=1, coord_y=1)
    publisher.publish_page(DOC, "006")

    html = _page_html(site, "6")
    assert "новый текст" in html
    assert "старый текст" not in html


def test_archive_removes_it_from_the_reader_page(site):
    db.upsert_remark_db(DOC, "006", "a1", "major", "будет удалена",
                            coord_x=1, coord_y=1, action="create")
    publisher.publish_page(DOC, "006")
    db.archive_remark(DOC, "006", "a1")
    publisher.publish_page(DOC, "006")
    assert "будет удалена" not in _page_html(site, "6")


def test_only_the_touched_page_is_rewritten(site):
    db.upsert_remark_db(DOC, "006", "a1", "major", "стр 6", coord_x=1, coord_y=1,
                            action="create")
    db.upsert_remark_db(DOC, "007", "b1", "major", "стр 7", coord_x=1, coord_y=1,
                            action="create")
    publisher.publish_page(DOC, "006")
    assert _page_html(site, "6") is not None
    # Соседняя страница не перерисовывается: публикация поштучная.
    assert _page_html(site, "7") is None


def test_missing_manifest_does_not_break_publication(tmp_path, monkeypatch):
    """В PUBLISH_DIR может не быть собранного сайта — JSON всё равно пишется."""
    if db._conn is not None:
        db._conn.close()
        db._conn = None
    monkeypatch.setattr(config, "DB_PATH", os.path.join(tmp_path, "redpen.db"))
    publish_dir = os.path.join(tmp_path, "bare")
    monkeypatch.setattr(config, "PUBLISH_DIR", publish_dir)
    db.init_db()
    try:
        db.upsert_remark_db(DOC, "006", "a1", "major", "текст", coord_x=1, coord_y=1,
                                action="create")
        assert publisher.publish_page(DOC, "006") is True
        assert os.path.exists(os.path.join(publish_dir, DOC, "remarks", "page_006.json"))
        assert not os.path.exists(os.path.join(publish_dir, DOC, "pages"))
    finally:
        db._conn.close()
        db._conn = None


def test_page_outside_the_manifest_is_skipped(site):
    db.upsert_remark_db(DOC, "099", "a1", "major", "нет такой страницы",
                            coord_x=1, coord_y=1, action="create")
    assert publisher.publish_page(DOC, "099") is True
    assert _page_html(site, "99") is None


def test_publish_all_heals_reader_pages(site):
    db.upsert_remark_db(DOC, "006", "a1", "major", "стр 6", coord_x=1, coord_y=1,
                            action="create")
    db.upsert_remark_db(DOC, "007", "b1", "major", "стр 7", coord_x=1, coord_y=1,
                            action="create")
    result = publisher.publish_all()
    assert result["failed"] == 0
    assert "стр 6" in _page_html(site, "6")
    assert "стр 7" in _page_html(site, "7")


def test_reader_page_stamp_has_no_time_of_day(site):
    """Метка «последнее обновление» — только дата.

    Страницы перерисовываются на каждое сохранение в редакторе, поэтому метка
    с минутами была бы публичным журналом активности: часы суток выдают
    часовой пояс, а при нескольких участниках — и людей (docs/anonymity-model.md).
    """
    import re

    db.upsert_remark_db(DOC, "006", "a1", "major", "текст", coord_x=1, coord_y=1,
                            action="create")
    publisher.publish_page(DOC, "006")

    html = _page_html(site, "6")
    match = re.search(r"Последнее обновление: ([^<]*)", html)
    assert match, "метки обновления нет вовсе"
    assert not re.search(r"\d{1,2}:\d{2}", match.group(1)), match.group(1)


def test_unwritable_pages_dir_does_not_break_json_publication(site):
    """Каталог pages/ может принадлежать другому пользователю.

    На проде его создаёт rsync content-sync от root, а API работает под uid
    10001. Раньше PermissionError здесь обрушивал publish_all целиком, хотя
    JSON — канон публикации — записывался нормально.
    """
    import stat

    db.upsert_remark_db(DOC, "006", "a1", "major", "текст", coord_x=1, coord_y=1,
                            action="create")
    publisher.publish_page(DOC, "006")

    pages_dir = os.path.join(site, "pages")
    mode = os.stat(pages_dir).st_mode
    os.chmod(pages_dir, stat.S_IRUSR | stat.S_IXUSR)  # только чтение
    try:
        db.upsert_remark_db(DOC, "006", "a1", "major", "новый текст",
                                coord_x=1, coord_y=1)
        assert publisher.publish_page(DOC, "006") is True
        with open(os.path.join(site, "remarks", "page_006.json"), encoding="utf-8") as f:
            assert "новый текст" in f.read()
    finally:
        os.chmod(pages_dir, mode)
