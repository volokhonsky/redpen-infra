"""Два разных действия над замечанием: «в архив» и «удалить навсегда».

Архив — четвёртое значение колонки `status` (`archived`): обратимое,
доступно редактору, замечание исчезает из всех рабочих списков и из статики,
но лежит на вкладке «Архив» и возвращается одной кнопкой (PATCH .../status).

Полное удаление (`DELETE .../purge`) — админское, необратимое: стирает строку,
все связанные данные и всю историю, оставляя ровно одну запись `action='purge'`.
"""

import json
import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
import publisher  # noqa: E402
from _auth_helpers import login  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(config, "BOOTSTRAP_INVITE_CODE", "")
    monkeypatch.setattr(config, "DB_PATH", os.path.join(tmp_path, "redpen.db"))
    monkeypatch.setattr(config, "PUBLISH_DIR", str(tmp_path / "public"))
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    db.init_db()
    yield
    if db._conn is not None:
        db._conn.close()
    db._conn = None


DOC = "archivedoc"
PAGE = "42"
PAGE_KEY = "042"


def _editor(monkeypatch, sub="arch-editor"):
    return login(monkeypatch, sub, role="editor")


def _admin(monkeypatch, sub="arch-admin"):
    return login(monkeypatch, sub, role="admin")


def _create(client, text="живое замечание", status="published", remark_id="a-1"):
    r = client.post(f"/api/editor/{DOC}/{PAGE}",
                    json={"id": remark_id, "kind": "minor", "text": text,
                          "coords": [10, 20], "status": status})
    assert r.status_code == 200, r.text
    return remark_id


def _last_changes(remark_id="a-1"):
    return db.list_history(doc_id=DOC, remark_id=remark_id, limit=1)[0]["changes"]


def _static(remark_id="a-1"):
    return remark_id in [a["id"] for a in publisher.render_page_static(DOC, PAGE_KEY)]


# --- в архив -------------------------------------------------------------------


def test_delete_archives_and_leaves_a_revision(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor, text="исчезнет из статики")
    assert _static() is True

    r = editor.delete(f"/api/editor/{DOC}/{PAGE}/a-1")
    assert r.status_code == 200, r.text

    assert db.get_remark(DOC, PAGE_KEY, "a-1")["status"] == "archived"
    assert _last_changes() == ["archive"]
    assert _static() is False


