"""Опрос: голос из-за пределов закрытого круга.

Замечания пишутся внутри круга, и круг успевает привыкнуть к собственным
формулировкам. Вопрос «можно ли это публиковать в текущем виде» имеет смысл
задавать тому, кто внутри разбора не сидит, — отсюда анонимный респондент.

Здесь проверяется, что этот путь записи остаётся узким: респондент не
становится участником, отвечать может только на вынесенное на оценку, и ничего
из отвеченного не уезжает ни в статику, ни в журнал ревизий.
"""

import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import publisher  # noqa: E402
from _auth_helpers import anon, login  # noqa: E402

DOC = "surveydoc"
PAGE = "42"
PAGE_KEY = "042"


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(config, "BOOTSTRAP_INVITE_CODE", "")
    monkeypatch.setattr(config, "DB_PATH", os.path.join(tmp_path, "redpen.db"))
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    db.init_db()
    yield
    if db._conn is not None:
        db._conn.close()
    db._conn = None


def _admin(monkeypatch, sub="survey-admin"):
    return login(monkeypatch, sub, role="admin")


def _create(client, remark_id="r-1", text="текст замечания"):
    r = client.post(f"/api/editor/{DOC}/{PAGE}",
                    json={"id": remark_id, "kind": "minor", "text": text,
                          "coords": [10, 20], "status": "published"})
    assert r.status_code == 200, r.text
    return remark_id


def _pool(admin, remark_id="r-1"):
    r = admin.post("/api/survey/pool",
                   json={"docId": DOC, "pageKey": PAGE, "remarkId": remark_id})
    assert r.status_code == 200, r.text
    return r.json()["item"]


def _respondent(pseudonym="Прохожий"):
    client = anon()
    r = client.post("/api/survey/session", json={"pseudonym": pseudonym})
    assert r.status_code == 200, r.text
    body = r.json()
    client.headers.update({"X-Survey-Token": body["token"]})
    return client, body


def _answer(client, remark_id="r-1", **scales):
    payload = {"docId": DOC, "pageKey": PAGE, "remarkId": remark_id}
    payload.update(scales)
    return client.put("/api/survey/ratings", json=payload)


# --- вход в опрос ----------------------------------------------------------

def test_session_needs_no_invite_and_yields_a_token():
    """Единственный путь записи, открытый без приглашения, — и он открыт."""
    client, body = _respondent("Прохожий")
    assert body["pseudonym"] == "Прохожий"
    assert body["author"] == "anonymous:Прохожий"
    assert len(body["token"]) >= 32


def test_respondent_is_not_a_user():
    """Круг участников остаётся закрытым: респондент в `users` не появляется."""
    _respondent("Прохожий")
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM survey_respondents").fetchone()[0] == 1


@pytest.mark.parametrize("pseudonym", ["", "a", "x" * 40, "anonymous:Пётр", "ко\x00т", None])
def test_bad_pseudonym_is_refused(pseudonym):
    assert anon().post("/api/survey/session",
                       json={"pseudonym": pseudonym}).status_code == 400


def test_prefix_is_not_stored_and_cannot_be_forged():
    """Подпись собирается на чтении: подставить чужой префикс вводом нельзя."""
    _, body = _respondent("Пётр")
    stored = db.get_connection().execute(
        "SELECT pseudonym FROM survey_respondents").fetchone()[0]
    assert stored == "Пётр"
    assert body["author"] == "anonymous:Пётр"


def test_same_pseudonym_is_two_respondents():
    """Псевдоним — подпись, а не личность: второй под тем же именем не должен
    видеть и переписывать ответы первого."""
    _, first = _respondent("Тёзка")
    _, second = _respondent("Тёзка")
    assert first["token"] != second["token"]
    assert db.get_connection().execute(
        "SELECT COUNT(*) FROM survey_respondents").fetchone()[0] == 2


def test_survey_routes_need_a_token(monkeypatch):
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    stranger = anon()
    assert stranger.get("/api/survey/batch").status_code == 401
    assert _answer(stranger, interest=3).status_code == 401
    stranger.headers.update({"X-Survey-Token": "0" * 64})
    assert stranger.get("/api/survey/batch").status_code == 401


# --- выдача порции ---------------------------------------------------------

def test_batch_serves_only_pooled_remarks(monkeypatch):
    admin = _admin(monkeypatch)
    _create(admin, "r-1")
    _create(admin, "r-2")
    _pool(admin, "r-1")
    client, _ = _respondent()
    body = client.get("/api/survey/batch").json()
    assert [i["remarkId"] for i in body["items"]] == ["r-1"]
    assert body["remaining"] == 1


