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


def test_same_pseudonym_is_one_respondent_and_two_sessions():
    """Псевдоним — это респондент, заход — сессия (2026-09-01). Вернувшийся под
    тем же именем продолжает свой опрос, а не начинает новый."""
    _, first = _respondent("Тёзка")
    _, second = _respondent("Тёзка")
    assert first["token"] != second["token"]
    assert first["sessionId"] != second["sessionId"]
    assert first["respondentId"] == second["respondentId"]
    assert first["returning"] is False and second["returning"] is True
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM survey_respondents").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM survey_sessions").fetchone()[0] == 2


def test_pseudonym_matches_exactly():
    """«Пётр» и «пётр» — разные респонденты: сводить написания значило бы
    гадать о намерении и отдавать одному чужие ответы. Пробелы по краям и
    внутри — не написание, а ввод, их схлопывает normalize_pseudonym."""
    _, first = _respondent("Пётр")
    _, other = _respondent("пётр")
    assert other["respondentId"] != first["respondentId"]
    assert other["pseudonym"] == "пётр"
    assert other["author"] == "anonymous:пётр"
    _, same = _respondent("  Пётр  ")
    assert same["respondentId"] == first["respondentId"]
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


def test_a_new_session_does_not_serve_what_the_pseudonym_already_answered(monkeypatch):
    """Ради чего разделены псевдоним и сессия: вернувшемуся не раздают заново."""
    admin = _admin(monkeypatch)
    for n in range(2):
        _create(admin, f"r-{n}")
        _pool(admin, f"r-{n}")
    first, _ = _respondent("Пётр")
    _answer(first, "r-0", interest=3)

    again, _ = _respondent("Пётр")           # тот же человек, новый заход
    batch = again.get("/api/survey/batch").json()
    assert batch["remaining"] == 1
    assert [i["remarkId"] for i in batch["items"]] == ["r-1"]

    other, _ = _respondent("Иван")           # другое имя — выдача полная
    assert other.get("/api/survey/batch").json()["remaining"] == 2


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
    _answer(client, interest=5, importance=5, admissibility=2,
            comment="STATIC-LEAK-CANARY-текст-возражения")

    rendered = publisher.render_page(DOC, PAGE_KEY)
    assert publisher.compute_page_sha(rendered) == before
    static = publisher.render_page_static(DOC, PAGE_KEY)
    blob = repr(rendered) + repr(static)
    assert "anonymous" not in blob
    assert "Прохожий" not in blob
    assert "STATIC-LEAK-CANARY" not in blob
    # inPool — редакторское сведение: в статику не течёт ни под каким именем.
    assert "inPool" not in blob and "poolAnswers" not in blob
    for item in list(rendered) + list(static):
        assert set(item) <= {"id", "text", "annType", "kind", "coords", "tags",
                             "category", "draft"}


# --- кто отвечал: список и удаление ----------------------------------------

def test_admin_sees_who_answered_without_any_token(monkeypatch):
    """Список отвечавших — админский, и токена в нём нет: в базе от токена
    остаётся только хеш, и наружу не выходит даже он."""
    admin = _admin(monkeypatch)
    for n in range(2):
        _create(admin, f"r-{n}")
        _pool(admin, f"r-{n}")
    petr, _ = _respondent("Пётр")
    _answer(petr, "r-0", interest=3, importance=4)
    again, _ = _respondent("Пётр")
    _answer(again, "r-1", interest=5)
    _respondent("Иван")            # назвался и ушёл, ничего не ответив

    data = admin.get("/api/survey/respondents").json()
    assert data["total"] == 2
    by_name = {i["pseudonym"]: i for i in data["items"]}
    assert by_name["Пётр"]["sessions"] == 2
    assert by_name["Пётр"]["answers"] == 3      # три строки по шкалам
    assert by_name["Пётр"]["remarks"] == 2
    assert by_name["Пётр"]["author"] == "anonymous:Пётр"
    assert by_name["Иван"] == {**by_name["Иван"], "sessions": 1, "answers": 0}
    assert "token" not in repr(data) and "token_hash" not in repr(data)

    sessions = admin.get(
        f"/api/survey/respondents/{by_name['Пётр']['id']}/sessions").json()["items"]
    assert [s["answers"] for s in sessions] == [1, 2]   # свежая сверху
    assert "token" not in repr(sessions)


def test_two_sessions_of_one_pseudonym_are_one_voice(monkeypatch):
    """Ключ ответа посессионный, поэтому строк две. Но в сводке человек один,
    и считается его последнее слово."""
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    first, _ = _respondent("Пётр")
    _answer(first, interest=1, admissibility=1)
    again, _ = _respondent("Пётр")
    _answer(again, interest=5, admissibility=2)

    assert db.get_connection().execute(
        "SELECT COUNT(*) FROM survey_answers").fetchone()[0] == 4
    item = admin.get("/api/survey/results").json()["items"][0]
    assert item["raters"] == 1
    assert item["interest"] == {"count": 1, "average": 5.0}
    assert item["admissibility"] == {"yes": 1, "no": 0}
    # В ленте замечания видны оба события: она показывает не сводку.
    timeline = admin.get(f"/api/remarks/{DOC}/{PAGE}/r-1/timeline").json()["items"]
    interest = [i for i in timeline if i.get("scale") == "interest"]
    assert sorted(i["value"] for i in interest) == [1, 5]