def test_restore_from_archive_is_a_restore_and_reaches_the_reader(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    editor.delete(f"/api/editor/{DOC}/{PAGE}/a-1")

    r = editor.patch(f"/api/editor/{DOC}/{PAGE}/a-1/status",
                     json={"status": "published", "summary": "возврат из архива"})
    assert r.status_code == 200, r.text
    assert db.get_remark(DOC, PAGE_KEY, "a-1")["status"] == "published"
    assert _last_changes() == ["restore"]
    assert _static() is True


def test_archived_is_refused_by_patch_status(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    r = editor.patch(f"/api/editor/{DOC}/{PAGE}/a-1/status", json={"status": "archived"})
    assert r.status_code == 400


def test_archived_card_still_opens(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    editor.delete(f"/api/editor/{DOC}/{PAGE}/a-1")
    r = editor.get(f"/api/remarks/{DOC}/{PAGE_KEY}/a-1")
    assert r.status_code == 200
    assert r.json()["remark"]["status"] == "archived"


# --- архив не всплывает в списках --------------------------------------------


def test_list_remarks_hides_the_archive_unless_asked(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor, remark_id="a-live", text="живое")
    _create(editor, remark_id="a-arch", text="архивное")
    editor.delete(f"/api/editor/{DOC}/{PAGE}/a-arch")

    default = editor.get("/api/remarks", params={"docId": DOC}).json()
    assert [i["remarkId"] for i in default["items"]] == ["a-live"]
    assert default["total"] == 1

    only = editor.get("/api/remarks", params={"docId": DOC, "status": "archived"}).json()
    assert [i["remarkId"] for i in only["items"]] == ["a-arch"]
    assert only["total"] == 1

    both = editor.get("/api/remarks", params={"docId": DOC, "includeArchived": "true"}).json()
    assert set(i["remarkId"] for i in both["items"]) == {"a-live", "a-arch"}
    assert both["total"] == 2


# --- удалить навсегда --------------------------------------------------------


def _survey_answers_table():
    """Имя таблицы ответов опроса в текущей схеме.

    На main она называется `survey_ratings` (ответ — только число), в ветке
    опроса переезжает в `survey_answers` (число или свободный текст, вопрос в
    колонке `question` вместо `scale`). Тест про полное удаление обязан
    проверять ту таблицу, которая есть в базе: иначе он ломается на слиянии
    двух веток — молча, потому что переименование в одной из них до этого
    файла не доезжает. Возвращает (таблица, колонка вопроса).
    """
    conn = db.get_connection()
    with db._lock:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    if "survey_answers" in names:
        return "survey_answers", "question"
    return "survey_ratings", "scale"


def _seed_related(remark_id="a-1"):
    """Навесить на замечание всё, что должно уйти вместе с ним."""
    db.upsert_remark_db(DOC, PAGE_KEY, remark_id, "minor", "с тегами",
                        tags=["omission"])
    db.set_rating(DOC, PAGE_KEY, remark_id, "importance", 2, rater_id=1)
    db.add_note(DOC, PAGE_KEY, remark_id, author_id=1, body="рабочий комментарий")
    db.pool_add(DOC, PAGE_KEY, remark_id, added_by=1)
    table, question_col = _survey_answers_table()  # берёт db._lock — до захвата ниже
    conn = db.get_connection()
    with db._lock:
        conn.execute(
            "INSERT INTO survey_respondents (pseudonym, token_hash, created_at) "
            "VALUES ('кто-то', 'hash-1', ?)",
            (db._now_iso(),),
        )
        rid = conn.execute("SELECT id FROM survey_respondents").fetchone()["id"]
        conn.execute(
            f"INSERT INTO {table} "
            f"(respondent_id, doc_id, page_num, remark_id, {question_col}, value, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'importance', 2, ?, ?)",
            (rid, DOC, PAGE_KEY, remark_id, db._now_iso(), db._now_iso()),
        )
        conn.commit()


def _related_counts(remark_id="a-1"):
    survey_table = _survey_answers_table()[0]  # берёт db._lock — до захвата ниже
    conn = db.get_connection()
    with db._lock:
        pk_row = conn.execute(
            "SELECT rowid_pk FROM remarks WHERE doc_id=? AND page_num=? AND remark_id=?",
            (DOC, PAGE_KEY, remark_id),
        ).fetchone()
        tags = 0
        if pk_row is not None:
            tags = conn.execute("SELECT COUNT(*) c FROM remark_tags WHERE remark_pk=?",
                                (pk_row["rowid_pk"],)).fetchone()["c"]
        out = {"remark_tags": tags}
        for table in ("remark_ratings", "remark_notes", "rating_pool",
                      survey_table):
            out[table] = conn.execute(
                f"SELECT COUNT(*) c FROM {table} WHERE doc_id=? AND page_num=? AND remark_id=?",
                (DOC, PAGE_KEY, remark_id),
            ).fetchone()["c"]
        out["remark_history"] = conn.execute(
            "SELECT COUNT(*) c FROM remark_history WHERE doc_id=? AND page_num=? AND remark_id=?",
            (DOC, PAGE_KEY, remark_id),
        ).fetchone()["c"]
    return out


def test_purge_is_admin_only(monkeypatch):
    editor = _editor(monkeypatch, "arch-editor2")
    _create(editor)
    r = editor.delete(f"/api/editor/{DOC}/{PAGE}/a-1/purge")
    assert r.status_code == 403
    assert db.get_remark(DOC, PAGE_KEY, "a-1") is not None


def test_purge_wipes_the_remark_and_all_its_traces(monkeypatch):
    admin = _admin(monkeypatch)
    _create(admin)
    _seed_related()
    before = _related_counts()
    assert all(v > 0 for v in before.values()), before

    r = admin.delete(f"/api/editor/{DOC}/{PAGE}/a-1/purge")
    assert r.status_code == 200, r.text

    assert db.get_remark(DOC, PAGE_KEY, "a-1") is None
    after = _related_counts()
    assert after["remark_tags"] == 0
    assert after["remark_ratings"] == 0
    assert after["remark_notes"] == 0
    assert after["rating_pool"] == 0
    assert after[_survey_answers_table()[0]] == 0

    # Ровно одна запись в журнале — сам факт удаления.
    conn = db.get_connection()
    with db._lock:
        rows = conn.execute(
            "SELECT action, author_id, snapshot, changes FROM remark_history "
            "WHERE doc_id=? AND page_num=? AND remark_id=?",
            (DOC, PAGE_KEY, "a-1"),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "purge"
    assert rows[0]["author_id"] == 1
    assert json.loads(rows[0]["changes"]) == ["purge"]
    assert json.loads(rows[0]["snapshot"])["remarkId"] == "a-1"


def test_purge_of_a_live_remark_republishes_the_page(monkeypatch):
    admin = _admin(monkeypatch, "arch-admin3")
    _create(admin, text="ещё на сайте")
    assert _static() is True

    r = admin.delete(f"/api/editor/{DOC}/{PAGE}/a-1/purge")
    assert r.status_code == 200, r.text
    assert _static() is False
    assert publisher.render_page_static(DOC, PAGE_KEY) == []


def test_purge_missing_remark_is_404(monkeypatch):
    admin = _admin(monkeypatch, "arch-admin4")
    r = admin.delete(f"/api/editor/{DOC}/{PAGE}/nope/purge")
    assert r.status_code == 404


# --- миграция status='deleted' -> 'archived' --------------------------------


def test_legacy_deleted_status_migrates_to_archived(tmp_path, monkeypatch):
    """База со строкой status='deleted' после init_db() отдаёт 'archived',
    и счётчики get_stats сходятся."""
    path = os.path.join(tmp_path, "legacy.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    db.init_db()
    db.upsert_remark_db("d", "006", "keep", "minor", "живое", status="published")
    db.upsert_remark_db("d", "006", "gone", "minor", "убрано", status="published")
    conn = db.get_connection()
    with db._lock:
        conn.execute("UPDATE remarks SET status='deleted' WHERE remark_id='gone'")
        conn.commit()

    # Повторная инициализация той же базы — как рестарт API.
    db._conn.close()
    db._conn = None
    db.init_db()

    assert db.get_remark("d", "006", "gone")["status"] == "archived"
    assert db.get_remark("d", "006", "keep")["status"] == "published"
    stats = {d["docId"]: d for d in db.get_stats()["docs"]}
    assert stats["d"] == {"docId": "d", "published": 1, "draft": 0, "archived": 1}
