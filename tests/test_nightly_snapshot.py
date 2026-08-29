"""Ночной снапшот БД → git-клон redpen-publish.

Канон аннотаций — SQLite; гит держит переносимую копию. Между выкладками
снапшот отстаёт от базы, и сборка из устаревшего клона раскатала бы старые
страницы поверх правильных. Ночной прогон эту ловушку закрывает.
"""

import json
import os
import subprocess

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import nightly_snapshot  # noqa: E402

DOC = "snapdoc"

MANIFEST = {
    "title": "Тестовый учебник",
    "chapters": [{"id": "chapter_I", "name": "Глава I", "sections": [
        {"id": "1", "name": "§ 1. Раздел", "startPage": 6, "endPage": 20}]}],
    "pages": [{"file": "page_006", "label": "6"}, {"file": "page_007", "label": "7"}],
}


def _git(repo, *args):
    return subprocess.run(["git"] + list(args), cwd=repo, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    if db._conn is not None:
        db._conn.close()
        db._conn = None
    monkeypatch.setattr(config, "DB_PATH", os.path.join(tmp_path, "redpen.db"))
    monkeypatch.setattr(config, "PUBLISH_DIR", "")  # публикация в том выключена

    path = os.path.join(tmp_path, "redpen-publish")
    os.makedirs(os.path.join(path, DOC))
    with open(os.path.join(path, DOC, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(MANIFEST, f, ensure_ascii=False)
    _git(path, "init", "-q")
    _git(path, "add", "-A")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    db.init_db()
    yield path
    db._conn.close()
    db._conn = None


def _run(repo, **kwargs):
    import sys
    argv = ["nightly_snapshot.py", "--repo", repo]
    for key, value in kwargs.items():
        if value:
            argv.append("--" + key.replace("_", "-"))
    old, sys.argv = sys.argv, argv
    try:
        return nightly_snapshot.main()
    finally:
        sys.argv = old


def test_dry_run_writes_nothing(repo):
    db.upsert_remark_db(DOC, "006", "a1", "major", "текст", coord_x=1, coord_y=1,
                            action="create")
    assert _run(repo) == 0
    assert not os.path.exists(os.path.join(repo, DOC, "remarks", "page_006.json"))
    assert _git(repo, "status", "--porcelain") == ""


def test_apply_exports_json_and_rebuilds_pages(repo):
    db.upsert_remark_db(DOC, "006", "a1", "major", "видно читателю", coord_x=1, coord_y=1,
                            action="create", category="omission")
    assert _run(repo, apply=True) == 0

    with open(os.path.join(repo, DOC, "remarks", "page_006.json"), encoding="utf-8") as f:
        assert json.load(f)[0]["category"] == "omission"
    with open(os.path.join(repo, DOC, "pages", "6", "index.html"), encoding="utf-8") as f:
        assert "видно читателю" in f.read()


def test_apply_commits_once_with_a_dated_message(repo):
    db.upsert_remark_db(DOC, "006", "a1", "major", "текст", coord_x=1, coord_y=1,
                            action="create")
    _run(repo, apply=True)
    assert _git(repo, "status", "--porcelain") == ""
    assert "chore(snapshot)" in _git(repo, "log", "-1", "--pretty=%s")
    # Автор — бот, а не человек: авторства людей в публичном репозитории быть
    # не должно (docs/anonymity-model.md).
    assert _git(repo, "log", "-1", "--pretty=%an") == "redpen-bot"


def test_second_run_without_changes_commits_nothing(repo):
    db.upsert_remark_db(DOC, "006", "a1", "major", "текст", coord_x=1, coord_y=1,
                            action="create")
    _run(repo, apply=True)
    before = _git(repo, "rev-parse", "HEAD")
    assert _run(repo, apply=True) == 0
    assert _git(repo, "rev-parse", "HEAD") == before


def test_untouched_pages_keep_their_stamp(repo):
    db.upsert_remark_db(DOC, "006", "a1", "major", "стр 6", coord_x=1, coord_y=1,
                            action="create")
    db.upsert_remark_db(DOC, "007", "b1", "major", "стр 7", coord_x=1, coord_y=1,
                            action="create")
    _run(repo, apply=True)

    page7 = os.path.join(repo, DOC, "pages", "7", "index.html")
    with open(page7, encoding="utf-8") as f:
        before = f.read()

    # Меняется только шестая страница; седьмую не должно перезаписать даже меткой.
    db.upsert_remark_db(DOC, "006", "a1", "major", "стр 6 исправлено",
                            coord_x=1, coord_y=1)
    _run(repo, apply=True)

    with open(page7, encoding="utf-8") as f:
        assert f.read() == before
    changed = _git(repo, "show", "--name-only", "--pretty=", "HEAD").splitlines()
    assert all("/pages/7/" not in name for name in changed), changed


def test_deletion_reaches_the_snapshot(repo):
    db.upsert_remark_db(DOC, "006", "a1", "major", "исчезнет", coord_x=1, coord_y=1,
                            action="create")
    _run(repo, apply=True)
    db.soft_delete_remark(DOC, "006", "a1")
    _run(repo, apply=True)

    with open(os.path.join(repo, DOC, "remarks", "page_006.json"), encoding="utf-8") as f:
        assert json.load(f) == []
    with open(os.path.join(repo, DOC, "pages", "6", "index.html"), encoding="utf-8") as f:
        assert "исчезнет" not in f.read()


def test_refuses_a_directory_that_is_not_a_repo(tmp_path, repo):
    plain = os.path.join(tmp_path, "plain")
    os.makedirs(plain)
    assert _run(plain, apply=True) == 2


def test_write_only_touches_files_but_not_git(repo):
    """Режим для контейнера API: файлы записаны, git не тронут.

    Так ключ с правом записи не нужен внутри контейнера, который смотрит в
    интернет: коммит и push делаются снаружи, в том же клоне.
    """
    db.upsert_remark_db(DOC, "006", "a1", "major", "текст", coord_x=1, coord_y=1,
                            action="create")
    assert _run(repo, write_only=True) == 0
    assert os.path.exists(os.path.join(repo, DOC, "remarks", "page_006.json"))
    # Изменения остались незакоммиченными — их заберёт внешний шаг.
    assert _git(repo, "status", "--porcelain") != ""
    assert _git(repo, "log", "-1", "--pretty=%s") == "init"


def test_from_dir_takes_ready_files_without_touching_the_db(repo, tmp_path, monkeypatch):
    """Режим для прода: база и клон не видны одному процессу.

    Источник — том, куда публикатор уже записал и JSON, и отрисованные
    страницы. Скрипт их просто забирает: пересобирать нечем и незачем, а базу
    в этом режиме открывать нельзя — root на хосте создал бы WAL-файлы, к
    которым API (uid 10001) потеряет доступ.
    """
    src = os.path.join(tmp_path, "volume")
    os.makedirs(os.path.join(src, DOC, "remarks"))
    os.makedirs(os.path.join(src, DOC, "pages", "6"))
    with open(os.path.join(src, DOC, "remarks", "page_006.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": "a1", "text": "из тома", "kind": "major"}], f, ensure_ascii=False)
    with open(os.path.join(src, DOC, "pages", "6", "index.html"), "w", encoding="utf-8") as f:
        f.write("<html>страница из тома</html>")

    def explode():
        raise AssertionError("база не должна открываться в режиме --from")
    monkeypatch.setattr(db, "init_db", explode)

    import sys
    argv = ["nightly_snapshot.py", "--repo", repo, "--apply", "--from", src]
    old, sys.argv = sys.argv, argv
    try:
        assert nightly_snapshot.main() == 0
    finally:
        sys.argv = old

    with open(os.path.join(repo, DOC, "remarks", "page_006.json"), encoding="utf-8") as f:
        assert json.load(f)[0]["text"] == "из тома"
    # Страницы забираются как есть — иначе content-sync затрёт свежие старыми.
    with open(os.path.join(repo, DOC, "pages", "6", "index.html"), encoding="utf-8") as f:
        assert "страница из тома" in f.read()


def test_no_pages_leaves_html_alone(repo):
    """Пока на проде старый код, рендер новым page_html выложил бы заодно
    незапланированные изменения вёрстки. JSON при этом уезжает как надо."""
    db.upsert_remark_db(DOC, "006", "a1", "major", "только json", coord_x=1, coord_y=1,
                            action="create")
    assert _run(repo, apply=True, no_pages=True) == 0
    assert os.path.exists(os.path.join(repo, DOC, "remarks", "page_006.json"))
    assert not os.path.exists(os.path.join(repo, DOC, "pages", "6", "index.html"))


def test_host_mode_needs_no_project_modules(repo, tmp_path):
    """`--from --no-pages` должен работать там, где из проекта нет ничего.

    Это ровно хостовая половина ночного прогона: база и код живут в контейнере,
    на хосте есть только git и сам этот файл.
    """
    import subprocess
    import sys

    export = os.path.join(tmp_path, "export", DOC, "remarks")
    os.makedirs(export)
    with open(os.path.join(export, "page_006.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": "a1", "text": "с хоста", "kind": "major"}], f, ensure_ascii=False)

    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "scripts", "nightly_snapshot.py")
    result = subprocess.run(
        [sys.executable, script, "--repo", repo, "--from", os.path.join(tmp_path, "export"),
         "--no-pages", "--apply"],
        cwd=tmp_path,  # вне репозитория: модули проекта не подхватятся из cwd
        env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    assert result.returncode == 0, result.stdout
    with open(os.path.join(repo, DOC, "remarks", "page_006.json"), encoding="utf-8") as f:
        assert json.load(f)[0]["text"] == "с хоста"
