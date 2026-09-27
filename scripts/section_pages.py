"""
Страница параграфа: краткое содержание, пропагандистские тезисы и вопросы
учителю -- <doc>/sections/<id>/index.html.

Замечание привязано к точке на странице и не видит параграфа целиком. А
главное в параграфе часто не в одной фразе, а в том, как подобраны факты от
первой страницы до заданий в конце. Страница параграфа -- место для этого
уровня разбора. На ней три блока:

* краткое содержание -- что рассказывает параграф, своими словами;
* пропагандистские тезисы -- что параграф внушает, со ссылками на страницы и
  замечания, где это видно;
* вопросы учителю -- вопросы, которые школьник может задать на уроке. Это
  отражение рубрики «Вопросы и задания» в конце параграфа: там учебник
  спрашивает школьника, здесь школьник спрашивает учителя. У каждого вопроса
  есть пояснение: что стоит знать, чтобы понять ответ.

Источник -- redpen-content/<doc>/sections/<id>.md, <id> -- тот же, что в
paragraphs_list.txt («1», «32-33»). Файла нет -- страницы у параграфа нет, и
это не ошибка. Формат:

    ~~~meta
    updated: 2026-09-27
    ~~~

    ## Краткое содержание
    Абзацы markdown.

    ## Пропагандистские тезисы
    ### Заголовок тезиса
    Разбор. Ссылки на страницу -- [стр. 7](p:7), на замечание --
    [замечание](p:7/ann-p007-1).

    ## Вопросы учителю
    ### Текст вопроса?
    Пояснение: что стоит знать.

Блок `~~~meta` необязателен. Заголовки второго уровня -- ровно эти три, в любом
порядке; незнакомый заголовок -- ошибка сборки, чтобы опечатка не съела блок
молча. Ссылки `p:` переписываются в относительные адреса страниц разбора, так
что страница работает и с флешки.

Сайт статический, поэтому содержание вшито в HTML при сборке, а не
подгружается. Страницу собирает build_website.py. API её не трогает: правка
замечания не меняет ни пересказа, ни тезисов. Счётчиков замечаний на странице
намеренно нет -- после первой же правки через редактор они бы устарели.

Какие параграфы получили страницу, записывается в metadata.json ключом
`hasPage` у раздела. По этому ключу оглавление и страницы учебника ставят
ссылку на страницу параграфа. Страницы учебника на проде перерисовывает API,
у которого нет redpen-content, -- поэтому признак живёт в манифесте, а не
вычисляется по наличию исходника.
"""

import html
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import blog
import chapters as chapters_mod
import page_html

_esc = html.escape

SECTIONS_DIRNAME = "sections"

# Заголовок в исходнике -> ключ блока.
BLOCK_TITLES = {
    "Краткое содержание": "summary",
    "Пропагандистские тезисы": "theses",
    "Вопросы учителю": "questions",
}

_META_RE = re.compile(r"\A\s*~~~meta[ \t]*\n(.*?)\n~~~[ \t]*\n", re.DOTALL)
# (p:17) или (p:17/ann-p017-2) внутри markdown-ссылки.
_PAGE_LINK_RE = re.compile(r"\(p:([^)/\s]+)(?:/([^)\s]+))?\)")


class SectionPageError(ValueError):
    pass


# --------------------------------------------------------------------------
# Исходник
# --------------------------------------------------------------------------

