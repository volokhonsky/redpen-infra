"""
Tests for scripts/page_html.py -- the per-page static HTML that makes the
corpus indexable, and scripts/sitemap.py.

The two invariants worth guarding:

1. published remarks must appear as real text in the HTML (before this,
   a crawler saw ~159 characters for the whole 1257-remark corpus);
2. drafts must ship with the page (so ?showDrafts=1 needs no request) but must
   NOT be indexable text -- they live in a <script type="application/json">.
"""

import json
import os
import re

import pytest

import page_html
import sitemap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHAPTERS = [{
    "name": "Глава I. Тест",
    "startPage": 5,
    "endPage": 40,
    "sections": [{"name": "§ 1. Раздел", "startPage": 6, "endPage": 20}],
}]

PUBLISHED = {
    "id": "ann-1",
    "text": "Опубликованный разбор со [ссылкой](https://example.org/istochnik).",
    "kind": "major",
    "coords": [430, 215],
    "tags": ["omission", "confidence:high"],
}
DRAFT = {
    "id": "ann-2",
    "text": "Черновиковый текст про несогласованное утверждение.",
    "kind": "minor",
    "coords": [100, 100],
    "tags": ["draft", "framing"],
    "draft": True,
}


def render(remarks, label="7", page_name=None):
    located = page_html.locate_label(CHAPTERS, label)
    return page_html.render_page(
        doc_id="testdoc",
        doc_title="Тестовый учебник",
        label=label,
        page_file="page_007",
        page_name=page_name,
        remarks=remarks,
        located=located,
        prev_label="6",
        next_label="8",
        timestamp="16.08.2026",
    )


def visible_text(html):
    """What a crawler reads: markup minus scripts and styles."""
    stripped = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", html)
    stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
    return re.sub(r"\s+", " ", stripped)


def blob(html):
    raw = re.search(r'id="redpen-page-data">(.*?)</script>', html, re.S).group(1)
    return json.loads(raw.replace("\\u003c", "<"))


# --- индексируемость ------------------------------------------------------

def test_published_remark_is_real_text_in_the_html():
    html = render([PUBLISHED])
    assert "Опубликованный разбор" in visible_text(html)


def test_published_page_is_indexable():
    assert 'name="robots"' not in render([PUBLISHED])


def test_page_without_published_remarks_is_noindex():
    # Thin content: the address keeps working and stays crawlable onward,
    # but must not enter the index.
    assert '<meta name="robots" content="noindex,follow"/>' in render([DRAFT])
    assert '<meta name="robots" content="noindex,follow"/>' in render([])


def test_title_carries_the_section():
    assert "§ 1. Раздел" in re.search(r"<title>(.*?)</title>", render([PUBLISHED])).group(1)


def test_front_matter_page_uses_its_name():
    html = render([], label="A1", page_name="Обложка")
    assert "<title>Обложка" in html


def test_canonical_and_open_graph():
    html = render([PUBLISHED])
    assert '<link rel="canonical" href="https://medinsky.net/testdoc/pages/7/"/>' in html
    assert '<meta property="og:url" content="https://medinsky.net/testdoc/pages/7/"/>' in html
    assert '<meta property="og:image" content="https://medinsky.net/testdoc/images/page_007.png"/>' in html


def test_description_comes_from_the_first_published_remark():
    description = re.search(r'<meta name="description" content="([^"]*)"', render([PUBLISHED])).group(1)
    assert description.startswith("Опубликованный разбор")
    assert "[" not in description and "](" not in description   # markdown stripped
    assert len(description) <= 161


def test_description_falls_back_when_nothing_is_published():
    description = re.search(r'<meta name="description" content="([^"]*)"', render([DRAFT])).group(1)
    assert "Тестовый учебник" in description


# --- черновики ------------------------------------------------------------

def test_draft_is_not_indexable_text():
    html = render([PUBLISHED, DRAFT])
    assert "Черновиковый текст" not in visible_text(html)


