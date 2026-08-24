"""
Tests for ``scripts/blog.py`` — статический блог сайта «Мединский.нет».

Проверяем разбор исходников, минимальный markdown и генерацию страниц.
Отдельно следим за главным инвариантом проекта: страницы блога не должны
содержать абсолютных путей — сайт обязан открываться из любой папки.
"""

import importlib.util
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_blog():
    path = os.path.join(ROOT, "scripts", "blog.py")
    spec = importlib.util.spec_from_file_location("blog", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


blog = _load_blog()


POST = """---
title: Заголовок поста
date: 2026-08-15
summary: Короткая аннотация.
---

Первый абзац.

- пункт раз
- пункт два
"""


@pytest.fixture()
def source_dir(tmp_path):
    src = tmp_path / "content" / "blog"
    src.mkdir(parents=True)
    (src / "2026-08-15-svezhaya.md").write_text(POST, encoding="utf-8")
    (src / "2026-07-01-staraya.md").write_text(
        "---\ntitle: Старая\ndate: 2026-07-01\n---\n\nТекст.\n", encoding="utf-8")
    return src


# --- разбор исходников -----------------------------------------------------

def test_parse_post_reads_frontmatter_and_body(source_dir):
    post = blog.parse_post(str(source_dir / "2026-08-15-svezhaya.md"))
    assert post["title"] == "Заголовок поста"
    assert post["date"] == "2026-08-15"
    assert post["summary"] == "Короткая аннотация."
    assert post["slug"] == "svezhaya"
    assert post["body"].startswith("Первый абзац.")
    assert "---" not in post["body"]


def test_posts_sorted_newest_first(source_dir):
    posts = blog.load_posts(str(source_dir))
    assert [p["slug"] for p in posts] == ["svezhaya", "staraya"]


def test_load_posts_on_missing_dir_returns_empty(tmp_path):
    assert blog.load_posts(str(tmp_path / "nope")) == []


def test_format_date_is_russian():
    assert blog.format_date("2026-08-15") == "15 августа 2026"
    # Нераспознанное отдаём как есть, без исключения.
    assert blog.format_date("скоро") == "скоро"


# --- markdown --------------------------------------------------------------

def test_render_markdown_blocks():
    html = blog.render_markdown(
        "## Заголовок\n\nАбзац с **жирным**.\n\n- раз\n- два\n\n> цитата\n")
    assert "<h3>Заголовок</h3>" in html   # h1 занят заголовком поста
    assert "<strong>жирным</strong>" in html
    assert html.count("<li>") == 2 and "<ul>" in html
    assert "<blockquote>" in html


def test_render_markdown_numbered_list():
    html = blog.render_markdown("1. раз\n2. два\n")
    assert "<ol>" in html and html.count("<li>") == 2


def test_render_markdown_links_and_escaping():
    html = blog.render_markdown("[тут](../index.html) и <script>alert(1)</script>")
    assert '<a href="../index.html">тут</a>' in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_render_markdown_keeps_parentheses_inside_a_url():
    """Наивное [^)\\s]+ обрывало адрес на первой закрывающей скобке.

    У Википедии скобка в адресе — норма, и обрезанный URL давал читателю 404,
    а лишняя «)» оставалась в тексте рядом со ссылкой. На проде так были
    сломаны 32 аннотации.
    """
    url = "https://ru.wikipedia.org/wiki/Объединение_Германии_(1990)"
    html = blog.render_markdown(f"[объединение]({url})")
    assert f'<a href="{url}">объединение</a>' in html
    assert ")</p>" not in html.replace(f'{url}">объединение</a>', "")


def test_render_markdown_does_not_swallow_parentheses_after_a_link():
    html = blog.render_markdown("[ссылка](https://e.org/a) и текст (в скобках) дальше")
    assert '<a href="https://e.org/a">ссылка</a>' in html
    assert "текст (в скобках) дальше" in html


def test_render_markdown_rejects_javascript_urls():
    html = blog.render_markdown("[клик](javascript:alert(1))")
    assert "<a" not in html


# --- генерация страниц -----------------------------------------------------

def test_build_blog_writes_index_and_posts(tmp_path, source_dir):
    out = tmp_path / "out"
    posts = blog.build_blog(str(out), source_dir=str(source_dir))

    assert len(posts) == 2
    index_html = (out / "blog" / "index.html").read_text(encoding="utf-8")
    assert "Заголовок поста" in index_html
    assert 'href="svezhaya/index.html"' in index_html

    post_html = (out / "blog" / "svezhaya" / "index.html").read_text(encoding="utf-8")
    assert "<h1>Заголовок поста</h1>" in post_html
    assert "Первый абзац" in post_html
    assert "<title>Заголовок поста — Мединский.нет</title>" in post_html


def test_blog_pages_use_relative_paths_only(tmp_path, source_dir):
    out = tmp_path / "out"
    blog.build_blog(str(out), source_dir=str(source_dir))

    index_html = (out / "blog" / "index.html").read_text(encoding="utf-8")
    post_html = (out / "blog" / "svezhaya" / "index.html").read_text(encoding="utf-8")

    # Офлайн-инвариант: страница не должна ЗАГРУЖАТЬ ничего по абсолютному
    # адресу — иначе копия с флешки полезет в сеть или отвалится.
    #
    # canonical и og:url при этом обязаны быть абсолютными: это метаданные для
    # поисковика и соцсетей, браузер их не запрашивает, и офлайн они безвредны.
    # Поэтому проверяем именно загружаемые ресурсы, а не наличие "https://".
    resource_re = re.compile(r'<(link|script|img|iframe|source)\b([^>]*)>', re.IGNORECASE)
    url_re = re.compile(r'(?:src|href)="([^"]+)"', re.IGNORECASE)
    for html in (index_html, post_html):
        for tag, attrs in resource_re.findall(html):
            if 'rel="canonical"' in attrs:      # метаданные, не ресурс
                continue
            for url in url_re.findall(attrs):
                assert not url.startswith(('http://', 'https://', '//')), f"внешний ресурс: {url}"
                assert not url.startswith('/'), f"абсолютный путь: {url}"

    assert 'href="../css/main.css"' in index_html
    assert 'href="../../css/main.css"' in post_html

    # Метаданные — наоборот, абсолютные.
    assert '<link rel="canonical" href="https://medinsky.net/blog/"/>' in index_html
    assert '<link rel="canonical" href="https://medinsky.net/blog/svezhaya/"/>' in post_html


def test_build_blog_without_posts_is_noop(tmp_path):
    out = tmp_path / "out"
    assert blog.build_blog(str(out), source_dir=str(tmp_path / "nope")) == []
    assert not (out / "blog").exists()


def test_latest_section_points_at_newest_post(source_dir):
    posts = blog.load_posts(str(source_dir))
    section = blog.render_latest_section(posts)
    assert "Заголовок поста" in section
    assert 'href="blog/svezhaya/index.html"' in section
    assert 'href="blog/index.html"' in section
    assert "Старая" not in section


def test_latest_section_empty_without_posts():
    assert blog.render_latest_section([]) == ""


# --- реальные исходники ----------------------------------------------------

def test_repo_blog_posts_parse():
    """Посты в content/blog/ должны читаться и иметь дату с заголовком."""
    posts = blog.load_posts(os.path.join(ROOT, blog.BLOG_SOURCE_DIRNAME))
    assert posts, "в content/blog/ нет ни одного поста"
    for post in posts:
        assert post["title"] and post["date"] and post["slug"]
        assert blog.format_date(post["date"]) != post["date"], (
            f"дата поста {post['slug']} не в формате YYYY-MM-DD")