def parse_source(text: str) -> Dict[str, Any]:
    """md -> {"meta": {...}, "summary": str, "theses": [...], "questions": [...]}.

    Тезис и вопрос -- {"title": str, "body": str}.
    """
    meta: Dict[str, str] = {}
    m = _META_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep:
                meta[key.strip()] = value.strip()
        text = text[m.end():]

    result: Dict[str, Any] = {"meta": meta, "summary": "", "theses": [], "questions": []}
    block: Optional[str] = None
    lines: List[str] = []

    def flush() -> None:
        body = "\n".join(lines).strip()
        lines.clear()
        if block is None:
            if body:
                raise SectionPageError("текст до первого заголовка «## …»")
            return
        if block == "summary":
            result["summary"] = body
            return
        # Внутри тезисов и вопросов каждый пункт открывается «### ».
        items = result[block]
        current = None
        for raw in body.split("\n"):
            if raw.startswith("### "):
                current = {"title": raw[4:].strip(), "body": []}
                items.append(current)
            elif current is None:
                if raw.strip():
                    raise SectionPageError(f"в блоке «{block}» текст до первого «### »")
            else:
                current["body"].append(raw)
        for item in items:
            if isinstance(item["body"], list):
                item["body"] = "\n".join(item["body"]).strip()

    for raw in text.split("\n"):
        if raw.startswith("## "):
            flush()
            title = raw[3:].strip()
            if title not in BLOCK_TITLES:
                known = ", ".join(f"«{t}»" for t in BLOCK_TITLES)
                raise SectionPageError(f"незнакомый заголовок «{title}»; допустимы {known}")
            block = BLOCK_TITLES[title]
        else:
            lines.append(raw)
    flush()

    if not result["summary"]:
        raise SectionPageError("нет блока «Краткое содержание»")
    return result


def rewrite_page_links(text: str, labels: Dict[str, str], doc_rel: str,
                       known_remarks: Optional[Dict[str, set]] = None,
                       warnings: Optional[List[str]] = None) -> str:
    """`(p:7)` -> `(../../pages/7/index.html)`, `(p:7/<id>)` -> `(…?only=<id>)`.

    Незнакомая метка страницы -- ошибка: это опечатка, и ссылка вела бы в 404.
    Незнакомый id замечания -- только предупреждение: сборка в свежий
    каталог идёт без замечаний (их кладёт API), и падать из-за этого нельзя.
    """
    def repl(m: "re.Match[str]") -> str:
        label, remark_id = m.group(1), m.group(2)
        if label not in labels:
            raise SectionPageError(f"ссылка на несуществующую страницу «{label}»")
        href = f"{doc_rel}{page_html.page_href(label)}"
        if remark_id:
            if known_remarks is not None and warnings is not None:
                ids = known_remarks.get(label)
                if ids is not None and remark_id not in ids:
                    warnings.append(f"замечания {remark_id} нет на стр. {label}")
            href += f"?only={remark_id}"
        return f"({href})"
    return _PAGE_LINK_RE.sub(repl, text)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

def _plain(text: str) -> str:
    return page_html.remark_plain_text(_PAGE_LINK_RE.sub("()", text))


def _description(summary: str, limit: int = 160) -> str:
    plain = _plain(summary)
    if len(plain) <= limit:
        return plain
    return plain[:limit - 1].rsplit(" ", 1)[0] + "…"


def _pages_word(start: int, end: int) -> str:
    return f"стр. {start}" if start == end else f"стр. {start}—{end}"


def _count(n: int, one: str, few: str, many: str) -> str:
    return f"{n} {page_html._plural_ru(n, one, few, many)}"


