"""Прогоны агентов: авторство машинных правок и групповой откат.

Агент — такой же актор, как человек, но за его правкой стоит прогон: версия
агента, промпт и модель. Без этого «автор» машинной правки — просто токен, и
вопрос «откуда взялась эта формулировка» остаётся без ответа.
"""

import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_path = os.path.join(tmp_path, "redpen.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db()
    yield
    if db._conn is not None:
        db._conn.close()
    db._conn = None


def _agent(name="classifier"):
    conn = db.get_connection()
    with db._lock:
        cur = conn.execute(
            "INSERT INTO users (role, created_at, kind, display_name) "
            "VALUES ('editor', '2026-08-21T00:00:00', 'agent', ?)",
            (name,),
        )
        conn.commit()
    return cur.lastrowid


def _run(actor_id, **kwargs):
    base = dict(agent_name="classifier", agent_version="v1", model="test-model",
                prompt_path="docs/category-agent-prompt.md", prompt_sha256="abc123",
                doc_id="doc1", section_id="20")
    base.update(kwargs)
    return db.start_agent_run(actor_id, **base)


def test_run_records_what_produced_the_edit():
    run = _run(_agent())
    assert run["agentVersion"] == "v1"
    assert run["promptSha256"] == "abc123"
    assert run["status"] == "running"
    assert run["finishedAt"] is None

    done = db.finish_agent_run(run["id"], notes="42 аннотации")
    assert done["status"] == "done"
    assert done["finishedAt"] is not None
    assert done["notes"] == "42 аннотации"


def test_revisions_point_back_at_the_run():
    actor = _agent()
    run = _run(actor)
    db.upsert_remark_db("doc1", "006", "a1", "major", "t", action="create",
                            author_id=actor, agent_run_id=run["id"],
                            summary="создано агентом")
    revisions = db.list_run_revisions(run["id"])
    assert len(revisions) == 1
    assert revisions[0]["remarkId"] == "a1"
    assert revisions[0]["summary"] == "создано агентом"

    assert db.list_agent_runs(actor_id=actor)[0]["revisionCount"] == 1


def test_revert_plan_restores_what_existed_before_the_run():
    actor = _agent()
    db.upsert_remark_db("doc1", "006", "a1", "major", "исходный текст",
                            action="create", category="omission", author_id=1)
    run = _run(actor)
    db.upsert_remark_db("doc1", "006", "a1", "major", "переписано агентом",
                            action="update", category="today",
                            category_source="agent", author_id=actor,
                            agent_run_id=run["id"])

    plan = db.plan_agent_run_revert(run["id"])
    assert len(plan) == 1
    assert plan[0]["action"] == "restore"
    assert plan[0]["snapshot"]["text"] == "исходный текст"
    assert plan[0]["snapshot"]["category"] == "omission"


def test_revert_plan_deletes_what_the_run_created():
    actor = _agent()
    run = _run(actor)
    db.upsert_remark_db("doc1", "006", "new-1", "major", "новая от агента",
                            action="create", author_id=actor, agent_run_id=run["id"])
    plan = db.plan_agent_run_revert(run["id"])
    assert plan[0]["action"] == "delete"
    assert plan[0]["targetRevId"] is None


def test_revert_plan_covers_every_touched_remark_once():
    actor = _agent()
    run = _run(actor)
    for remark_id in ("a1", "a2"):
        db.upsert_remark_db("doc1", "006", remark_id, "major", "v1", action="create",
                                author_id=actor, agent_run_id=run["id"])
        # Прогон трогал одну и ту же аннотацию дважды — в плане она одна.
        db.upsert_remark_db("doc1", "006", remark_id, "major", "v2", author_id=actor,
                                agent_run_id=run["id"])
    plan = db.plan_agent_run_revert(run["id"])
    assert [item["remarkId"] for item in plan] == ["a1", "a2"]


def test_edits_outside_the_run_are_untouched_by_the_plan():
    actor = _agent()
    run = _run(actor)
    db.upsert_remark_db("doc1", "006", "a1", "major", "агент", action="create",
                            author_id=actor, agent_run_id=run["id"])
    db.upsert_remark_db("doc1", "006", "human-1", "major", "человек", action="create",
                            author_id=1)
    assert [item["remarkId"] for item in db.plan_agent_run_revert(run["id"])] == ["a1"]