def test_draft_ships_with_the_page_so_no_request_is_needed():
    entries = blob(render([PUBLISHED, DRAFT]))
    drafts = [e for e in entries if e.get("draft")]
    assert len(drafts) == 1
    assert "Черновиковый текст" in drafts[0]["html"]


def test_blob_carries_rendered_html_not_markdown():
    entry = blob(render([PUBLISHED]))[0]
    assert entry["html"].startswith("<p>")
    assert '<a href="https://example.org/istochnik">' in entry["html"]


def test_blob_cannot_terminate_the_enclosing_script():
    html = render([dict(PUBLISHED, text="Опасный текст </script><script>alert(1)</script>")])
    raw = re.search(r'id="redpen-page-data">(.*?)</script>', html, re.S).group(1)
    # The blob must not be able to close its own <script> element...
    assert "</script>" not in raw
    # ...and the same text in the panel must stay inert markup, not a tag.
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# --- разметка тела --------------------------------------------------------

def test_markdown_link_becomes_a_real_link():
    assert '<a href="https://example.org/istochnik">ссылкой</a>' in render([PUBLISHED])


def test_url_with_parentheses_survives_in_body_and_description():
    url = "https://ru.wikipedia.org/wiki/Голод_в_СССР_(1946—1947)"
    body = f"Голод назван, но не измерен. [Голод в СССР (1946—1947)]({url})"
    html = page_html.render_remark_html(body)
    assert f'<a href="{url}">' in html
    # Описание страницы не должно нести огрызок адреса или осиротевшую скобку.
    plain = page_html.remark_plain_text(body)
    assert plain == "Голод назван, но не измерен. Голод в СССР (1946—1947)"


def test_raw_html_link_in_a_body_is_normalised_not_escaped():
    # Two remarks in the corpus predate the markdown-only rule.
    html = page_html.render_remark_html('Читайте <a href="https://e.org/x" target="_blank">речь</a> целиком.')
    assert '<a href="https://e.org/x">речь</a>' in html
    assert "&lt;a href" not in html


def test_dangerous_markup_in_a_body_is_escaped():
    html = page_html.render_remark_html('<img src=x onerror="alert(1)">')
    assert "<img" not in html
    assert "&lt;img" in html


def test_bookkeeping_tags_are_not_shown_to_readers():
    html = render([PUBLISHED])
    assert "omission" in html
    assert "confidence:high" not in visible_text(html)


def test_numbering_starts_at_one_per_page():
    html = render([PUBLISHED, dict(PUBLISHED, id="ann-3", text="Второй.")])
    assert re.findall(r'class="panel-item__num"[^>]*>(\d+)<', html) == ["1", "2"]


def test_list_is_wrapped_in_details_open_by_default():
    """The list ships expanded: a crawler and a JS-less reader must see the
    whole text. page-view.js collapses it only on touch devices, where the
    comment is shown by the overlay instead."""
    html = render([PUBLISHED])
    assert '<details class="panel-list-wrap" id="panel-list-wrap" open>' in html
    assert "<summary>Все замечания (1)</summary>" in html


def test_summary_counts_published_remarks():
    html = render([PUBLISHED, dict(PUBLISHED, id="ann-3", text="Второй."), DRAFT])
    assert "<summary>Все замечания (2)</summary>" in html


def test_collapsed_list_still_carries_the_indexable_text():
    # The whole point of the details wrapper: it changes layout, not content.
    assert "Опубликованный разбор" in visible_text(render([PUBLISHED]))


def test_navigation_uses_real_links():
    html = render([PUBLISHED])
    assert '<a class="page-nav__prev" rel="prev" href="../../pages/6/index.html"' in html
    assert '<a class="page-nav__next" rel="next" href="../../pages/8/index.html"' in html


