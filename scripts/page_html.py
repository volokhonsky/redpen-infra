"""
Render one static HTML file per textbook page, with the published annotations
inlined as real text.

Why: the whole book used to be a single HTML document that fetched everything
over JSON, so a crawler saw ~159 characters of text for the entire corpus. Each
page now gets its own address -- <docId>/pages/<label>/index.html -- carrying
its own <title>, description, canonical, Open Graph tags, breadcrumbs and the
annotation texts themselves.

Two representations live in the same file:

* published annotations are rendered as HTML in the panel -- this is what gets
  indexed;
* *every* annotation, drafts included, also goes into an inline
  <script type="application/json"> blob, so `?showDrafts=1` works with no
  network request at all. Script contents are not indexed as page text, which
  is exactly what we want for unreviewed drafts.

Annotation bodies are converted to HTML here rather than in the browser, so the
blob carries ready markup and the viewer needs no markdown library.

Links are relative (`prefix`/`up`, as in scripts/blog.py) so the site keeps
working from a subdirectory, from file:// and from a USB stick.
"""

import datetime
import html
import json
import os
import re
from typing import Any, Dict, List, Optional

import annotation_categories
import blog
import chapters as chapters_mod

_esc = html.escape

# Absolute base used only for canonical/Open Graph URLs, which must be
# absolute. Everything the page actually loads stays relative.
SITE_URL = os.getenv("REDPEN_SITE_URL", "https://medinsky.net").rstrip("/")

PAGES_DIRNAME = "pages"

# Annotation bodies are markdown, but the corpus predates that rule and a
# couple of entries carry a raw <a href="...">...</a>. blog._render_inline
# escapes everything, so those would surface as literal tags; normalise them to
# markdown links first. Attributes such as target="_blank" are dropped on
# purpose -- link behaviour is the site's decision, not the annotation's.
_RAW_LINK_RE = re.compile(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)


def render_annotation_html(text: str) -> str:
    """Annotation markdown -> HTML, reusing the blog's renderer."""
    normalized = _RAW_LINK_RE.sub(lambda m: f"[{m.group(2).strip()}]({m.group(1)})", text or "")
    return blog.render_markdown(normalized)


def annotation_plain_text(text: str) -> str:
    """Strip markup down to bare prose, for <meta name="description">."""
    plain = _RAW_LINK_RE.sub(lambda m: m.group(2), text or "")
    plain = re.sub(r'\[([^\]]+)\]\([^)\s]+\)', r'\1', plain)   # markdown links -> label
    plain = re.sub(r'[*`#>]+', '', plain)
    plain = re.sub(r'\s+', ' ', plain)
    return plain.strip()


def make_description(annotations: List[Dict[str, Any]], fallback: str, limit: int = 160) -> str:
    """First published annotation, trimmed at a word boundary."""
    source = annotation_plain_text(annotations[0]["text"]) if annotations else ""
    if not source:
        source = fallback
    if len(source) <= limit:
        return source
    cut = source[:limit]
    if " " in cut:
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ,.;:—-") + "…"


def is_anchored(ann: Dict[str, Any]) -> bool:
    """Does this annotation get a marker on the scan?"""
    coords = ann.get("coords")
    return isinstance(coords, (list, tuple)) and len(coords) == 2


def is_published(ann: Dict[str, Any]) -> bool:
    return not ann.get("draft")


def page_href(label: str) -> str:
    """Doc-relative link to a page directory, with an explicit index.html so
    the link also resolves under file:// (same rule as the blog)."""
    return f"{PAGES_DIRNAME}/{label}/index.html"


def _display_tags(ann: Dict[str, Any]) -> List[str]:
    """Tags worth showing to a reader: technique/typical-comment labels, not
    bookkeeping ones.

    `cat:*` тоже не показываем: это зеркало поля `category`, и категория уже
    сообщена читателю цветом. Дублировать её ещё и чипом — шум."""
    return [
        t for t in (ann.get("tags") or [])
        if t != "draft"
        and not t.startswith("confidence:")
        and not t.startswith(annotation_categories.CAT_PREFIX)
    ]


def _page_title(label: str, located: Dict[str, Any], doc_title: str) -> str:
    section = located.get("section")
    chapter = located.get("chapter")
    context = None
    if section:
        context = section["name"]
    elif chapter:
        context = chapter["name"]
    head = f"Стр. {label}" if label.isdigit() else (located.get("pageName") or f"Стр. {label}")
    if context:
        return f"{head} · {context} — разбор учебника «{doc_title}»"
    return f"{head} — разбор учебника «{doc_title}»"