def render_section_page(
    *,
    doc_id: str,
    doc_title: str,
    chapter: Optional[Dict[str, Any]],
    section: Dict[str, Any],
    content: Dict[str, Any],
    page_labels: List[Tuple[str, Optional[str]]],
    prev_link: Optional[Tuple[str, str]],
    next_link: Optional[Tuple[str, str]],
    timestamp: str,
) -> str:
    """Полный HTML страницы параграфа. Ссылки в `content` уже переписаны.

    `prev_link`/`next_link` -- (адрес относительно <doc>/, подпись).
    """
    root = "../../../"      # <doc>/sections/<id>/ -> корень сайта
    doc_rel = "../../"      # <doc>/sections/<id>/ -> <doc>/

    name = section["name"]
    start, end = section["startPage"], section.get("endPage") or section["startPage"]
    theses, questions = content["theses"], content["questions"]

    title = f"{name} — кратко, пропагандистские тезисы и вопросы учителю"
    description = _description(content["summary"])
    canonical = f"{page_html.SITE_URL}/{doc_id}/{SECTIONS_DIRNAME}/{section['id']}/"

    crumbs = [
        f'<a href="{root}index.html">Мединский.нет</a>',
        f'<a href="{doc_rel}index.html">{_esc(doc_title)}</a>',
    ]
    if chapter:
        crumbs.append(f'<span>{_esc(chapter["name"])}</span>')
    crumbs.append(f'<span aria-current="page">{_esc(name)}</span>')
    breadcrumbs = '\n    <span class="breadcrumbs__sep">›</span>\n    '.join(crumbs)

    jump = ['<a href="#summary">Кратко</a>']
    if theses:
        jump.append(f'<a href="#theses">Тезисы <span class="section-jump__n">{len(theses)}</span></a>')
    if questions:
        jump.append(f'<a href="#questions">Вопросы учителю <span class="section-jump__n">{len(questions)}</span></a>')
    jump.append('<a href="#pages">Страницы</a>')

    first_label = page_labels[0][0] if page_labels else None
    read_link = (
        f' · <a href="{doc_rel}{page_html.page_href(first_label)}">читать постранично →</a>'
        if first_label else ""
    )

    blocks = [f"""    <section id="summary" class="section-block section-summary">
      <h2>Краткое содержание</h2>
      <div class="section-prose">
{blog.render_markdown(content["summary"])}
      </div>
    </section>"""]

    if theses:
        items = []
        for i, thesis in enumerate(theses, 1):
            items.append(f"""        <li class="thesis" id="thesis-{i}">
          <h3 class="thesis__title"><span class="thesis__num">{i}</span>{blog._render_inline(thesis["title"])}</h3>
          <div class="thesis__body section-prose">
{blog.render_markdown(thesis["body"])}
          </div>
        </li>""")
        blocks.append(f"""    <section id="theses" class="section-block section-theses">
      <h2>Пропагандистские тезисы</h2>
      <p class="section-block__lead">Что параграф внушает читателю. Чаще всего не прямым текстом, а подбором фактов, формулировками, иллюстрациями и заданиями. Ссылки ведут на страницы и замечания, где это видно.</p>
      <ol class="thesis-list">
{chr(10).join(items)}
      </ol>
    </section>""")

    if questions:
        items = []
        for i, question in enumerate(questions, 1):
            context = question["body"]
            context_html = (
                f"""
          <div class="question__context section-prose">
            <p class="question__context-label">Что стоит знать</p>
{blog.render_markdown(context)}
          </div>""" if context else ""
            )
            items.append(f"""        <li class="question" id="q-{i}">
          <p class="question__text"><a class="question__anchor" href="#q-{i}" aria-label="Ссылка на вопрос {i}">{i}</a>{blog._render_inline(question["title"])}</p>{context_html}
        </li>""")
        blocks.append(f"""    <section id="questions" class="section-block section-questions">
      <h2>Вопросы учителю</h2>
      <p class="section-block__lead">В конце параграфа учебник задаёт вопросы школьнику. Здесь наоборот — вопросы, которые школьник может задать учителю на уроке. Они не для того, чтобы поймать учителя на ошибке. Они о том, что параграф оставил за кадром. Под каждым вопросом — что стоит знать, чтобы понять ответ.</p>
      <ol class="question-list">
{chr(10).join(items)}
      </ol>
    </section>""")

    pills = "".join(
        f'<li><a class="toc-page" href="{doc_rel}{page_html.page_href(label)}"'
        f'{f" title={chr(34)}{_esc(pname, quote=True)}{chr(34)}" if pname else ""}>{_esc(pname or label)}</a></li>'
        for label, pname in page_labels
    )
    blocks.append(f"""    <section id="pages" class="section-block section-pages">
      <h2>Страницы параграфа</h2>
      <ul class="toc-pages">{pills}</ul>
    </section>""")

    nav = []
    if prev_link:
        nav.append(f'<a class="page-nav__prev" rel="prev" href="{doc_rel}{prev_link[0]}">← {_esc(prev_link[1])}</a>')
    nav.append(f'<a class="page-nav__index" href="{doc_rel}index.html">Оглавление</a>')
    if next_link:
        nav.append(f'<a class="page-nav__next" rel="next" href="{doc_rel}{next_link[0]}">{_esc(next_link[1])} →</a>')

    updated = content["meta"].get("updated")
    updated_html = (
        f'\n      <p class="section-hero__updated">Разбор параграфа обновлён {_esc(blog.format_date(updated))}</p>'
        if updated else ""
    )
    chapter_html = (
        f'<p class="section-hero__chapter">{_esc(chapter["name"])}</p>\n      ' if chapter else ""
    )
    body = "\n\n".join(blocks)
    nav_html = "\n    ".join(nav)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <title>{_esc(title)} — Мединский.нет</title>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <meta name="description" content="{_esc(description, quote=True)}"/>
  <link rel="canonical" href="{_esc(canonical, quote=True)}"/>
  <meta property="og:type" content="article"/>
  <meta property="og:site_name" content="Мединский.нет"/>
  <meta property="og:title" content="{_esc(title, quote=True)}"/>
  <meta property="og:description" content="{_esc(description, quote=True)}"/>
  <meta property="og:url" content="{_esc(canonical, quote=True)}"/>
  <link rel="stylesheet" href="{root}css/main.css">
  <link rel="stylesheet" href="{root}css/page-panel.css">
  <link rel="stylesheet" href="{root}css/section-page.css">
  <link rel="icon" href="{root}favicon.svg">
