#!/usr/bin/env python3
"""
Блог сайта «Мединский.нет» — рендеринг статических страниц на этапе сборки.

Исходники постов лежат в content/blog/*.md (этот репозиторий), по файлу на
запись. Имя файла задаёт slug: 2026-08-15-pochemu-medinsky-net.md ->
pochemu-medinsky-net. Дата берётся из frontmatter, а не из имени файла.

Главный инвариант проекта (docs/README.md) действует и здесь: блог — часть
полностью статического сайта. Ничего не грузится в рантайме, все ссылки
относительные, чтобы разбор одинаково работал с домена, из локальной папки и
с флешки.

Markdown рендерится своим минимальным рендерером: пакета markdown в окружении
сборки нет, marked в просмотрщике — клиентский, а тексты постов простые.
Поддерживаются заголовки, абзацы, списки, цитаты, горизонтальная черта,
**жирный**/*курсив*, `код` и ссылки.
"""

import datetime
import os
import re
from html import escape as _esc

# Каталог с исходниками постов (относительно корня репозитория).
BLOG_SOURCE_DIRNAME = os.path.join('content', 'blog')

RU_MONTHS = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]


# --------------------------------------------------------------------------
# Разбор исходников
# --------------------------------------------------------------------------

def slug_from_filename(filename):
    """2026-08-15-pochemu-medinsky-net.md -> pochemu-medinsky-net."""
    name = os.path.splitext(os.path.basename(filename))[0]
    return re.sub(r'^\d{4}-\d{2}-\d{2}-', '', name)


def parse_post(path):
    """
    Прочитать пост: frontmatter между строками '---' + markdown-тело.

    Frontmatter — простые пары `key: value` (без YAML-зависимости).
    Возвращает dict с ключами slug, title, date, summary, body.
    Обязателен только title; дата без него — пустая строка, такие посты
    уезжают в конец списка.
    """
    with open(path, 'r', encoding='utf-8') as f:
        raw = f.read()

    meta = {}
    body = raw
    if raw.lstrip().startswith('---'):
        rest = raw.lstrip()[3:].lstrip('\n')
        parts = re.split(r'^---\s*$', rest, maxsplit=1, flags=re.MULTILINE)
        if len(parts) == 2:
            front, body = parts
            for line in front.splitlines():
                line = line.strip()
                if not line or line.startswith('#') or ':' not in line:
                    continue
                key, value = line.split(':', 1)
                meta[key.strip().lower()] = value.strip()

    return {
        'slug': meta.get('slug') or slug_from_filename(path),
        'title': meta.get('title') or slug_from_filename(path),
        'date': meta.get('date', ''),
        'summary': meta.get('summary', ''),
        'body': body.strip('\n'),
    }


def load_posts(source_dir):
    """Все посты из каталога, отсортированные по дате (свежие первыми)."""
    if not os.path.isdir(source_dir):
        return []

    posts = []
    for name in sorted(os.listdir(source_dir)):
        if not name.endswith('.md') or name.startswith('_'):
            continue
        try:
            posts.append(parse_post(os.path.join(source_dir, name)))
        except Exception as e:
            print(f"Warning: could not read blog post {name}: {e}")

    # Пустая дата сортируется как самая старая, чтобы битый пост не всплыл
    # на титульную как последняя запись.
    posts.sort(key=lambda p: p.get('date') or '', reverse=True)
    return posts


def format_date(value):
    """2026-08-15 -> '15 августа 2026'. Нераспознанное отдаём как есть."""
    try:
        d = datetime.date.fromisoformat(str(value).strip())
    except (ValueError, TypeError):
        return str(value or '')
    return f"{d.day} {RU_MONTHS[d.month - 1]} {d.year}"


# --------------------------------------------------------------------------
# Минимальный markdown
# --------------------------------------------------------------------------

# URL в markdown-ссылке может содержать парные скобки — у Википедии это норма
# («…/Объединение_Германии_(1990)»). Наивное `[^)\s]+` обрывало адрес на первой
# закрывающей скобке: ссылка вела в 404, а лишняя «)» оставалась в тексте рядом.
# Поддерживаем один уровень вложенности — этого хватает на все реальные адреса,
# а полноценный баланс скобок регулярным выражением не выражается.
MD_LINK_RE = re.compile(r'\[([^\]]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)')