def _heading(label: str, page_name: Optional[str]) -> str:
    if page_name:
        return page_name
    if label.isdigit():
        return f"Страница {label}"
    return f"Страница {label}"


def _breadcrumbs(root: str, doc_rel: str, doc_title: str, located: Dict[str, Any], label: str) -> str:
    crumbs = [
        f'<a href="{root}index.html">Мединский.нет</a>',
        f'<a href="{doc_rel}index.html">{_esc(doc_title)}</a>',
    ]
    chapter = located.get("chapter")
    section = located.get("section")
    if chapter:
        crumbs.append(f'<span>{_esc(chapter["name"])}</span>')
    if section:
        crumbs.append(f'<span>{_esc(section["name"])}</span>')
    crumbs.append(f'<span aria-current="page">стр. {_esc(label)}</span>')
    return (
        '  <nav class="breadcrumbs" aria-label="Навигационная цепочка">\n    '
        + '\n    <span class="breadcrumbs__sep">›</span>\n    '.join(crumbs)
        + '\n  </nav>'
    )


def _panel_list(published: List[Dict[str, Any]]) -> str:
    """The indexable part: published annotations as real HTML."""
    if not published:
        return (
            '      <p class="panel-empty">На этой странице пока нет опубликованного разбора.</p>'
        )
    items = []
    number = 0
    for ann in published:
        number += 1
        ann_type = ann.get("annType") or "comment"
        # Категория — готовое поле аннотации (ровно одно, по умолчанию «Прочее»).
        # Не выводим её из тегов: этим занимался переходный код, теперь значение
        # приезжает из БД через publisher._render_item.
        category = annotation_categories.normalize_category(ann.get("category"))
        tags = _display_tags(ann)
        tags_html = ""
        if tags:
            chips = "".join(f'<li class="panel-item__tag">{_esc(t)}</li>' for t in tags)
            tags_html = f'\n          <ul class="panel-item__tags">{chips}</ul>'
        items.append(
            f'        <li class="panel-item panel-item--{_esc(ann_type)} '
            f'panel-item--cat-{_esc(category)}" '
            f'id="panel-item-{_esc(str(ann["id"]))}" data-ann-id="{_esc(str(ann["id"]))}">\n'
            f'          <span class="panel-item__num" aria-hidden="true">{number}</span>\n'
            f'          <div class="panel-item__body comment-content">{render_annotation_html(ann["text"])}</div>'
            f'{tags_html}\n'
            f'        </li>'
        )
    # `open` стоит в разметке всегда: краулер и читатель без JS видят полный
    # список. На телефоне page-view.js снимает атрибут, и вместо стены текста
    # под сканом остаётся одна строка.
    return (
        '      <details class="panel-list-wrap" id="panel-list-wrap" open>\n'
        f'        <summary>Все комментарии ({len(published)})</summary>\n'
        '        <ol class="panel-list" id="panel-list">\n'
        + "\n".join(items) +
        '\n        </ol>\n'
        '      </details>'
    )


def _panel_tags(published: List[Dict[str, Any]]) -> str:
    """Per-page tag facets, as links into the existing ?tags= filter."""
    counts: Dict[str, int] = {}
    for ann in published:
        for tag in _display_tags(ann):
            counts[tag] = counts.get(tag, 0) + 1
    if not counts:
        return ""
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    chips = "".join(
        f'<li><a class="panel-chip" href="?tags={_esc(tag, quote=True)}" data-tag="{_esc(tag, quote=True)}">'
        f'{_esc(tag)}<span class="panel-chip__count">{count}</span></a></li>'
        for tag, count in ordered
    )
    return (
        '      <details class="panel-tags" id="panel-tags">\n'
        f'        <summary>Теги страницы ({len(ordered)})</summary>\n'
        f'        <ul class="panel-chips">{chips}</ul>\n'
        '      </details>'
    )