def test_links_are_relative_so_the_site_works_offline():
    html = render([PUBLISHED])
    for href in re.findall(r'(?:href|src)="([^"]+)"', html):
        assert not href.startswith("/"), f"absolute path breaks file:// and USB copies: {href}"


# --- инвариант: никакой сети ----------------------------------------------

def test_page_view_js_never_talks_to_the_network():
    """The viewer must work from a USB stick; page data is inlined, so this
    file has no business fetching anything."""
    source = open(os.path.join(ROOT, "templates", "js", "page-view.js"), encoding="utf-8").read()
    # Comments explain the rule and name the forbidden calls; strip them so the
    # check measures code rather than prose.
    code = re.sub(r"(?s)/\*.*?\*/", "", source)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    for forbidden in ("fetch(", "XMLHttpRequest", "REDPEN_API_BASE", "api.medinsky.net"):
        assert forbidden not in code


def test_rendered_page_pulls_no_json():
    html = render([PUBLISHED, DRAFT])
    assert "remarks/" not in html
    assert "metadata.json" not in html


# --- оглавление -----------------------------------------------------------

def test_toc_links_every_page_and_shows_counts():
    pages = [{"file": "page_006", "label": "6"}, {"file": "page_007", "label": "7"}]
    html = page_html.render_toc(
        doc_id="testdoc", doc_title="Тестовый учебник", description="Описание",
        pages=pages, counts={"6": 0, "7": 3}, chapter_list=CHAPTERS, timestamp="16.08.2026",
    )
    assert 'href="pages/6/index.html"' in html
    assert 'href="pages/7/index.html"' in html
    assert "§ 1. Раздел" in html
    assert '<span class="toc-page__count">3</span>' in html
    # A page with nothing published is still reachable, just muted.
    assert "toc-page--empty" in html


# --- sitemap --------------------------------------------------------------

@pytest.fixture()
def built_site(tmp_path):
    (tmp_path / "index.html").write_text("<html><head></head><body>landing</body></html>", encoding="utf-8")
    (tmp_path / "document_index.html").write_text("<html><head></head><body>dup</body></html>", encoding="utf-8")
    pages = tmp_path / "doc" / "pages"
    (pages / "7").mkdir(parents=True)
    (pages / "8").mkdir(parents=True)
    (pages / "7" / "index.html").write_text("<html><head><title>a</title></head><body>ok</body></html>", encoding="utf-8")
    (pages / "8" / "index.html").write_text(
        '<html><head><meta name="robots" content="noindex,follow"/></head><body>thin</body></html>',
        encoding="utf-8",
    )
    cabinet = tmp_path / "cabinet"
    cabinet.mkdir()
    (cabinet / "index.html").write_text("<html><head></head><body>cabinet</body></html>", encoding="utf-8")
    return tmp_path


def test_sitemap_lists_only_indexable_pages(built_site):
    urls = sitemap.generate(str(built_site), "https://example.net")
    assert urls == ["https://example.net/", "https://example.net/doc/pages/7/"]


def test_sitemap_skips_noindex_duplicates_and_cabinet(built_site):
    urls = sitemap.generate(str(built_site), "https://example.net")
    assert not any("document_index" in u for u in urls)
    assert not any("cabinet" in u for u in urls)


def test_sitemap_is_well_formed_xml(built_site):
    from xml.etree import ElementTree

    sitemap.generate(str(built_site), "https://example.net")
    tree = ElementTree.parse(os.path.join(built_site, "sitemap.xml"))
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    assert tree.getroot().tag == f"{ns}urlset"
    assert len(tree.getroot().findall(f"{ns}url")) == 2


def test_robots_points_at_the_sitemap(built_site):
    sitemap.generate(str(built_site), "https://example.net")
    robots = open(os.path.join(built_site, "robots.txt"), encoding="utf-8").read()
    assert "Sitemap: https://example.net/sitemap.xml" in robots
    assert "Disallow: /cabinet/" in robots
