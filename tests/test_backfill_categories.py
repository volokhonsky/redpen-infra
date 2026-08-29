"""Бэкфилл категорий: что считается решением, а что — нет.

Ключевое различие всей приёмки: «Прочее», выбранное осознанно, и «Прочее»,
которое просто никто не трогал. По значению они неотличимы, различает их
`category_source`.
"""

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "scripts", "api", "backfill_categories.py")


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_path = os.path.join(tmp_path, "redpen.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "PUBLISH_DIR", "")
    monkeypatch.setattr(config, "BOOTSTRAP_INVITE_CODE", "")
    db.init_db()
    yield db_path
    db._conn.close()
    db._conn = None


def _run(db_path, decisions, tmp_path, *extra):
    path = os.path.join(tmp_path, "decisions.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(decisions, f, ensure_ascii=False)
    env = dict(os.environ, DB_PATH=db_path, PUBLISH_DIR="")
    result = subprocess.run(
        [sys.executable, SCRIPT, "--from-file", path, "--apply", "--no-publish", *extra],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert result.returncode == 0, result.stdout
    return result.stdout


def _state(remark_id):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT category, category_source FROM remarks WHERE remark_id = ?", (remark_id,)
    ).fetchone()
    return row["category"], row["category_source"]


def test_decision_is_recorded_even_when_the_value_does_not_change(tmp_path, _fresh_db):
    """Осознанное «Прочее» перестаёт быть «не разобрано».

    Тот самый случай, который скрипт раньше считал «без изменений» и молча
    оставлял в очереди разбора навсегда.
    """
    db.upsert_remark_db("doc1", "006", "a1", "major", "пояснение", action="create")
    assert _state("a1") == ("other", "default")

    out = _run(_fresh_db, [{"docId": "doc1", "pageNum": "006", "remarkId": "a1",
                            "category": "other", "why": "просто дополнение"}], tmp_path)
    assert "подтверждено" in out
    assert _state("a1") == ("other", "agent")


def test_real_change_moves_both_value_and_source(tmp_path, _fresh_db):
    db.upsert_remark_db("doc1", "006", "a2", "major", "текст", action="create")
    _run(_fresh_db, [{"docId": "doc1", "pageNum": "006", "remarkId": "a2",
                      "category": "omission", "why": "факт вынут"}], tmp_path)
    assert _state("a2") == ("omission", "agent")


def test_already_decided_is_left_alone(tmp_path, _fresh_db):
    """Решение человека повторным прогоном не перетирается."""
    db.upsert_remark_db("doc1", "006", "a3", "major", "текст", action="create",
                            category="other", author_id=1)
    assert _state("a3") == ("other", "human")

    out = _run(_fresh_db, [{"docId": "doc1", "pageNum": "006", "remarkId": "a3",
                            "category": "other", "why": "то же самое"}], tmp_path)
    assert "без изменений" in out
    assert _state("a3") == ("other", "human")


def test_only_default_skips_what_someone_already_decided(tmp_path, _fresh_db):
    db.upsert_remark_db("doc1", "006", "a4", "major", "текст", action="create",
                            category="today", author_id=1)
    _run(_fresh_db, [{"docId": "doc1", "pageNum": "006", "remarkId": "a4",
                      "category": "omission", "why": "переклассификация"}],
         tmp_path, "--only-default")
    assert _state("a4") == ("today", "human")