def _page_data_blob(annotations: List[Dict[str, Any]]) -> str:
    """Every annotation, drafts included, with markup already rendered.

    Lives in a <script type="application/json"> so drafts ship with the page
    (no fetch for ?showDrafts=1) without becoming indexable page text.
    """
    payload = []
    for ann in annotations:
        item = {
            "id": ann["id"],
            "annType": ann.get("annType") or "comment",
            # Категория — то, чем просмотрщик красит маркер. Без неё всё
            # схлопывается в «Прочее», поэтому она в блобе всегда.
            "category": annotation_categories.normalize_category(ann.get("category")),
            "html": render_annotation_html(ann["text"]),
        }
        if is_anchored(ann):
            item["coords"] = list(ann["coords"])
        if ann.get("tags"):
            item["tags"] = list(ann["tags"])
        if ann.get("draft"):
            item["draft"] = True
        payload.append(item)
    # "<" is escaped so the blob can never terminate the enclosing <script>.
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return f'  <script type="application/json" id="redpen-page-data">{encoded}</script>'


def render_page(
    *,
    doc_id: str,
    doc_title: str,
    label: str,
    page_file: str,
    page_name: Optional[str],
    annotations: List[Dict[str, Any]],
    located: Dict[str, Any],
    prev_label: Optional[str],
    next_label: Optional[str],
    timestamp: str,
) -> str:
    """Full HTML for one page. `annotations` is the page's bare array."""
    root = "../../../"      # <doc>/pages/<label>/ -> site root
    doc_rel = "../../"      # <doc>/pages/<label>/ -> <doc>/

    published = [a for a in annotations if is_published(a)]

    title = _page_title(label, {**located, "pageName": page_name}, doc_title)
    description = make_description(
        published,
        fallback=f"Постраничный разбор учебника «{doc_title}»: страница {label}.",
    )
    canonical = f"{SITE_URL}/{doc_id}/{PAGES_DIRNAME}/{label}/"
    og_image = f"{SITE_URL}/{doc_id}/images/{page_file}.png"

    # Pages with nothing published yet would be thin content: keep the address
    # working and crawlable onward, but out of the index.
    robots = "" if published else '\n  <meta name="robots" content="noindex,follow"/>'

    heading = _heading(label, page_name)
    section = located.get("section")
    context_line = section["name"] if section else (located["chapter"]["name"] if located.get("chapter") else "")

    nav_links = []
    if prev_label:
        nav_links.append(f'<a class="page-nav__prev" rel="prev" href="{doc_rel}{page_href(prev_label)}">← стр. {_esc(prev_label)}</a>')
    nav_links.append(f'<a class="page-nav__index" href="{doc_rel}index.html">Оглавление</a>')
    if next_label:
        nav_links.append(f'<a class="page-nav__next" rel="next" href="{doc_rel}{page_href(next_label)}">стр. {_esc(next_label)} →</a>')
    nav_html = '\n    '.join(nav_links)

    panel_tags = _panel_tags(published)
    panel_tags_block = f"\n{panel_tags}" if panel_tags else ""

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <title>{_esc(title)}</title>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <meta name="description" content="{_esc(description, quote=True)}"/>
  <link rel="canonical" href="{_esc(canonical, quote=True)}"/>{robots}
  <meta property="og:type" content="article"/>
  <meta property="og:site_name" content="Мединский.нет"/>
  <meta property="og:title" content="{_esc(title, quote=True)}"/>
  <meta property="og:description" content="{_esc(description, quote=True)}"/>
  <meta property="og:url" content="{_esc(canonical, quote=True)}"/>
  <meta property="og:image" content="{_esc(og_image, quote=True)}"/>
  <meta name="twitter:card" content="summary_large_image"/>
  <link rel="stylesheet" href="{root}css/main.css">
  <link rel="stylesheet" href="{root}css/annotations.css">
  <link rel="stylesheet" href="{root}css/page-panel.css">
  <link rel="icon" href="{root}favicon.svg">
</head>
<body>
  <a href="{root}index.html" class="home-button" title="На главную">⌂</a>
  <header>Мединский.нет <span id="timestamp" style="font-size: 0.7rem; font-weight: normal; opacity: 0.8;">Последнее обновление: {timestamp}</span></header>