def test_deleting_a_session_keeps_the_other_one(monkeypatch):
    admin = _admin(monkeypatch)
    for n in range(2):
        _create(admin, f"r-{n}")
        _pool(admin, f"r-{n}")
    first, _ = _respondent("Пётр")
    _answer(first, "r-0", interest=3)
    again, body = _respondent("Пётр")
    _answer(again, "r-1", interest=4)

    r = admin.delete(f"/api/survey/sessions/{body['sessionId']}")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"]["answers"] == 1

    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM survey_sessions").fetchone()[0] == 1
    assert [row[0] for row in conn.execute(
        "SELECT remark_id FROM survey_answers")] == ["r-0"]
    # Псевдоним цел: у него остался первый заход.
    assert conn.execute("SELECT COUNT(*) FROM survey_respondents").fetchone()[0] == 1
    # Стёртая сессия больше не опознаётся — открытая вкладка вернётся к имени.
    assert again.get("/api/survey/batch").status_code == 401
    assert first.get("/api/survey/batch").status_code == 200


def test_deleting_a_pseudonym_takes_all_its_sessions(monkeypatch):
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    petr, _ = _respondent("Пётр")
    _answer(petr, interest=3)
    _respondent("Пётр")
    ivan, _ = _respondent("Иван")
    _answer(ivan, interest=5)

    target = [i for i in admin.get("/api/survey/respondents").json()["items"]
              if i["pseudonym"] == "Пётр"][0]
    r = admin.delete(f"/api/survey/respondents/{target['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == {"answers": 1, "sessions": 2, "pseudonym": "Пётр"}

    conn = db.get_connection()
    assert [row[0] for row in conn.execute(
        "SELECT pseudonym FROM survey_respondents")] == ["Иван"]
    assert conn.execute("SELECT COUNT(*) FROM survey_sessions").fetchone()[0] == 1
    # Чужие ответы целы, и сводка пересчиталась по ним.
    item = admin.get("/api/survey/results").json()["items"][0]
    assert item["raters"] == 1 and item["interest"]["average"] == 5.0


def test_deleting_a_session_writes_no_revision(monkeypatch):
    """Ответы опроса ревизиями замечания не были — и их удаление тоже не ревизия."""
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    client, body = _respondent("Пётр")
    _answer(client, interest=3)
    conn = db.get_connection()
    before = conn.execute("SELECT COUNT(*) FROM remark_history").fetchone()[0]
    assert admin.delete(f"/api/survey/sessions/{body['sessionId']}").status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM remark_history").fetchone()[0] == before


def test_who_answered_is_admins_only(monkeypatch):
    """Та же граница, что у сводки: редактор ставит вопросы, но не видит, кто
    и как ответил (docs/anonymity-model.md)."""
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    client, body = _respondent("Пётр")
    _answer(client, interest=3)
    target = admin.get("/api/survey/respondents").json()["items"][0]["id"]

    editor = login(monkeypatch, "survey-editor", role="editor")
    assert editor.get("/api/survey/respondents").status_code == 403
    assert editor.delete(f"/api/survey/sessions/{body['sessionId']}").status_code == 403
    assert editor.delete(f"/api/survey/respondents/{target}").status_code == 403
    assert anon().get("/api/survey/respondents").status_code == 401

    assert admin.delete("/api/survey/sessions/9999").status_code == 404
    assert admin.delete("/api/survey/respondents/9999").status_code == 404


# --- открытый ответ --------------------------------------------------------

def test_questions_describe_both_kinds_of_answer(monkeypatch):
    """Опросник рисует карточку по одному списку `questions`: у каждого пункта
    `answer` говорит, кнопки это или поле ввода. Отдельного ключа `scales`
    больше нет — прежний опросник в бою его и не видел."""
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    client, body = _respondent()
    assert "scales" not in body
    by_name = {q["name"]: q for q in body["questions"]}
    assert by_name["interest"]["answer"] == "value"
    assert by_name["comment"]["answer"] == "text"
    assert by_name["comment"]["maxLength"] == 1000

    batch = client.get("/api/survey/batch").json()
    assert "scales" not in batch
    assert {q["name"] for q in batch["questions"]} == set(by_name)


def test_open_answer_is_saved_with_the_ratings_and_overwrites(monkeypatch):
    """Текст приходит тем же вызовом, что и цифры, и в ленте встаёт
    комментарием, а не оценкой: у него нет значения, которое можно усреднить."""
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    client, _ = _respondent("Прохожий")
    assert _answer(client, interest=4, comment="слишком резко").status_code == 200
    # Рабочий комментарий рядом — чтобы проверка источников была не пустой.
    assert admin.post(f"/api/remarks/{DOC}/{PAGE}/r-1/notes",
                      json={"body": "обсудим на летучке"}).status_code == 200

    tl = admin.get(f"/api/remarks/{DOC}/{PAGE}/r-1/timeline").json()["items"]
    notes = [i for i in tl if i["kind"] == "note" and i.get("source") == "survey"]
    assert len(notes) == 1
    assert notes[0]["body"] == "слишком резко"
    assert notes[0]["actorName"] == "anonymous:Прохожий"
    assert notes[0]["actorId"] is None
    # Рабочий тред помечен своим источником — иначе два вида `note` не различить.
    editor_notes = [i for i in tl if i["kind"] == "note" and i.get("source") == "editor"]
    assert [i["body"] for i in editor_notes] == ["обсудим на летучке"]

    # Повтор внутри захода — исправление, а не вторая мысль.
    assert _answer(client, interest=4, comment="точнее: голословно").status_code == 200
    res = admin.get("/api/survey/results").json()["items"][0]
    assert res["commentsN"] == 1
    assert res["comments"][0]["text"] == "точнее: голословно"
    assert res["comments"][0]["author"] == "anonymous:Прохожий"


def test_comments_of_two_sessions_are_both_kept(monkeypatch):
    """Цифру второго захода сводка заменяет, текст — нет: вторая мысль не
    поправка к первой, и терять её ради аккуратности таблицы незачем."""
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    first, _ = _respondent("Прохожий")
    _answer(first, interest=2, comment="первая мысль")
    again, _ = _respondent("Прохожий")
    _answer(again, interest=5, comment="вторая мысль")

    res = admin.get("/api/survey/results").json()["items"][0]
    assert res["raters"] == 1                    # голос один
    assert res["interest"]["average"] == 5.0     # последний
    assert res["commentsN"] == 2
    assert [c["text"] for c in res["comments"]] == ["первая мысль", "вторая мысль"]
    assert {c["author"] for c in res["comments"]} == {"anonymous:Прохожий"}


def test_open_answer_length_limit(monkeypatch):
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    client, _ = _respondent()
    assert _answer(client, interest=3, comment="x" * 1000).status_code == 200
    assert _answer(client, interest=3, comment="x" * 1001).status_code == 400


def test_open_answer_missing_key_keeps_it_empty_string_deletes(monkeypatch):
    """Семантика тегов: ключа нет — не трогать, `""` — стереть."""
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    client, _ = _respondent()

    _answer(client, interest=3, comment="черновик мысли")
    assert admin.get("/api/survey/results").json()["items"][0]["commentsN"] == 1

    _answer(client, interest=3)
    assert admin.get("/api/survey/results").json()["items"][0]["commentsN"] == 1

    _answer(client, interest=3, comment="")
    assert admin.get("/api/survey/results").json()["items"][0]["commentsN"] == 0


def test_comment_without_a_single_rating_is_refused(monkeypatch):
    """Карточка оценивается целиком: одинокий текст не дал бы ни строки в
    сводку. Отказ полный — не пишется и он сам."""
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    client, _ = _respondent()
    assert _answer(client, comment="только текст").status_code == 400
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM survey_answers").fetchone()[0] == 0


def test_a_commented_remark_is_not_served_again(monkeypatch):
    """Открытый ответ — тоже ответ: замечание с ним из раздачи уходит."""
    admin = _admin(monkeypatch)
    _create(admin)
    _pool(admin)
    client, _ = _respondent("Прохожий")
    _answer(client, interest=3, comment="сказано")
    again, _ = _respondent("Прохожий")
    assert again.get("/api/survey/batch").json()["remaining"] == 0


# --- хвост пула ------------------------------------------------------------

def test_tail_flag_when_remainder_fits_a_batch(monkeypatch):
    """`tail` означает «это весь непросмотренный остаток» — опросник говорит
    человеку, что дальше ничего нет, вместо бодрого «оценить ещё десять»."""
    admin = _admin(monkeypatch)
    for n in range(3):
        _create(admin, f"r-{n}")
        _pool(admin, f"r-{n}")
    client, _ = _respondent()
    batch = client.get("/api/survey/batch").json()
    assert batch["tail"] is True
    assert len(batch["items"]) == 3 and batch["remaining"] == 3

    for item in batch["items"]:
        _answer(client, item["remarkId"], interest=3)
    done = client.get("/api/survey/batch").json()
    assert done["items"] == [] and done["remaining"] == 0 and done["tail"] is True


def test_no_tail_flag_while_the_pool_is_deep(monkeypatch):
    admin = _admin(monkeypatch)
    for n in range(12):
        _create(admin, f"r-{n}")
        _pool(admin, f"r-{n}")
    client, _ = _respondent()
    batch = client.get("/api/survey/batch").json()
    assert batch["tail"] is False
    assert len(batch["items"]) == 10 and batch["remaining"] == 12
