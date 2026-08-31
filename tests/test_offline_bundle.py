"""
Тесты сборщика офлайн-архива (``scripts/make_offline_bundle.py``).

Проверяем ровно то, что обещано читателю на главной: в копии нет кода,
обращающегося к сети, и страница со всеми замечаниями открывается из файла.

С 2026-08-30 бандл — прямая копия статики: постраничный просмотрщик не делает
ни одного запроса, замечания приезжают инлайновым блоком redpen-page-data
внутри самой страницы. Прежний offline-data.js и шим fetch существовали ради
SPA и удалены вместе с ним.

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
    for name in ("page-view.js", "redpen-markers.js", "redpen-categories.js",
                 "legacy-page-redirect.js"):
        (site_dir / "js" / name).write_text("// %s" % name, encoding="utf-8")
    # Модули рабочего места — в архив попасть не должны: каждый из них умеет
    # ходить в API, а redpen-config.js вдобавок несёт его адрес.
    for name in ("redpen-auth.js", "redpen-api.js", "redpen-config.js"):
        (site_dir / "js" / name).write_text(
            "var base='https://api.medinsky.net';", encoding="utf-8")
    (site_dir / "favicon.svg").write_text("<svg/>", encoding="utf-8")

    doc = site_dir / DOC
    for sub in ("remarks", "text", "images"):
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
        (doc / "remarks" / (page + ".json")).write_text(json.dumps([
            {"id": "ann-1", "text": "Разбор «ёлки» — см. https://example.org/источник",
             "kind": "major", "coords": [10, 20], "tags": []},
        ], ensure_ascii=False), encoding="utf-8")
    # легаси-компаньон: в архив попасть не должен
    (doc / "remarks" / "page_001.drafts.json").write_text("[]", encoding="utf-8")

    # Оглавление книги.
    (doc / "index.html").write_text("""<!DOCTYPE html>
<html><head><link rel="stylesheet" href="../css/main.css"></head>
<body>
  <div class="toc"></div>
  <script src="../js/legacy-page-redirect.js"></script>
</body></html>
""", encoding="utf-8")

    # Страницы: замечания внутри html, ни одного запроса наружу.
    for label, page in (("1", "page_001"), ("2", "page_002")):
        page_dir = doc / "pages" / label
        page_dir.mkdir(parents=True)
        (page_dir / "index.html").write_text("""<!DOCTYPE html>
<html><head><link rel="stylesheet" href="../../../css/main.css"></head>
<body>
  <img id="page-image" src="../../images/%s.png" />
  <script type="application/json" id="redpen-page-data">[{"id":"ann-1"}]</script>
  <script src="../../../js/redpen-categories.js"></script>
  <script src="../../../js/redpen-markers.js"></script>
  <script src="../../../js/page-view.js"></script>
</body></html>
""" % page, encoding="utf-8")

    # копия старого SPA: могла остаться на томе публикации, в архив не идёт
    (doc / "document_index.html").write_text(
        "<script>fetch('https://api.medinsky.net/x')</script>", encoding="utf-8")
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
                "css/main.css", "js/page-view.js", "js/redpen-markers.js",
                DOC + "/index.html", DOC + "/pages/1/index.html",
                DOC + "/images/page_001.png", DOC + "/metadata.json"):
        assert os.path.exists(os.path.join(root, *rel.split("/"))), rel


def test_editor_scripts_and_legacy_files_dropped(bundle):
    root, _ = bundle
    for name in mob.EDITOR_SCRIPTS:
        assert not os.path.exists(os.path.join(root, "js", name))
    assert not os.path.exists(os.path.join(root, DOC, "document_index.html"))
    assert not os.path.exists(os.path.join(root, DOC, "remarks", "page_001.drafts.json"))


def test_no_network_references(bundle):
    """Главная проверка: обещание «ничего никуда не отправляет» — грепом."""
    root, _ = bundle
    assert mob.check_no_network_refs(root) == []


def test_page_carries_its_remarks_inline(bundle):
    """Данные страницы едут в самой странице — под file:// fetch запрещён."""
    root, _ = bundle
    html = _read(root, DOC, "pages", "1", "index.html")
    assert 'id="redpen-page-data"' in html
    assert '"ann-1"' in html
    assert "../../../js/page-view.js" in html


def test_all_paths_stay_relative(bundle):
    root, _ = bundle
    html = (_read(root, DOC, "index.html") + _read(root, "index.html")
            + _read(root, DOC, "pages", "1", "index.html"))
    assert not re.search(r'(?:src|href)="/[^/]', html)
    assert not re.search(r'(?:src|href)="https?://', html)


def test_stats_counted(bundle):
    _root, stats = bundle
    assert stats["pages"] == 2
    assert stats["remarks"] == 2


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
        assert "redpen-testbook-offline/%s/pages/1/index.html" % DOC in names
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