</head>
<body class="section-body">
  <a href="{root}index.html" class="home-button" title="На главную">⌂</a>
  <header>Мединский.нет <span id="timestamp" style="font-size: 0.7rem; font-weight: normal; opacity: 0.8;">Последнее обновление: {timestamp}</span></header>

  <nav class="breadcrumbs" aria-label="Навигационная цепочка">
    {breadcrumbs}
  </nav>

  <main class="section-page">
    <div class="section-hero">
      {chapter_html}<h1>{_esc(name)}</h1>
      <p class="section-hero__pages">{_pages_word(start, end)} · {_count(len(theses), "тезис", "тезиса", "тезисов")} · {_count(len(questions), "вопрос учителю", "вопроса учителю", "вопросов учителю")}{read_link}</p>{updated_html}
      <nav class="section-jump" aria-label="Разделы страницы">{"".join(jump)}</nav>
    </div>

{body}
  </main>

  <nav class="page-nav" aria-label="Навигация по параграфам">
    {nav_html}
  </nav>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Сборка
# --------------------------------------------------------------------------

def _iter_sections(chapter_list: List[Dict[str, Any]]):
    for chapter in chapter_list:
        for section in chapter.get("sections") or []:
            yield chapter, section


def _load_known_remarks(doc_dir: str, pages: List[Dict[str, Any]]) -> Dict[str, set]:
    """метка страницы -> id её замечаний; только для страниц, где файл есть."""
    known: Dict[str, set] = {}
    for page in pages:
        label, page_file = str(page.get("label") or ""), page.get("file")
        if not label or not page_file:
            continue
        path = os.path.join(doc_dir, "remarks", f"{page_file}.json")
        if os.path.exists(path):
            known[label] = {str(r.get("id")) for r in page_html.load_remarks(doc_dir, page_file)}
    return known


