"""
Тесты сборщика офлайн-архива (``scripts/make_offline_bundle.py``).

Проверяем ровно то, что обещано читателю на главной: в копии нет кода,
обращающегося к сети, а все данные, которые просмотрщик грузит fetch'ем, лежат
в offline-data.js — иначе под ``file://`` страница останется пустой.

Работаем на синтетическом сайте из двух страниц, реальный redpen-publish не
трогаем.
"""

import importlib.util
import json
import os
import re
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = "testbook"

_PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _load_module():
    path = os.path.join(ROOT, "scripts", "make_offline_bundle.py")
    spec = importlib.util.spec_from_file_location("make_offline_bundle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mob = _load_module()


@pytest.fixture
def site(tmp_path):
    """Мини-сайт той же формы, что и redpen-publish."""
    site_dir = tmp_path / "site"
    (site_dir / "css").mkdir(parents=True)
    (site_dir / "js").mkdir()
    (site_dir / "css" / "main.css").write_text("body{}", encoding="utf-8")
    for name in ("layout.js", "comment-content.js", "annotations.js", "mobile.js", "main.js"):
        (site_dir / "js" / name).write_text("// %s" % name, encoding="utf-8")
    # редакторские скрипты — в архив попасть не должны
    (site_dir / "js" / "redpen-auth.js").write_text(
        "var base='https://api.medinsky.net';", encoding="utf-8")
    (site_dir / "js" / "redpen-editor-panel.js").write_text("//", encoding="utf-8")
    (site_dir / "js" / "redpen-editor-bootstrap.js").write_text(
        "fetch('https://api.medinsky.net/api/auth/me');", encoding="utf-8")
    (site_dir / "favicon.svg").write_text("<svg/>", encoding="utf-8")

    doc = site_dir / DOC
    for sub in ("annotations", "text", "images"):
        (doc / sub).mkdir(parents=True)
    (doc / "metadata.json").write_text(json.dumps({
        "id": DOC,
        "title": "Тестовая книга",
        "description": "Описание",
        "icon": "cover.png",
        "pages": [{"file": "page_001", "label": "1"}, {"file": "page_002", "label": "2"}],
    }, ensure_ascii=False), encoding="utf-8")
    (doc / "cover.png").write_bytes(_PNG_1x1)

    for page in ("page_001", "page_002"):
        (doc / "images" / (page + ".png")).write_bytes(_PNG_1x1)
        (doc / "text" / (page + ".json")).write_text(
            json.dumps([{"id": page + "_line001", "bbox": [1, 2, 3, 4]}]), encoding="utf-8")
        (doc / "annotations" / (page + ".json")).write_text(json.dumps([
            {"id": "ann-1", "text": "Разбор «ёлки» — см. https://example.org/источник",
             "annType": "main", "coords": [10, 20], "tags": []},
        ], ensure_ascii=False), encoding="utf-8")
    # легаси-компаньон: в архив попасть не должен
    (doc / "annotations" / "page_001.drafts.json").write_text("[]", encoding="utf-8")

    (doc / "index.html").write_text("""<!DOCTYPE html>
<html><head><link rel="stylesheet" href="../css/main.css"></head>
<body>
  <div id="layout"></div>
  <script>
    window.REDPEN_API_BASE = window.REDPEN_API_BASE || 'https://api.medinsky.net';
    window.REDPEN_GOOGLE_CLIENT_ID = window.REDPEN_GOOGLE_CLIENT_ID || 'x.apps.googleusercontent.com';
  </script>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <script src="../js/layout.js"></script>
  <script src="../js/comment-content.js"></script>
  <script src="../js/annotations.js"></script>
  <script src="../js/mobile.js"></script>
  <script src="../js/main.js?v=tags-1"></script>
  <script src="../js/redpen-editor-panel.js"></script>
  <script src="../js/redpen-auth.js"></script>
  <script src="../js/redpen-editor-bootstrap.js"></script>
</body></html>
""", encoding="utf-8")
    # копия, которую сборщик обязан отбросить
    (doc / "document_index.html").write_text("<html></html>", encoding="utf-8")
    return site_dir


@pytest.fixture
def bundle(site, tmp_path):
    root, stats = mob.stage_bundle(str(site), DOC, str(tmp_path / "stage"), "bundle")
    return root, stats


def _read(root, *parts):
    with open(os.path.join(root, *parts), encoding="utf-8") as f:
        return f.read()


# --- состав архива --------------------------------------------------------

def test_bundle_layout(bundle):
    root, _ = bundle
    for rel in ("index.html", "ЧИТАТЬ.html", "README.txt", "favicon.svg",
                "css/main.css", "js/main.js", "js/redpen-offline.js",
                DOC + "/index.html", DOC + "/offline-data.js",
                DOC + "/images/page_001.png", DOC + "/metadata.json"):
        assert os.path.exists(os.path.join(root, *rel.split("/"))), rel


def test_editor_scripts_and_legacy_files_dropped(bundle):
    root, _ = bundle
    for name in mob.EDITOR_SCRIPTS:
        assert not os.path.exists(os.path.join(root, "js", name))
    assert not os.path.exists(os.path.join(root, DOC, "document_index.html"))
    assert not os.path.exists(os.path.join(root, DOC, "annotations", "page_001.drafts.json"))


def test_no_network_references(bundle):
    """Главная проверка: обещание «ничего никуда не отправляет» — грепом."""
    root, _ = bundle
    assert mob.check_no_network_refs(root) == []


def test_doc_index_loads_offline_data_before_viewer(bundle):
    root, _ = bundle
    html = _read(root, DOC, "index.html")
    assert "offline-data.js" in html
    assert "redpen-offline.js" in html
    # шим обязан выполниться раньше main.js, иначе fetch уже улетит в никуда
    assert html.index("redpen-offline.js") < html.index("../js/main.js")
    assert html.index("offline-data.js") < html.index("redpen-offline.js")
    # просмотрщик при этом остался на месте
    assert "../js/annotations.js" in html
    assert 'href="../css/main.css"' in html


def test_all_paths_stay_relative(bundle):
    root, _ = bundle
    html = _read(root, DOC, "index.html") + _read(root, "index.html")
    assert not re.search(r'(?:src|href)="/[^/]', html)
    assert not re.search(r'(?:src|href)="https?://', html)


# --- данные ---------------------------------------------------------------

def test_offline_data_covers_every_page(bundle):
    root, _ = bundle
    payload = _parse_offline_data(_read(root, DOC, "offline-data.js"))
    assert payload["metadata"]["title"] == "Тестовая книга"
    assert sorted(payload["annotations"]) == ["page_001", "page_002"]
    assert sorted(payload["text"]) == ["page_001", "page_002"]
    assert payload["annotations"]["page_001"][0]["id"] == "ann-1"
    assert "page_001.drafts" not in payload["annotations"]


def test_offline_data_survives_non_ascii(bundle):
    root, _ = bundle
    payload = _parse_offline_data(_read(root, DOC, "offline-data.js"))
    assert "«ёлки»" in payload["annotations"]["page_001"][0]["text"]


def _parse_offline_data(js):
    """Достаёт payload из ``window.REDPEN_OFFLINE = JSON.parse("...");``.

    JS-строковый литерал совместим с JSON, поэтому разбираем двумя json.loads —
    так тест заодно проверяет, что литерал корректно экранирован.
    """
    m = re.search(r'JSON\.parse\((".*")\);', js, re.DOTALL)
    assert m, "offline-data.js не в ожидаемом формате"
    return json.loads(json.loads(m.group(1)))


def test_stats_counted(bundle):
    _root, stats = bundle
    assert stats["pages"] == 2
    assert stats["annotations"] == 2
    assert stats["vendoredMarked"] is False


# --- rewrite -------------------------------------------------------------

def test_rewrite_keeps_vendored_marked_when_present(site, tmp_path):
    (site / "js" / "vendor").mkdir()
    (site / "js" / "vendor" / "marked.min.js").write_text("//marked", encoding="utf-8")
    root, _ = mob.stage_bundle(str(site), DOC, str(tmp_path / "stage2"), "bundle")
    html = _read(root, DOC, "index.html")
    assert "../js/vendor/marked.min.js" in html
    assert "cdn.jsdelivr.net" not in html


def test_rewrite_fails_loudly_on_unknown_template():
    with pytest.raises(RuntimeError):
        mob.rewrite_doc_index("<html><body>нет скриптов</body></html>", False)


# --- упаковка -------------------------------------------------------------

def test_zip_contents_and_manifest(site, tmp_path):
    out = tmp_path / "out" / "bundle.zip"
    rc = mob.main(["--doc", DOC, "--site-dir", str(site), "--out", str(out),
                   "--stage-dir", str(tmp_path / "stage3")])
    assert rc == 0
    assert out.exists()

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "redpen-testbook-offline/index.html" in names
        assert "redpen-testbook-offline/%s/offline-data.js" % DOC in names
        # PNG кладём без сжатия — распаковка с флешки заметно быстрее
        png = zf.getinfo("redpen-testbook-offline/%s/images/page_001.png" % DOC)
        assert png.compress_type == zipfile.ZIP_STORED
        html = zf.getinfo("redpen-testbook-offline/index.html")
        assert html.compress_type == zipfile.ZIP_DEFLATED

    manifest = json.loads((tmp_path / "out" / "bundle.zip.json").read_text(encoding="utf-8"))
    assert manifest["doc"] == DOC
    assert manifest["size"] == out.stat().st_size
    assert len(manifest["sha256"]) == 64


def test_missing_document_fails(site, tmp_path):
    with pytest.raises(SystemExit):
        mob.stage_bundle(str(site), "nope", str(tmp_path / "stage4"), "bundle")
