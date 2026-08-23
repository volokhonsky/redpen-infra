"""
Аналитика: подготовка лога для Matomo и счётчики контента.

Главное, что здесь проверяется, — не арифметика отчёта, а два инварианта:
следы редактора и кабинета не уходят ни в Matomo, ни в наши счётчики, а в
нашей базе не появляется ничего, чем можно опознать читателя (этим занимается
Matomo, и второй копии таких данных мы не заводим).
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "ops"))

import analytics  # noqa: E402
import matomo_export  # noqa: E402
import page_sections  # noqa: E402
import redpen_stats  # noqa: E402


def log_line(uri="/medinsky11klass/pages/17/", ip="203.0.113.7", ua="Mozilla/5.0",
             referer="", status=200, ts=None, method="GET", size=1234):
    record = {
        "level": "info",
        "ts": (ts or datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)).timestamp(),
        "logger": "http.log.access.log0",
        "msg": "handled request",
        "request": {
            "remote_ip": ip,
            "proto": "HTTP/2.0",
            "method": method,
            "host": "medinsky.net",
            "uri": uri,
            "headers": {"User-Agent": [ua], "Referer": [referer] if referer else []},
        },
        "duration": 0.0123,
        "size": size,
        "status": status,
    }
    return json.dumps(record)


MANIFEST = {
    "pages": [{"file": f"page_{n:03d}", "label": str(n)} for n in range(15, 22)],
    "chapters": [{"id": "c1", "sections": [
        {"id": "2", "name": "§ 2. Политическая система", "startPage": 15, "endPage": 20},
    ]}],
}


@pytest.fixture()
def site_dir(tmp_path):
    book = tmp_path / "site" / "medinsky11klass"
    book.mkdir(parents=True)
    (book / "metadata.json").write_text(json.dumps(MANIFEST, ensure_ascii=False),
                                        encoding="utf-8")
    return str(tmp_path / "site")


# --------------------------------------------------------------------------
# Классификация пути
# --------------------------------------------------------------------------

def test_classify_reader_page():
    info = analytics.classify_path("/medinsky11klass/pages/17/")
    assert info["kind"] == "page"
    assert info["doc_id"] == "medinsky11klass"
    assert info["page_label"] == "17"
    assert not info["private"]


@pytest.mark.parametrize("uri,kind", [
    ("/medinsky10klass/images/page_006.png", "image"),
    ("/medinsky11klass/annotations/page_017.json", "data"),
    ("/medinsky11klass/", "doc"),
    ("/", "home"),
    ("/blog/why/", "blog"),
    ("/robots.txt", "meta"),
    ("/sitemap.xml", "meta"),
    ("/js/page-view.js", "asset"),
    ("/redpen-medinsky11klass-offline.zip", "download"),
    ("/medinsky11klass/document_index.html", "spa"),
])
def test_classify_kinds(uri, kind):
    assert analytics.classify_path(uri)["kind"] == kind


@pytest.mark.parametrize("uri", [
    "/app/", "/app/index.html", "/cabinet/", "/api/annotations/x/y",
    "/.hooks/redpen-publish",
    "/medinsky11klass/pages/17/?editor=1",
])
def test_editor_traffic_is_private(uri):
    """Редактор и кабинет — закрытый круг участников. Связывать их запросы с
    адресом нельзя (docs/anonymity-model.md), поэтому они не учитываются."""
    assert analytics.classify_path(uri)["private"] is True


def test_permalink_and_legacy_params():
    info = analytics.classify_path("/medinsky11klass/pages/17/?only=circle-3")
    assert info["ann_id"] == "circle-3"
    assert analytics.classify_path("/?page=17")["legacy_param"] == "17"
    assert analytics.classify_path("/medinsky11klass/pages/17/?tags=draft")["tag_filter"]


@pytest.mark.parametrize("referer,expected", [
    ("", "direct"),
    ("https://medinsky.net/medinsky11klass/", "internal"),
    ("https://www.google.com/", "search"),
    ("https://t.me/somechannel", "social"),
    ("https://example.org/post", "link"),
])
def test_referer_source(referer, expected):
    assert analytics.referer_source(referer, ("medinsky.net",))[0] == expected


@pytest.mark.parametrize("ua,expected", [
    ("Mozilla/5.0 (Windows NT 10.0) Chrome/120", "desktop"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari", "mobile"),
    ("Mozilla/5.0 (compatible; YandexBot/3.0)", "bot"),
    ("curl/8.4.0", "bot"),
    ("", "unknown"),
])
def test_ua_class(ua, expected):
    assert analytics.ua_class(ua) == expected


# --------------------------------------------------------------------------
# Разбор строки
# --------------------------------------------------------------------------

def test_parse_line_skips_non_access_records():
    assert analytics.parse_line("") is None
    assert analytics.parse_line("not json") is None
    assert analytics.parse_line(json.dumps({"level": "info", "msg": "reload"})) is None


def test_parse_line_reads_caddy_json():
    parsed = analytics.parse_line(log_line(referer="https://t.me/x"))
    assert parsed["ip"] == "203.0.113.7"
    assert parsed["status"] == 200
    assert parsed["referer"] == "https://t.me/x"
    assert parsed["duration_ms"] == 12


# --------------------------------------------------------------------------
# Экспорт в Matomo
# --------------------------------------------------------------------------

def test_combined_line_is_ncsa_extended():
    """Формат, который import_logs.py читает как ncsa_extended."""
    parsed = analytics.parse_line(log_line(referer="https://t.me/x"))
    line = analytics.combined_line(parsed)
    assert line == ('203.0.113.7 - - [20/Aug/2026:12:00:00 +0000] '
                    '"GET /medinsky11klass/pages/17/ HTTP/2.0" 200 1234 '
                    '"https://t.me/x" "Mozilla/5.0"')


def test_combined_line_escapes_quotes():
    """Кавычка в User-Agent не должна разваливать строку лога."""
    parsed = analytics.parse_line(log_line(ua='Evil"UA'))
    assert '"Evil\\"UA"' in analytics.combined_line(parsed)


def test_section_uri_puts_paragraph_into_path():
    """Приём, ради которого всё: Matomo строит иерархию по сегментам адреса,
    поэтому параграф, ставший сегментом, даёт посещаемость по разделам."""
    uri = analytics.section_uri("medinsky11klass", "17", "2")
    # Без завершающего слэша: иначе Matomo подвешивает лишний уровень «/index».
    assert uri == "/medinsky11klass/%C2%A72/17"
    assert analytics.section_uri("medinsky11klass", "A1", None).endswith("/A1")


def test_export_writes_combined_and_skips_private(tmp_path, site_dir):
    directory = tmp_path / "logs"
    directory.mkdir()
    (directory / "access.log").write_text("\n".join([
        log_line(),
        log_line(uri="/cabinet/"),
        log_line(uri="/medinsky11klass/pages/17/?editor=1"),
        json.dumps({"level": "info", "msg": "reload"}),
    ]) + "\n", encoding="utf-8")

    out = tmp_path / "matomo.log"
    written = matomo_export.export(str(directory), str(out),
                                   str(tmp_path / "state.json"), site_dir)
    assert written == 1
    text = out.read_text(encoding="utf-8")
    assert "/cabinet/" not in text
    assert "editor=1" not in text
    # Параграф подставлен в адрес: стр. 17 входит в § 2.
    assert "%C2%A72/17 " in text


def test_export_keeps_real_paths_without_site_dir(tmp_path):
    directory = tmp_path / "logs"
    directory.mkdir()
    (directory / "access.log").write_text(log_line() + "\n", encoding="utf-8")
    out = tmp_path / "matomo.log"
    matomo_export.export(str(directory), str(out), str(tmp_path / "state.json"), None)
    assert "/medinsky11klass/pages/17/" in out.read_text(encoding="utf-8")


def test_export_does_not_repeat_lines(tmp_path, site_dir):
    """import_logs.py дублей не отсекает, значит отдавать их ему нельзя."""
    directory = tmp_path / "logs"
    directory.mkdir()
    log = directory / "access.log"
    log.write_text(log_line() + "\n", encoding="utf-8")
    out = tmp_path / "matomo.log"
    state = tmp_path / "state.json"

    assert matomo_export.export(str(directory), str(out), str(state), site_dir) == 1
    matomo_export.commit_state(str(state))
    assert matomo_export.export(str(directory), str(out), str(state), site_dir) == 0
    matomo_export.commit_state(str(state))
    with open(log, "a", encoding="utf-8") as f:
        f.write(log_line(uri="/medinsky11klass/pages/18/") + "\n")
    assert matomo_export.export(str(directory), str(out), str(state), site_dir) == 1
    assert "/17/" not in out.read_text(encoding="utf-8")  # только новая строка


def test_export_position_moves_only_after_commit(tmp_path, site_dir):
    """Если импорт упал, позиция двигаться не должна: `import_logs.py` не
    возобновляемый, и второй попытки у этих строк не будет."""
    directory = tmp_path / "logs"
    directory.mkdir()
    (directory / "access.log").write_text(log_line() + "\n", encoding="utf-8")
    out = tmp_path / "matomo.log"
    state = tmp_path / "state.json"

    assert matomo_export.export(str(directory), str(out), str(state), site_dir) == 1
    # Импорт «упал» — коммита не было, строка отдаётся снова.
    assert matomo_export.export(str(directory), str(out), str(state), site_dir) == 1
    assert matomo_export.commit_state(str(state)) is True
    assert matomo_export.export(str(directory), str(out), str(state), site_dir) == 0
    assert matomo_export.commit_state(str(state)) is True


def test_export_dry_run_keeps_position(tmp_path, site_dir):
    directory = tmp_path / "logs"
    directory.mkdir()
    (directory / "access.log").write_text(log_line() + "\n", encoding="utf-8")
    out = tmp_path / "matomo.log"
    state = tmp_path / "state.json"
    assert matomo_export.export(str(directory), str(out), str(state), site_dir,
                                dry_run=True) == 1
    assert matomo_export.export(str(directory), str(out), str(state), site_dir) == 1
    matomo_export.commit_state(str(state))


def test_export_restarts_on_rotated_file(tmp_path, site_dir):
    directory = tmp_path / "logs"
    directory.mkdir()
    log = directory / "access.log"
    log.write_text(log_line() + "\n", encoding="utf-8")
    out = tmp_path / "matomo.log"
    state = tmp_path / "state.json"
    matomo_export.export(str(directory), str(out), str(state), site_dir)
    matomo_export.commit_state(str(state))
    log.write_text(log_line(uri="/medinsky11klass/pages/19/") + "\n", encoding="utf-8")
    assert matomo_export.export(str(directory), str(out), str(state), site_dir) == 1


# --------------------------------------------------------------------------
# Свёртка в параграфы
# --------------------------------------------------------------------------

def test_section_of_uses_manifest(site_dir):
    manifest = page_sections.load_manifest(site_dir, "medinsky11klass")
    assert page_sections.section_of(manifest, "17")["id"] == "2"
    assert page_sections.section_of(manifest, "999") is None
    assert page_sections.section_of(manifest, "A1") is None
    assert manifest["label_to_key"]["17"] == "017"


def test_missing_manifest_is_not_fatal(tmp_path):
    manifest = page_sections.load_manifest(str(tmp_path), "nosuchbook")
    assert manifest == {"label_to_key": {}, "sections": []}


# --------------------------------------------------------------------------
# Счётчики контента
# --------------------------------------------------------------------------

@pytest.fixture()
def log_dir(tmp_path):
    lines = [
        log_line(),
        log_line(uri="/medinsky11klass/images/page_017.png"),
        log_line(uri="/medinsky11klass/pages/18/", referer="https://medinsky.net/x"),
        log_line(uri="/medinsky11klass/pages/17/?only=circle-3"),
        log_line(uri="/cabinet/", ip="198.51.100.4"),
        log_line(uri="/nope", status=404, ip="198.51.100.9"),
        log_line(uri="/medinsky11klass/pages/19/", ip="198.51.100.9",
                 ua="Mozilla/5.0 (compatible; Googlebot/2.1)"),
        json.dumps({"level": "info", "msg": "serving initial configuration"}),
    ]
    directory = tmp_path / "logs"
    directory.mkdir()
    (directory / "access.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(directory)


def test_ingest_writes_hits_and_skips_private(tmp_path, log_dir):
    conn = redpen_stats.open_db(str(tmp_path / "stats.db"))
    stats = redpen_stats.ingest(conn, log_dir, ("medinsky.net",))
    assert stats["hits"] == 6  # /cabinet/ и служебная запись не в счёт
    paths = [r[0] for r in conn.execute("SELECT path FROM hits")]
    assert "/cabinet/" not in paths


def test_stats_db_holds_nothing_identifying(tmp_path, log_dir):
    """Опознанием читателя занимается Matomo. Здесь — только контент."""
    db_path = str(tmp_path / "stats.db")
    conn = redpen_stats.open_db(db_path)
    redpen_stats.ingest(conn, log_dir, ("medinsky.net",))
    conn.commit()
    columns = {r[1] for r in conn.execute("PRAGMA table_info(hits)")}
    assert not columns & {"visitor", "session_id", "ip", "user_agent"}
    with open(db_path, "rb") as f:
        blob = f.read()
    for ip in (b"203.0.113.7", b"198.51.100.4", b"198.51.100.9"):
        assert ip not in blob


def test_ingest_is_idempotent(tmp_path, log_dir):
    conn = redpen_stats.open_db(str(tmp_path / "stats.db"))
    first = redpen_stats.ingest(conn, log_dir, ("medinsky.net",))
    assert redpen_stats.ingest(conn, log_dir, ("medinsky.net",))["hits"] == 0
    assert conn.execute("SELECT COUNT(*) FROM hits").fetchone()[0] == first["hits"]


def test_ingest_picks_up_appended_lines(tmp_path, log_dir):
    conn = redpen_stats.open_db(str(tmp_path / "stats.db"))
    redpen_stats.ingest(conn, log_dir, ("medinsky.net",))
    with open(os.path.join(log_dir, "access.log"), "a", encoding="utf-8") as f:
        f.write(log_line(uri="/medinsky11klass/pages/20/") + "\n")
    assert redpen_stats.ingest(conn, log_dir, ("medinsky.net",))["hits"] == 1


def test_ingest_restarts_on_rotated_file(tmp_path, log_dir):
    """Ротация подменяет файл: смещение из прошлого прогона к нему не относится."""
    conn = redpen_stats.open_db(str(tmp_path / "stats.db"))
    redpen_stats.ingest(conn, log_dir, ("medinsky.net",))
    with open(os.path.join(log_dir, "access.log"), "w", encoding="utf-8") as f:
        f.write(log_line(uri="/medinsky11klass/pages/20/") + "\n")
    assert redpen_stats.ingest(conn, log_dir, ("medinsky.net",))["hits"] == 1


def test_report_rolls_pages_into_sections(tmp_path, log_dir, site_dir):
    conn = redpen_stats.open_db(str(tmp_path / "stats.db"))
    redpen_stats.ingest(conn, log_dir, ("medinsky.net",))
    text = redpen_stats.report(conn, days=3650, site_dir=site_dir,
                               content_db="/nonexistent.db")
    assert "§ 2. Политическая система" in text
    assert "circle-3" in text


def test_report_ignores_bots_as_demand(tmp_path, log_dir, site_dir):
    """Обход робота — не спрос: иначе полный обход книги перекрыл бы живой
    интерес к одной странице."""
    conn = redpen_stats.open_db(str(tmp_path / "stats.db"))
    redpen_stats.ingest(conn, log_dir, ("medinsky.net",))
    text = redpen_stats.report(conn, days=3650, site_dir=site_dir,
                               content_db="/nonexistent.db")
    pages = text.split("== Самые читаемые страницы")[1].split("== Спрос")[0]
    assert "\n  medinsky11klass  19 " not in pages  # эту страницу брал Googlebot


def test_report_shows_demand_without_coverage(tmp_path, log_dir, site_dir):
    """Главный вопрос отчёта: какие страницы читают, хотя разбора там нет."""
    content_db = tmp_path / "redpen.db"
    conn = redpen_stats.sqlite3.connect(str(content_db))
    conn.execute("CREATE TABLE annotations (doc_id TEXT, page_num TEXT, status TEXT)")
    conn.executemany("INSERT INTO annotations VALUES (?, ?, ?)", [
        ("medinsky11klass", "017", "published"),   # разобрана
        ("medinsky11klass", "018", "draft"),       # только черновики
    ])
    conn.commit()
    conn.close()

    stats = redpen_stats.open_db(str(tmp_path / "stats.db"))
    redpen_stats.ingest(stats, log_dir, ("medinsky.net",))
    text = redpen_stats.report(stats, days=3650, site_dir=site_dir,
                               content_db=str(content_db))
    demand = text.split("== Спрос против покрытия")[1].split("== Комментарии")[0]
    assert "\n  medinsky11klass  18 " in demand
    assert "\n  medinsky11klass  17 " not in demand


def test_prune_drops_old_rows(tmp_path, log_dir):
    conn = redpen_stats.open_db(str(tmp_path / "stats.db"))
    redpen_stats.ingest(conn, log_dir, ("medinsky.net",))
    assert redpen_stats.prune(conn, days=1) > 0
    assert conn.execute("SELECT COUNT(*) FROM hits").fetchone()[0] == 0


def test_section_prefix_can_be_ascii(tmp_path, site_dir):
    """Запас на случай, если Matomo покажет § в процентной кодировке."""
    directory = tmp_path / "logs"
    directory.mkdir()
    (directory / "access.log").write_text(log_line() + "\n", encoding="utf-8")
    out = tmp_path / "matomo.log"
    matomo_export.export(str(directory), str(out), str(tmp_path / "state.json"),
                         site_dir, section_prefix="para-")
    assert "/medinsky11klass/para-2/17 " in out.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Следы редактора: предпросмотр грузит настоящую страницу читателя
# --------------------------------------------------------------------------

PREVIEW = "https://medinsky.net/app/"


@pytest.mark.parametrize("referer,expected", [
    ("", False),
    ("https://medinsky.net/app/", True),
    ("https://medinsky.net/app", True),
    ("https://medinsky.net/cabinet/?tag=draft", True),
    ("https://medinsky.net/medinsky11klass/pages/17/", False),
    ("https://t.me/x", False),
    # Чужой сайт со своим /app/ нам не указ.
    ("https://example.org/app/", False),
])
def test_private_referer(referer, expected):
    assert analytics.private_referer(referer, ("medinsky.net",)) is expected


def test_preview_iframe_is_not_a_reader_visit():
    """`/app/` показывает страницу читателя в iframe с ?only=<id>. По адресу
    это неотличимо от чтения — иначе своя же правка засчитывалась бы как визит,
    а разбор комментария как пересылка."""
    parsed = analytics.parse_line(
        log_line(uri="/medinsky11klass/pages/210/?only=circle-2", referer=PREVIEW))
    assert analytics.build_hit(parsed, ("medinsky.net",)) is None


def test_same_page_from_outside_still_counts():
    """Обратная сторона: настоящая пересылка ссылки учитывается."""
    parsed = analytics.parse_line(
        log_line(uri="/medinsky11klass/pages/210/?only=circle-2",
                 referer="https://t.me/channel"))
    hit = analytics.build_hit(parsed, ("medinsky.net",))
    assert hit is not None and hit["ann_id"] == "circle-2"


def test_export_drops_preview_traffic(tmp_path, site_dir):
    directory = tmp_path / "logs"
    directory.mkdir()
    (directory / "access.log").write_text("\n".join([
        log_line(uri="/medinsky11klass/pages/17/", referer=PREVIEW),
        log_line(uri="/medinsky11klass/pages/17/", referer="https://t.me/x"),
    ]) + "\n", encoding="utf-8")
    out = tmp_path / "matomo.log"
    written = matomo_export.export(str(directory), str(out),
                                   str(tmp_path / "state.json"), site_dir,
                                   own_hosts=("medinsky.net",))
    assert written == 1
    assert "/app/" not in out.read_text(encoding="utf-8")
    assert "t.me" in out.read_text(encoding="utf-8")


def test_ingest_drops_preview_traffic(tmp_path):
    directory = tmp_path / "logs"
    directory.mkdir()
    (directory / "access.log").write_text("\n".join([
        log_line(uri="/medinsky11klass/pages/17/", referer=PREVIEW),
        log_line(uri="/medinsky11klass/pages/18/",
                 referer="https://medinsky.net/cabinet/"),
        log_line(uri="/medinsky11klass/pages/19/"),
    ]) + "\n", encoding="utf-8")
    conn = redpen_stats.open_db(str(tmp_path / "stats.db"))
    stats = redpen_stats.ingest(conn, str(directory), ("medinsky.net",))
    assert stats["hits"] == 1
    assert conn.execute("SELECT page_label FROM hits").fetchone()[0] == "19"