{_breadcrumbs(root, doc_rel, doc_title, located, label)}

  <div id="layout">
    <div id="content-wrapper">
      <div id="image-container">
        <img id="page-image" src="{doc_rel}images/{page_file}.png"
             alt="{_esc(heading, quote=True)} учебника «{_esc(doc_title, quote=True)}»"/>
      </div>
    </div>

    <aside id="page-panel">
      <div id="panel-context">
        <h1 class="panel-context__title">{_esc(heading)}</h1>
        {f'<p class="panel-context__section">{_esc(context_line)}</p>' if context_line else ''}
        <p class="panel-context__count">{_annotations_summary(len(published))}</p>
      </div>{panel_tags_block}
{_panel_list(published)}
    </aside>
  </div>

  <nav class="page-nav" aria-label="Навигация по страницам">
    {nav_html}
  </nav>

  <div id="mobile-overlay" class="mobile-overlay">
    <div class="mobile-overlay-close" id="mobile-overlay-close" role="button" tabindex="0" aria-label="Закрыть">×</div>
    <div class="mobile-comment-content comment-content" id="mobile-comment-content"></div>
  </div>

{_page_data_blob(annotations)}
  <script src="{root}js/redpen-categories.js"></script>
  <script src="{root}js/page-view.js"></script>
</body>
</html>
"""


def _annotations_summary(total: int) -> str:
    if not total:
        return "Разбор этой страницы ещё не опубликован."
    return f"{total} {_plural_ru(total, 'комментарий', 'комментария', 'комментариев')} к странице"


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def render_toc(
    *,
    doc_id: str,
    doc_title: str,
    description: str,
    pages: List[Dict[str, Any]],
    counts: Dict[str, int],
    chapter_list: List[Dict[str, Any]],
    timestamp: str,
) -> str:
    """Document contents page -- <doc>/index.html.

    Doubles as the crawl hub: without it the 448 per-page files would be
    orphans that nothing links to. Groups pages by chapter/section and shows
    how much published commentary each one carries.
    """
    root = "../"
    total = sum(counts.values())
    indexed = sum(1 for c in counts.values() if c)

    # Bucket every page under the section (or chapter) that owns it, keeping
    # manifest order so front matter stays first.
    groups: List[Dict[str, Any]] = []
    current_key = object()
    for page in pages:
        label = str(page.get("label") or "")
        if not label:
            continue
        located = locate_label(chapter_list, label)
        section = located.get("section")
        chapter = located.get("chapter")
        key = (chapter or {}).get("name"), (section or {}).get("name")
        if key != current_key:
            current_key = key
            groups.append({
                "chapter": (chapter or {}).get("name"),
                "section": (section or {}).get("name"),
                "pages": [],
            })
        groups[-1]["pages"].append((label, page.get("name"), counts.get(label, 0)))

    blocks = []
    last_chapter = None
    for group in groups:
        if group["chapter"] != last_chapter:
            last_chapter = group["chapter"]
            if last_chapter:
                blocks.append(f'    <h2 class="toc-chapter">{_esc(last_chapter)}</h2>')
        if group["section"]:
            blocks.append(f'    <h3 class="toc-section">{_esc(group["section"])}</h3>')
        links = []
        for label, name, count in group["pages"]:
            title_attr = f' title="{_esc(name, quote=True)}"' if name else ""
            badge = f'<span class="toc-page__count">{count}</span>' if count else ""
            cls = "toc-page" + ("" if count else " toc-page--empty")
            links.append(
                f'<li><a class="{cls}" href="{page_href(label)}"{title_attr}>'
                f'{_esc(name or label)}{badge}</a></li>'
            )
        blocks.append('    <ul class="toc-pages">' + "".join(links) + '</ul>')

    body = "\n".join(blocks)
    canonical = f"{SITE_URL}/{doc_id}/"

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <title>{_esc(doc_title)} — постраничный разбор — Мединский.нет</title>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <meta name="description" content="{_esc(description, quote=True)}"/>
  <link rel="canonical" href="{_esc(canonical, quote=True)}"/>
  <meta property="og:type" content="book"/>
  <meta property="og:site_name" content="Мединский.нет"/>
  <meta property="og:title" content="{_esc(doc_title, quote=True)} — постраничный разбор"/>
  <meta property="og:description" content="{_esc(description, quote=True)}"/>
  <meta property="og:url" content="{_esc(canonical, quote=True)}"/>
  <link rel="stylesheet" href="{root}css/main.css">
  <link rel="stylesheet" href="{root}css/page-panel.css">
  <link rel="icon" href="{root}favicon.svg">
</head>
<body>
  <a href="{root}index.html" class="home-button" title="На главную">⌂</a>
  <header>Мединский.нет <span id="timestamp" style="font-size: 0.7rem; font-weight: normal; opacity: 0.8;">Последнее обновление: {timestamp}</span></header>

  <nav class="breadcrumbs" aria-label="Навигационная цепочка">
    <a href="{root}index.html">Мединский.нет</a>
    <span class="breadcrumbs__sep">›</span>
    <span aria-current="page">{_esc(doc_title)}</span>
  </nav>

  <main class="toc">
    <h1>{_esc(doc_title)}</h1>
    <p class="toc-intro">{_esc(description)}</p>
    <p class="toc-stats">Разбор опубликован на {indexed} {_plural_ru(indexed, 'странице', 'страницах', 'страницах')} — {total} {_plural_ru(total, 'комментарий', 'комментария', 'комментариев')}.</p>
{body}
  </main>

  <script src="{root}js/legacy-page-redirect.js"></script>
</body>
</html>
"""


