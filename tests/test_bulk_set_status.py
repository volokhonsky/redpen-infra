"""scripts/api/bulk_set_status.py — массовая смена статуса замечаний."""

import io
import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import bulk_set_status  # noqa: E402

DOC = "bookdoc"


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(tmp_path, "redpen.db"))
    monkeypatch.setattr(config, "PUBLISH_DIR", "")
    db.init_db()
    for page, rid, status in [
        ("021", "a1", "published"),
        ("037", "a2", "published"),
        ("038", "b1", "published"),
        ("038", "b2", "draft"),
        ("204", "c1", "published"),
    ]:
        db.upsert_remark_db(DOC, page, rid, "major", f"текст {rid}", action="create", status=status)
    yield
    db._conn.close()
    db._conn = None


def _run(*argv):
    out = io.StringIO()
    args = bulk_set_status.build_parser().parse_args(["--doc", DOC, *argv])
    result = bulk_set_status.run(args, out=out)
    return result, out.getvalue()


def _status(page, rid):
    return db.get_remark(DOC, page, rid)["status"]


def test_report_mode_writes_nothing():
    result, report = _run("--pages", "038-999", "--from", "published", "--to", "draft")
    assert result == {"selected": 2, "changing": 2}
    assert "без записи" in report
    assert _status("038", "b1") == "published"


def test_unpublish_range_and_mark_with_a_tag():
    _run("--pages", "038-999", "--from", "published", "--to", "draft",
         "--add-tag", "was-published", "--summary", "снято до приёмки", "--apply")
    assert _status("038", "b1") == "draft"
    assert _status("204", "c1") == "draft"
    assert _status("037", "a2") == "published"        # вне диапазона
    assert db.get_remark(DOC, "038", "b1")["tags"] == ["was-published"]
    # Черновик в диапазоне не трогали: он не прошёл --from published.
    assert db.get_remark(DOC, "038", "b2")["tags"] == []
    history = db.list_history(doc_id=DOC, page_num="038", remark_id="b1")
    assert any(h.get("summary") == "снято до приёмки" for h in history)


def test_restore_by_tag():
    _run("--pages", "038-999", "--from", "published", "--to", "draft",
         "--add-tag", "was-published", "--apply")
    _run("--tag", "was-published", "--from", "draft", "--to", "published",
         "--remove-tag", "was-published", "--apply")
    assert _status("038", "b1") == "published"
    assert _status("038", "b2") == "draft"
    assert db.get_remark(DOC, "038", "b1")["tags"] == []


def test_ids_file_and_archive(tmp_path):
    ids = tmp_path / "ids.txt"
    ids.write_text("# решения\n38 b2\n021 a1  # принять\n999 nope\n", encoding="utf-8")
    result, report = _run("--ids", str(ids), "--to", "archived", "--apply")
    assert result["selected"] == 2
    assert "999 nope" in report
    assert _status("038", "b2") == "archived"
    assert _status("021", "a1") == "archived"


def test_requires_a_selection():
    with pytest.raises(bulk_set_status.UsageError):
        _run("--to", "draft")