def _render_inline(text):
    """Инлайн-разметка. Экранируем ДО вставки тегов — источник доверенный,
    но HTML в постах мы намеренно не поддерживаем."""
    out = _esc(text)
    # `код` — раньше остального, чтобы звёздочки внутри не съедались
    out = re.sub(r'`([^`]+)`', r'<code>\1</code>', out)
    # [текст](url); url прогоняем через тот же escape, схемы ограничиваем
    def _link(m):
        label, href = m.group(1), m.group(2)
        if not re.match(r'^(https?:|mailto:|\.{0,2}/|[\w.-]+\.html|\?)', href):
            return f'{label} ({href})'
        return f'<a href="{href}">{label}</a>'
    out = MD_LINK_RE.sub(_link, out)
    out = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', out)
    out = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<em>\1</em>', out)
    return out


def render_markdown(text):
    """Блочный markdown -> HTML. Поддержаны h2-h4, p, ul, ol, blockquote, hr."""
    html = []
    list_tag = None       # 'ul' | 'ol' | None
    paragraph = []        # накопленные строки текущего абзаца
    quote = []            # накопленные строки текущей цитаты

    def close_paragraph():
        if paragraph:
            html.append(f"<p>{_render_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def close_list():
        nonlocal list_tag
        if list_tag:
            html.append(f"</{list_tag}>")
            list_tag = None

    def close_quote():
        if quote:
            body = ' '.join(quote)
            html.append(f"<blockquote><p>{_render_inline(body)}</p></blockquote>")
            quote.clear()

    def close_all():
        close_paragraph()
        close_list()
        close_quote()

    for line in (text or '').split('\n'):
        stripped = line.strip()

        if not stripped:
            close_all()
            continue

        if re.match(r'^(-{3,}|\*{3,})$', stripped):
            close_all()
            html.append('<hr>')
            continue

        heading = re.match(r'^(#{1,6})\s+(.*)$', stripped)
        if heading:
            close_all()
            # h1 занят заголовком поста — сдвигаем уровни на один вниз
            level = min(len(heading.group(1)) + 1, 6)
            html.append(f"<h{level}>{_render_inline(heading.group(2))}</h{level}>")
            continue

        if stripped.startswith('>'):
            close_paragraph()
            close_list()
            quote.append(stripped.lstrip('>').strip())
            continue

        bullet = re.match(r'^[-*+]\s+(.*)$', stripped)
        numbered = re.match(r'^\d+[.)]\s+(.*)$', stripped)
        if bullet or numbered:
            close_paragraph()
            close_quote()
            want = 'ul' if bullet else 'ol'
            if list_tag != want:
                close_list()
                html.append(f"<{want}>")
                list_tag = want
            item = (bullet or numbered).group(1)
            html.append(f"<li>{_render_inline(item)}</li>")
            continue

        # Продолжение элемента списка с отступом — приклеиваем к нему
        if list_tag and line.startswith(('  ', '\t')) and html and html[-1].endswith('</li>'):
            html[-1] = html[-1][:-len('</li>')] + ' ' + _render_inline(stripped) + '</li>'
            continue

        close_list()
        close_quote()
        paragraph.append(stripped)

    close_all()
    return '\n'.join(html)


# --------------------------------------------------------------------------
# Страницы
# --------------------------------------------------------------------------

SITE_URL = os.getenv('REDPEN_SITE_URL', 'https://medinsky.net').rstrip('/')


def _page(title, description, prefix, timestamp, body_html, canonical_path=''):
    """
    Общая обёртка страницы блога.

    prefix — относительный путь до корня сайта ('' для blog/index.html,
    '../' для blog/<slug>/index.html). Абсолютных путей здесь быть не должно:
    сайт обязан открываться из любой папки.

    canonical_path — путь страницы от корня сайта ('blog/', 'blog/<slug>/').
    Только canonical и og:url обязаны быть абсолютными; всё, что страница
    реально загружает, остаётся относительным.
    """
    up = '../' + prefix  # из blog/ до корня — ещё один уровень
    canonical = f"{SITE_URL}/{canonical_path}"
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <title>{_esc(title)}</title>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <meta name="description" content="{_esc(description, quote=True)}"/>
  <link rel="canonical" href="{_esc(canonical, quote=True)}"/>
  <meta property="og:type" content="article"/>
  <meta property="og:site_name" content="Мединский.нет"/>
  <meta property="og:title" content="{_esc(title, quote=True)}"/>
  <meta property="og:description" content="{_esc(description, quote=True)}"/>
  <meta property="og:url" content="{_esc(canonical, quote=True)}"/>
  <link rel="stylesheet" href="{up}css/main.css">
  <link rel="stylesheet" href="{up}css/landing.css">
  <link rel="stylesheet" href="{up}css/blog.css">
  <link rel="stylesheet" href="{up}css/responsive.css">
  <link rel="icon" href="{up}favicon.svg">
</head>
<body>
  <a href="{up}index.html" class="blog-home" title="На главную">⌂</a>
  <header>Мединский.нет <span id="timestamp" style="font-size: 0.7rem; font-weight: normal; opacity: 0.8;">Последнее обновление: {timestamp}</span></header>

  <main class="landing">
{body_html}
    <footer class="prose">Последнее обновление: {timestamp}</footer>
  </main>
</body>
</html>
"""


def _post_teaser(post, href):
    """Одна запись в списке: дата, заголовок-ссылка, аннотация."""
    summary_html = ''
    if post.get('summary'):
        summary_html = f"""
        <p class="blog-teaser__summary">{_esc(post['summary'])}</p>"""
    return f"""      <article class="blog-teaser">
        <p class="blog-teaser__date">{_esc(format_date(post.get('date')))}</p>
        <h3 class="blog-teaser__title"><a href="{href}">{_esc(post['title'])}</a></h3>{summary_html}
      </article>
"""


def render_latest_section(posts):
    """Секция «Блог» для титульной: последняя запись + ссылка на архив."""
    if not posts:
        return ''

    latest = posts[0]
    summary_html = ''
    if latest.get('summary'):
        summary_html = f"""
        <p class="blog-latest__summary">{_esc(latest['summary'])}</p>"""

    return f"""
    <section class="blog-section prose">
      <h2>Блог</h2>
      <article class="blog-latest">
        <p class="blog-latest__date">{_esc(format_date(latest.get('date')))}</p>
        <h3 class="blog-latest__title">{_esc(latest['title'])}</h3>{summary_html}
        <a class="btn" href="blog/{latest['slug']}/index.html">Читать запись</a>
      </article>
      <p class="blog-section__more"><a href="blog/index.html">Все записи блога →</a></p>
    </section>
"""


def build_blog(output_dir, source_dir=None, timestamp=None, auto_header=''):
    """
    Отрендерить блог в <output_dir>/blog/.

    Пишет blog/index.html (архив) и blog/<slug>/index.html на каждый пост.
    Возвращает список постов (свежие первыми) — из него титульная берёт
    последнюю запись.
    """
    if source_dir is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        source_dir = os.path.join(project_root, BLOG_SOURCE_DIRNAME)
    if timestamp is None:
        timestamp = datetime.datetime.now().strftime('%d.%m.%Y')

    posts = load_posts(source_dir)
    if not posts:
        print(f"[!] No blog posts found in {source_dir} — skipping blog build")
        return posts

    blog_dir = os.path.join(output_dir, 'blog')
    os.makedirs(blog_dir, exist_ok=True)

    # Архив
    teasers = ''.join(_post_teaser(p, f"{p['slug']}/index.html") for p in posts)
    index_body = f"""    <section class="blog-index prose">
      <h1>Блог</h1>
      <p class="blog-index__lead">Заметки о том, как устроен разбор и что мы находим в учебнике.</p>
{teasers}    </section>
"""
    index_path = os.path.join(blog_dir, 'index.html')
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(auto_header + _page(
            'Блог — Мединский.нет',
            'Заметки проекта «Мединский.нет»: как устроен разбор единого учебника истории.',
            '', timestamp, index_body, canonical_path='blog/'))

    # Страницы постов
    for post in posts:
        post_dir = os.path.join(blog_dir, post['slug'])
        os.makedirs(post_dir, exist_ok=True)
        body = f"""    <article class="blog-post prose">
      <p class="blog-post__date">{_esc(format_date(post.get('date')))}</p>
      <h1>{_esc(post['title'])}</h1>
      <div class="blog-post__body">
{render_markdown(post['body'])}
      </div>
      <p class="blog-post__back"><a href="../index.html">← Все записи блога</a></p>
    </article>
"""
        with open(os.path.join(post_dir, 'index.html'), 'w', encoding='utf-8') as f:
            f.write(auto_header + _page(
                f"{post['title']} — Мединский.нет",
                post.get('summary') or post['title'],
                '../', timestamp, body, canonical_path=f"blog/{post['slug']}/"))

    print(f"[+] Built blog: {len(posts)} post(s) in {blog_dir}")
    return posts