def test_batch_is_capped_and_excludes_what_was_already_answered(monkeypatch):
    admin = _admin(monkeypatch)
    for n in range(12):
        _create(admin, f"r-{n}")
        _pool(admin, f"r-{n}")
    client, _ = _respondent()
    first = client.get("/api/survey/batch").json()
    assert len(first["items"]) == 10  # десять за заход, а не весь пул
    assert first["remaining"] == 12

    for item in first["items"]:
        assert _answer(client, item["remarkId"], interest=3).status_code == 200
    second = client.get("/api/survey/batch").json()
    assert second["remaining"] == 2
    answered = {i["remarkId"] for i in first["items"]}
    assert not answered & {i["remarkId"] for i in second["items"]}


def test_batch_of_another_respondent_is_untouched(monkeypatch):
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    first, _ = _respondent("Первый")
    second, _ = _respondent("Второй")
    _answer(first, interest=3)
    assert first.get("/api/survey/batch").json()["remaining"] == 0
    assert second.get("/api/survey/batch").json()["remaining"] == 1


# --- ответы ----------------------------------------------------------------

def test_all_three_scales_are_saved_at_once(monkeypatch):
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    client, _ = _respondent()
    r = _answer(client, interest=4, importance=5, admissibility=2)
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == ["admissibility", "importance", "interest"]
    results = admin.get("/api/survey/results").json()["items"]
    assert results[0]["interest"] == {"count": 1, "average": 4.0}
    assert results[0]["admissibility"] == {"yes": 1, "no": 0}


def test_answering_again_corrects_rather_than_adds(monkeypatch):
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    client, _ = _respondent()
    _answer(client, interest=1)
    _answer(client, interest=5)
    results = admin.get("/api/survey/results").json()["items"]
    assert results[0]["interest"] == {"count": 1, "average": 5.0}
    assert results[0]["raters"] == 1


def test_admissibility_is_binary(monkeypatch):
    """«Можно ли публиковать» — вопрос о решении: середины у него нет."""
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    client, _ = _respondent()
    assert _answer(client, admissibility=2).status_code == 200
    assert _answer(client, admissibility=3).status_code == 400
    assert _answer(client, interest=3).status_code == 200


def test_empty_answer_is_refused(monkeypatch):
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    client, _ = _respondent()
    assert _answer(client).status_code == 400


def test_answering_outside_the_pool_is_refused(monkeypatch):
    """Иначе опрос — анонимная запись по любому адресу, а не опрос."""
    admin = _admin(monkeypatch)
    _create(admin)
    client, _ = _respondent()
    assert _answer(client, interest=3).status_code == 403
    assert _answer(client, "nope", interest=3).status_code == 404


# --- управление пулом ------------------------------------------------------

def test_pool_is_editors_work_and_results_are_admins(monkeypatch):
    """Вынести замечание на оценку — обычная редакторская работа. Сводка
    ответов — нет: это агрегированные мнения анонимных респондентов."""
    admin = _admin(monkeypatch)
    _create(admin)
    editor = login(monkeypatch, "survey-editor", role="editor")
    assert editor.post("/api/survey/pool",
                       json={"docId": DOC, "pageKey": PAGE, "remarkId": "r-1"}).status_code == 200
    assert editor.get("/api/survey/pool").json()["total"] == 1
    assert editor.delete(f"/api/survey/pool/{DOC}/{PAGE}/r-1").status_code == 200

    assert editor.get("/api/survey/results").status_code == 403
    viewer = login(monkeypatch, "survey-viewer", role="viewer")
    assert viewer.get("/api/survey/pool").status_code == 403
    assert viewer.post("/api/survey/pool",
                       json={"docId": DOC, "pageKey": PAGE, "remarkId": "r-1"}).status_code == 403
    assert anon().get("/api/survey/pool").status_code == 401


def test_membership_is_visible_on_the_remark(monkeypatch):
    """Без этого признака кнопка «в опрос» работает в один конец: положить
    можно, а узнать, что замечание уже там, — нет."""
    admin = _admin(monkeypatch)
    _create(admin)

    one = admin.get(f"/api/remarks/{DOC}/{PAGE}/r-1").json()["remark"]
    assert one["inPool"] is False and one["poolAnswers"] == 0
    assert admin.get("/api/remarks").json()["items"][0]["inPool"] is False

    _pool(admin)
    client, _ = _respondent()
    _answer(client, interest=4)

    one = admin.get(f"/api/remarks/{DOC}/{PAGE}/r-1").json()["remark"]
    assert one["inPool"] is True and one["poolAnswers"] == 1
    assert admin.get("/api/remarks").json()["items"][0]["poolAnswers"] == 1

    # Изъятие из пула ответы не трогает — и признак это показывает честно.
    admin.delete(f"/api/survey/pool/{DOC}/{PAGE}/r-1")
    one = admin.get(f"/api/remarks/{DOC}/{PAGE}/r-1").json()["remark"]
    assert one["inPool"] is False and one["poolAnswers"] == 0