def locate_label(chapter_list: List[Dict[str, Any]], label: str) -> Dict[str, Any]:
    """chapters.locate_page for a manifest label (front matter labels such as
    A1 are not printed page numbers and belong to no chapter)."""
    if not label.isdigit():
        return {"chapter": None, "section": None}
    return chapters_mod.locate_page(chapter_list, int(label))


def load_annotations(doc_dir: str, page_file: str) -> List[Dict[str, Any]]:
    path = os.path.join(doc_dir, "annotations", f"{page_file}.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


#: Метка «последнее обновление» — с точностью до дня, не до минуты.
#: Читателю нужна свежесть, а минуты складываются в публичное расписание правок:
#: часы суток выдают часовой пояс, а при двух-трёх участниках — и людей.
#: С тех пор как страницы перерисовываются на каждое сохранение в редакторе,
#: минутная метка была бы прямым журналом активности прямо на сайте.
#: См. docs/anonymity-model.md.
STAMP_FORMAT = "%d.%m.%Y"


def day_stamp() -> str:
    return datetime.datetime.now().strftime(STAMP_FORMAT)


_STAMP_RE = re.compile(r"Последнее обновление: [^<]*")

#: Шапка сгенерированного файла. Живёт здесь, а не только в build_website.py,
#: потому что страницы пишут двое: сборка и публикатор API (на каждую правку).
#: Разойдись они хоть одной строкой — и каждый цикл «сборка → снапшот» менял бы
#: все 448 файлов разом, а в ночном коммите не было бы видно сути.
AUTO_HEADER = "<!-- AUTO-GENERATED FILE. Do not edit directly. Run scripts/build_website.py -->\n"


def _write_page_preserving_stamp(out_path: str, html_text: str) -> bool:
    """Записать страницу; если изменилась только метка даты — не трогать.

    True, если файл действительно изменился.

    Две причины. Во-первых, честность: метка называется «последнее обновление»,
    и у страницы, чьи аннотации никто не трогал полгода, она должна показывать
    ту давнюю дату, а не день последней сборки. Во-вторых, диффы: без этого
    любая пересборка меняет все 448 файлов разом, и в ночном коммите не видно,
    что на самом деле поменялось."""
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            existing = f.read()
    except OSError:
        existing = None

    if existing is not None:
        if _STAMP_RE.sub("", existing) == _STAMP_RE.sub("", html_text):
            return False

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_text)
    return True