def build_section_pages(doc_dir: str, source_dir: str, timestamp: str,
                        auto_header: str = "") -> List[str]:
    """Собрать страницы параграфов и отметить их в metadata.json.

    Вызывать после chapters.generate() (он переписывает `chapters` и стирает
    отметки) и до page_html.build_pages() (оглавлению и страницам учебника
    нужны отметки, чтобы поставить ссылку).
    """
    metadata_path = os.path.join(doc_dir, "metadata.json")
    if not os.path.exists(metadata_path):
        return []
    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    chapter_list = metadata.get("chapters") or []
    pages = metadata.get("pages") or []
    labels = {str(p.get("label")): p.get("file") for p in pages if p.get("label")}
    doc_id = os.path.basename(os.path.normpath(doc_dir))
    doc_title = metadata.get("title") or doc_id

    sources = {}
    if os.path.isdir(source_dir):
        for name in sorted(os.listdir(source_dir)):
            if name.endswith(".md") and not name.startswith("_"):
                sources[name[:-3]] = os.path.join(source_dir, name)

    all_sections = list(_iter_sections(chapter_list))
    section_ids = {str(s.get("id")) for _, s in all_sections}
    orphans = sorted(set(sources) - section_ids)
    if orphans:
        raise SectionPageError(f"{doc_id}: нет параграфов с id {', '.join(orphans)} (см. paragraphs_list.txt)")

    for _, section in all_sections:
        section.pop("hasPage", None)
        if str(section.get("id")) in sources:
            section["hasPage"] = True

    def link_to(section: Dict[str, Any]) -> Tuple[str, str]:
        short = section["name"].split(".", 1)[0]          # «§ 2»
        if section.get("hasPage"):
            return f"{SECTIONS_DIRNAME}/{section['id']}/index.html", short
        return page_html.page_href(str(section["startPage"])), short

    numbered = [s for _, s in all_sections if str(s.get("name", "")).startswith("§")]
    known_remarks = _load_known_remarks(doc_dir, pages)

    written = []
    for chapter, section in all_sections:
        sid = str(section.get("id"))
        if sid not in sources:
            continue
        with open(sources[sid], "r", encoding="utf-8") as f:
            try:
                content = parse_source(f.read())
            except SectionPageError as e:
                raise SectionPageError(f"{sources[sid]}: {e}") from e

        warnings: List[str] = []

        def rewrite(text: str) -> str:
            try:
                return rewrite_page_links(text, labels, "../../", known_remarks, warnings)
            except SectionPageError as e:
                raise SectionPageError(f"{sources[sid]}: {e}") from e

        content["summary"] = rewrite(content["summary"])
        for key in ("theses", "questions"):
            for item in content[key]:
                item["title"] = rewrite(item["title"])
                item["body"] = rewrite(item["body"])
        for w in warnings:
            print(f"[section_pages] WARNING {sources[sid]}: {w}")

        start, end = section["startPage"], section.get("endPage") or section["startPage"]
        page_labels = [
            (str(p["label"]), p.get("name")) for p in pages
            if str(p.get("label", "")).isdigit() and start <= int(p["label"]) <= end
        ]

        idx = next((i for i, s in enumerate(numbered) if s is section), None)
        prev_link = link_to(numbered[idx - 1]) if idx else None
        next_link = link_to(numbered[idx + 1]) if idx is not None and idx + 1 < len(numbered) else None

        html_text = render_section_page(
            doc_id=doc_id,
            doc_title=doc_title,
            chapter=chapter,
            section=section,
            content=content,
            page_labels=page_labels,
            prev_link=prev_link,
            next_link=next_link,
            timestamp=timestamp,
        )
        out_dir = os.path.join(doc_dir, SECTIONS_DIRNAME, sid)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "index.html")
        page_html._write_page_preserving_stamp(out_path, auto_header + html_text)
        written.append(out_path)

    # Страницы, у которых исходник исчез, убираем: иначе адрес жил бы с
    # устаревшим разбором, а оглавление на него уже не ссылалось бы.
    out_root = os.path.join(doc_dir, SECTIONS_DIRNAME)
    if os.path.isdir(out_root):
        for name in os.listdir(out_root):
            if name not in sources:
                stale_dir = os.path.join(out_root, name)
                stale = os.path.join(stale_dir, "index.html")
                if os.path.exists(stale):
                    os.remove(stale)
                if os.path.isdir(stale_dir) and not os.listdir(stale_dir):
                    os.rmdir(stale_dir)

    chapters_mod.write_chapters(doc_dir, chapter_list)

    if written:
        print(f"[section_pages] wrote {len(written)} section pages to {out_root}")
    return written