def test_remarks_can_be_filtered_by_membership(monkeypatch):
    admin = _admin(monkeypatch)
    _create(admin, "r-1")
    _create(admin, "r-2")
    _pool(admin, "r-1")

    def ids(**params):
        r = admin.get("/api/remarks", params=params).json()
        assert r["total"] == len(r["items"])
        return sorted(i["remarkId"] for i in r["items"])

    assert ids(inPool="true") == ["r-1"]
    assert ids(inPool="false") == ["r-2"]
    assert ids() == ["r-1", "r-2"]


def test_pool_add_is_idempotent_and_lists_the_text(monkeypatch):
    admin = _admin(monkeypatch)
    _create(admin, text="проверяемое утверждение")
    _pool(admin)
    _pool(admin)
    body = admin.get("/api/survey/pool").json()
    assert body["total"] == 1
    assert body["items"][0]["text"] == "проверяемое утверждение"
    assert body["items"][0]["answers"] == 0


def test_removing_from_the_pool_keeps_the_answers(monkeypatch):
    """Снять вопрос с раздачи и стереть ответы — разные действия."""
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    client, _ = _respondent()
    _answer(client, interest=4)
    assert admin.delete(f"/api/survey/pool/{DOC}/{PAGE}/r-1").status_code == 200
    assert admin.delete(f"/api/survey/pool/{DOC}/{PAGE}/r-1").status_code == 404
    assert admin.get("/api/survey/pool").json()["total"] == 0
    assert admin.get("/api/survey/results").json()["items"][0]["interest"]["count"] == 1


def test_pool_rejects_an_unknown_remark(monkeypatch):
    admin = _admin(monkeypatch)
    assert admin.post("/api/survey/pool",
                      json={"docId": DOC, "pageKey": PAGE, "remarkId": "r-1"}).status_code == 404


# --- результаты попадают в ленту, но никуда больше ------------------------

def test_answer_shows_up_in_the_timeline(monkeypatch):
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    client, _ = _respondent("Прохожий")
    _answer(client, interest=4, admissibility=1)

    items = admin.get(f"/api/remarks/{DOC}/{PAGE}/r-1/timeline").json()["items"]
    answers = [i for i in items if i.get("source") == "survey"]
    assert len(answers) == 2
    assert {a["scale"] for a in answers} == {"interest", "admissibility"}
    assert all(a["actorName"] == "anonymous:Прохожий" for a in answers)
    # Респондента нет в `users`, и actorId ему взять неоткуда.
    assert all(a["actorId"] is None for a in answers)


def test_answer_is_not_a_revision_and_not_an_editor_rating(monkeypatch):
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    conn = db.get_connection()
    before = conn.execute("SELECT COUNT(*) FROM remark_history").fetchone()[0]
    client, _ = _respondent()
    _answer(client, interest=4, importance=2, admissibility=2)
    assert conn.execute("SELECT COUNT(*) FROM remark_history").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM remark_ratings").fetchone()[0] == 0
    # Сводка редакторских оценок ответов опроса не видит: два разных голоса.
    summary = admin.get(f"/api/remarks/{DOC}/{PAGE}/r-1/ratings").json()["summary"]
    assert summary["interest"]["count"] == 0


def test_nothing_from_the_survey_reaches_the_static_files(monkeypatch):
    """Главный инвариант: ни пул, ни ответы в опубликованное не текут, и хеш
    страницы от них не сдвигается — иначе редактор получил бы 409 на пустом
    месте."""
    admin = _admin(monkeypatch)
    _create(admin)
    before = publisher.compute_page_sha(publisher.render_page(DOC, PAGE_KEY))
    _pool(admin)
    client, _ = _respondent("Прохожий")
    _answer(client, interest=5, importance=5, admissibility=2)

    rendered = publisher.render_page(DOC, PAGE_KEY)
    assert publisher.compute_page_sha(rendered) == before
    static = publisher.render_page_static(DOC, PAGE_KEY)
    blob = repr(rendered) + repr(static)
    assert "anonymous" not in blob
    assert "Прохожий" not in blob
    # inPool — редакторское сведение: в статику не течёт ни под каким именем.
    assert "inPool" not in blob and "poolAnswers" not in blob
    for item in list(rendered) + list(static):
        assert set(item) <= {"id", "text", "annType", "kind", "coords", "tags",
                             "category", "draft"}