def build_single_page(doc_dir: str, page_file: str,
                      timestamp: Optional[str] = None,
                      auto_header: Optional[str] = None) -> Optional[str]:
    """Перерисовать HTML одной страницы. Возвращает путь или None.

    Нужна публикатору API: просмотрщик читает аннотации не из
    `annotations/page_NNN.json`, а из инлайнового блока `redpen-page-data`,
    вшитого сюда. Пока перерисовки не было, правка через редактор обновляла
    JSON, но до читателя не доезжала вовсе.

    None означает «страницу отрисовать нечем» — нет манифеста или в нём нет
    такого файла. Это не ошибка: в `PUBLISH_DIR` может не быть собранного
    сайта (dev без тома), и падать из-за этого публикация не должна.
    """
    metadata_path = os.path.join(doc_dir, "metadata.json")
    if not os.path.exists(metadata_path):
        return None
    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    pages = metadata.get("pages")
    if not isinstance(pages, list) or not pages:
        return None

    index = next((i for i, page in enumerate(pages) if page.get("file") == page_file), None)
    if index is None:
        return None

    page = pages[index]
    label = str(page.get("label") or "")
    if not label:
        return None

    doc_id = os.path.basename(os.path.normpath(doc_dir))
    html_text = render_page(
        doc_id=doc_id,
        doc_title=metadata.get("title") or doc_id,
        label=label,
        page_file=page_file,
        page_name=page.get("name"),
        annotations=load_annotations(doc_dir, page_file),
        located=locate_label(metadata.get("chapters") or [], label),
        prev_label=str(pages[index - 1].get("label")) if index > 0 else None,
        next_label=str(pages[index + 1].get("label")) if index + 1 < len(pages) else None,
        timestamp=timestamp or day_stamp(),
    )

    out_dir = os.path.join(doc_dir, PAGES_DIRNAME, label)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "index.html")
    header = AUTO_HEADER if auto_header is None else auto_header
    _write_page_preserving_stamp(out_path, header + html_text)
    return out_path


def build_pages(doc_dir: str, timestamp: str, auto_header: str = "") -> List[str]:
    """Write <doc_dir>/pages/<label>/index.html for every page in the manifest.

    Returns the list of written paths. A document without a `pages` manifest is
    skipped (legacy mode -- same rule as generate_page_manifest.py).
    """
    metadata_path = os.path.join(doc_dir, "metadata.json")
    if not os.path.exists(metadata_path):
        return []
    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    pages = metadata.get("pages")
    if not isinstance(pages, list) or not pages:
        print(f"[page_html] {doc_dir} has no pages manifest; skipping per-page HTML")
        return []

    doc_id = os.path.basename(os.path.normpath(doc_dir))
    doc_title = metadata.get("title") or doc_id
    chapter_list = metadata.get("chapters") or []

    # annotations/ is owned by the API's publisher, not by this build (see
    # content_sync's --exclude /*/annotations/). A build into a fresh
    # --target-dir therefore has none, and every page would silently render
    # empty and noindex. Loud, because the failure mode is an invisible
    # de-indexing of the whole site.
    if not os.path.isdir(os.path.join(doc_dir, "annotations")):
        print(
            f"[page_html] WARNING: {doc_dir}/annotations is missing -- every page will render "
            f"empty and noindex. Annotations come from the DB export (scripts/api/export_annotations.py) "
            f"and live in the redpen-publish checkout; copy them into the target dir before building."
        )

    written = []
    published_total = 0
    indexable = 0
    counts: Dict[str, int] = {}
    for i, page in enumerate(pages):
        label = str(page.get("label") or "")
        page_file = page.get("file")
        if not label or not page_file:
            continue

        located = locate_label(chapter_list, label)

        annotations = load_annotations(doc_dir, page_file)
        published = [a for a in annotations if is_published(a)]
        published_total += len(published)
        counts[label] = len(published)
        if published:
            indexable += 1

        out_dir = os.path.join(doc_dir, PAGES_DIRNAME, label)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "index.html")

        html_text = render_page(
            doc_id=doc_id,
            doc_title=doc_title,
            label=label,
            page_file=page_file,
            page_name=page.get("name"),
            annotations=annotations,
            located=located,
            prev_label=str(pages[i - 1].get("label")) if i > 0 else None,
            next_label=str(pages[i + 1].get("label")) if i + 1 < len(pages) else None,
            timestamp=timestamp,
        )
        _write_page_preserving_stamp(out_path, auto_header + html_text)
        written.append(out_path)

    # The contents page: without it every per-page file is an orphan that
    # nothing links to, and a crawler has no route into the book.
    toc = render_toc(
        doc_id=doc_id,
        doc_title=doc_title,
        description=metadata.get("description") or f"Постраничный разбор учебника «{doc_title}».",
        pages=pages,
        counts=counts,
        chapter_list=chapter_list,
        timestamp=timestamp,
    )
    with open(os.path.join(doc_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(auto_header + toc)

    print(
        f"[page_html] wrote {len(written)} per-page HTML files to {os.path.join(doc_dir, PAGES_DIRNAME)}; "
        f"{indexable} indexable ({published_total} published annotations), "
        f"{len(written) - indexable} noindex; contents page at {os.path.join(doc_dir, 'index.html')}"
    )
    return written
