#!/usr/bin/env python3
"""
make_offline_bundle.py

Собирает офлайн-архив разбора одной книги: самодостаточную копию сайта, которую
можно распаковать на флешку и читать без интернета (см.
``docs/offline-bundle-plan.md``).

Что делает сборщик, чего не делает обычная публикация:

1. пишет ``offline-data.js`` — все ``metadata.json``/``annotations/*.json``/
   ``text/*.json`` одним файлом, потому что под ``file://`` fetch к соседним
   файлам запрещён (подменой занимается ``js/redpen-offline.js``);
2. вырезает из index.html книги всё, что умеет ходить в сеть: скрипты с
   абсолютными http(s)-адресами, ``REDPEN_API_BASE`` и редакторские скрипты.
   После этого офлайн-копия физически не содержит кода, обращающегося к API, —
   и это проверяется грепом (``--check`` включён по умолчанию).

Использование:

    python3 scripts/make_offline_bundle.py --doc medinsky11klass \\
        --site-dir redpen-publish --out tmp/redpen-medinsky11klass-offline.zip
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# Хосты, которых в html/js офлайн-копии быть не должно. Тела аннотаций
# (offline-data.js) не проверяем: там живут ссылки на источники, это нормально.
FORBIDDEN_HOSTS = ('api.medinsky.net', 'cdn.jsdelivr.net', 'accounts.google.com')

# Скрипты редактора: офлайн они бесполезны и как раз они умеют ходить в API.
EDITOR_SCRIPTS = (
    'redpen-editor-panel.js',
    'redpen-auth.js',
    'redpen-editor-bootstrap.js',
)

PAGE_JSON_RE = re.compile(r'^(?P<page>.+)\.json$')


# --------------------------------------------------------------------------
# сбор данных
# --------------------------------------------------------------------------

def _read_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def collect_page_data(doc_dir, subdir):
    """{page_id: <разобранный json>} для annotations/ или text/.

    Легаси-компаньоны ``*.drafts.json`` пропускаем: черновики давно лежат в
    основном ``page_NNN.json`` под тегом draft.
    """
    src = os.path.join(doc_dir, subdir)
    out = {}
    if not os.path.isdir(src):
        return out
    for name in sorted(os.listdir(src)):
        m = PAGE_JSON_RE.match(name)
        if not m:
            continue
        page = m.group('page')
        if page.endswith('.drafts'):
            continue
        out[page] = _read_json(os.path.join(src, name))
    return out


def build_offline_data_js(metadata, annotations, text):
    """``window.REDPEN_OFFLINE = JSON.parse("...")``.

    Именно JSON.parse из строкового литерала, а не объектный литерал: движок
    разбирает такой мегабайтный payload заметно быстрее.
    """
    payload = {'metadata': metadata, 'annotations': annotations, 'text': text}
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    # Экранируем как JS-строку: json.dumps даёт корректный литерал в двойных
    # кавычках, включая \u2028/\u2029 (ensure_ascii=False их бы оставил живыми,
    # а в JS они ломают строку).
    literal = json.dumps(raw, ensure_ascii=False).replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    return (
        '// Данные офлайн-копии: подменяют fetch(metadata/annotations/text).\n'
        '// Сгенерировано scripts/make_offline_bundle.py — не править руками.\n'
        'window.REDPEN_OFFLINE = JSON.parse(%s);\n' % literal
    )


# --------------------------------------------------------------------------
# правка index.html книги
# --------------------------------------------------------------------------

def rewrite_doc_index(html, has_vendored_marked):
    """Убирает из страницы книги всё сетевое и подключает офлайн-данные."""
    # 1. Скрипты с абсолютным http(s)-адресом (marked с CDN и что угодно ещё).
    html = re.sub(r'[ \t]*<script[^>]+src="https?://[^"]*"[^>]*>\s*</script>\s*\n?', '', html)

    # 2. Инлайн-блок с адресом API и Google client id.
    html = re.sub(
        r'[ \t]*<script>\s*\n?(?:[^<]*REDPEN_API_BASE[^<]*)</script>\s*\n?',
        '', html, flags=re.DOTALL)

    # 3. Редакторские скрипты.
    for name in EDITOR_SCRIPTS:
        html = re.sub(
            r'[ \t]*<script[^>]+src="[^"]*%s(?:\?[^"]*)?"[^>]*>\s*</script>\s*\n?' % re.escape(name),
            '', html)

    # 4. Офлайн-данные и шим — до всех остальных скриптов страницы.
    inserts = []
    if has_vendored_marked:
        inserts.append('  <script src="../js/vendor/marked.min.js"></script>')
    inserts.append('  <script src="offline-data.js"></script>')
    inserts.append('  <script src="../js/redpen-offline.js"></script>')
    block = '\n'.join(inserts) + '\n'

    m = re.search(r'(?P<indent>[ \t]*)<script[^>]+src="\.\./js/', html)
    if not m:
        raise RuntimeError('в index.html книги не найдено ни одного скрипта ../js/ — шаблон изменился?')
    # m.start() указывает на начало отступа — возвращаем его следующему тегу,
    # иначе он уезжает к нулевой колонке.
    html = html[:m.start()] + block + m.group('indent') + html[m.end('indent'):]
    return html


# --------------------------------------------------------------------------
# генерируемые файлы бандла
# --------------------------------------------------------------------------

def _esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def render_root_index(doc_id, metadata, built_at):
    """Корневой экран архива.

    Сайтовый селектор сюда не годится: он ссылается на другие книги, которых в
    архиве нет.
    """
    title = metadata.get('title') or doc_id
    description = metadata.get('description') or ''
    icon = metadata.get('icon')
    cover = ('\n      <img class="cover" src="%s/%s" alt="Обложка" width="140">'
             % (_esc(doc_id), _esc(icon))) if icon else ''
    return """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Красной ручкой — офлайн-копия</title>
  <link rel="icon" href="favicon.svg">
  <style>
    body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
           max-width: 44rem; margin: 0 auto; padding: 2.5rem 1.25rem; line-height: 1.55; color: #222; }}
    h1 {{ font-size: 1.6rem; margin-bottom: .25rem; }}
    .cover {{ float: right; margin: 0 0 1rem 1.5rem; border: 1px solid #ddd; }}
    .btn {{ display: inline-block; margin-top: 1rem; padding: .6rem 1.2rem; background: #DC143C;
           color: #fff; text-decoration: none; border-radius: 4px; font-weight: 600; }}
    .note {{ margin-top: 2rem; padding: 1rem 1.25rem; background: #f6f6f6; border-left: 3px solid #DC143C;
            font-size: .95rem; }}
    footer {{ margin-top: 2.5rem; color: #777; font-size: .85rem; clear: both; }}
  </style>
</head>
<body>
  <h1>Красной ручкой</h1>
  <p>Офлайн-копия разбора. Интернет не нужен.</p>
  <section>{cover}
    <h2>{title}</h2>
    <p>{description}</p>
    <a class="btn" href="{doc}/index.html">Открыть разбор</a>
  </section>
  <div class="note">
    Это полная копия сайта medinsky.net: страницы, текст и аннотации лежат
    файлами рядом. Ничего никуда не отправляется — в копии нет кода, способного
    обратиться к сети. Редактирование здесь не работает: разбор правится только
    на сайте.
  </div>
  <footer>Собрано: {built_at}</footer>
</body>
</html>
""".format(cover=cover, title=_esc(title), description=_esc(description),
           doc=_esc(doc_id), built_at=_esc(built_at))


def render_entry_redirect(doc_id):
    return """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="0;url=index.html">
  <title>Красной ручкой</title>
</head>
<body>
  <p>Открываю разбор: <a href="index.html">index.html</a></p>
</body>
</html>
"""


def render_readme(doc_id, metadata, built_at, pages, annotations_count):
    return """Красной ручкой — офлайн-копия разбора
=====================================

Книга: {title}
Страниц: {pages}
Аннотаций: {anns}
Собрано: {built_at}
Источник: https://medinsky.net

Как читать
----------
Откройте файл ЧИТАТЬ.html (или index.html) двойным щелчком — он откроется в
браузере. Интернет не нужен, устанавливать ничего не надо. Каталог можно
целиком скопировать на флешку.

Что внутри
----------
  index.html         — начальный экран
  {doc}/     — сам разбор: страницы, текст, аннотации
  css/, js/          — оформление и скрипты просмотрщика

Чего здесь нет
--------------
Редактирования: разбор правится только на сайте. В этой копии нет ни кода,
обращающегося к нашему серверу, ни счётчиков, ни аналитики — она ничего никуда
не отправляет.

Адреса страниц
--------------
Работают так же, как на сайте: index.html?p=17 откроет страницу 17.
""".format(title=metadata.get('title') or doc_id, pages=pages, anns=annotations_count,
           built_at=built_at, doc=doc_id)


# --------------------------------------------------------------------------
# сборка
# --------------------------------------------------------------------------

def stage_bundle(site_dir, doc_id, stage_root, bundle_name):
    """Собирает дерево бандла в stage_root/<bundle_name>. Возвращает (путь, статистика)."""
    doc_src = os.path.join(site_dir, doc_id)
    for required in (os.path.join(site_dir, 'css'), os.path.join(site_dir, 'js'),
                     doc_src, os.path.join(doc_src, 'index.html'),
                     os.path.join(doc_src, 'metadata.json')):
        if not os.path.exists(required):
            raise SystemExit('[!] не найдено: %s' % required)

    root = os.path.join(stage_root, bundle_name)
    if os.path.exists(root):
        shutil.rmtree(root)
    os.makedirs(root)

    # 1. общая статика сайта
    shutil.copytree(os.path.join(site_dir, 'css'), os.path.join(root, 'css'))
    shutil.copytree(os.path.join(site_dir, 'js'), os.path.join(root, 'js'))
    favicon = os.path.join(site_dir, 'favicon.svg')
    if os.path.exists(favicon):
        shutil.copy2(favicon, os.path.join(root, 'favicon.svg'))

    # Шим мог не попасть в опубликованный js/ (сайт собран до его появления).
    shim_dst = os.path.join(root, 'js', 'redpen-offline.js')
    if not os.path.exists(shim_dst):
        shutil.copy2(os.path.join(PROJECT_ROOT, 'templates', 'js', 'redpen-offline.js'), shim_dst)

    # Редакторские скрипты в архиве не нужны вовсе.
    for name in EDITOR_SCRIPTS:
        path = os.path.join(root, 'js', name)
        if os.path.exists(path):
            os.remove(path)

    # 2. каталог книги
    doc_dst = os.path.join(root, doc_id)
    shutil.copytree(
        doc_src, doc_dst,
        ignore=shutil.ignore_patterns('document_index.html', '*.drafts.json'))

    # 3. данные для file://
    metadata = _read_json(os.path.join(doc_src, 'metadata.json'))
    annotations = collect_page_data(doc_src, 'annotations')
    text = collect_page_data(doc_src, 'text')
    with open(os.path.join(doc_dst, 'offline-data.js'), 'w', encoding='utf-8') as f:
        f.write(build_offline_data_js(metadata, annotations, text))

    # 4. index.html книги без сетевых зависимостей
    has_vendored_marked = os.path.exists(os.path.join(root, 'js', 'vendor', 'marked.min.js'))
    with open(os.path.join(doc_src, 'index.html'), 'r', encoding='utf-8') as f:
        html = f.read()
    with open(os.path.join(doc_dst, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(rewrite_doc_index(html, has_vendored_marked))

    # 5. корневые файлы
    built_at = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    pages = len(metadata.get('pages') or []) or len(text) or len(annotations)
    ann_count = sum(len(v) for v in annotations.values() if isinstance(v, list))
    with open(os.path.join(root, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(render_root_index(doc_id, metadata, built_at))
    with open(os.path.join(root, 'ЧИТАТЬ.html'), 'w', encoding='utf-8') as f:
        f.write(render_entry_redirect(doc_id))
    with open(os.path.join(root, 'README.txt'), 'w', encoding='utf-8') as f:
        f.write(render_readme(doc_id, metadata, built_at, pages, ann_count))

    stats = {
        'doc': doc_id,
        'pages': pages,
        'annotations': ann_count,
        'builtAt': built_at,
        'vendoredMarked': has_vendored_marked,
    }
    return root, stats


def check_no_network_refs(root):
    """Ни один html/js бандла (кроме данных) не должен упоминать наши хосты."""
    problems = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(('.html', '.js')):
                continue
            if name == 'offline-data.js':  # тела аннотаций, ссылки на источники — норма
                continue
            path = os.path.join(dirpath, name)
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            for host in FORBIDDEN_HOSTS:
                if host in content:
                    problems.append('%s: %s' % (os.path.relpath(path, root), host))
    return problems


def write_zip(root, out_path):
    """Пакует дерево. Картинки — без сжатия: PNG всё равно не жмётся, а так
    архив собирается и распаковывается заметно быстрее."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)
    base = os.path.dirname(root)
    with zipfile.ZipFile(out_path, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                arcname = os.path.relpath(path, base)
                stored = name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif'))
                zf.write(path, arcname,
                         compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
    return out_path


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Сборка офлайн-архива разбора одной книги')
    parser.add_argument('--doc', required=True, help='идентификатор книги (каталог в корне сайта)')
    parser.add_argument('--site-dir', default=os.path.join(PROJECT_ROOT, 'redpen-publish'),
                        help='корень опубликованного сайта (по умолчанию redpen-publish)')
    parser.add_argument('--out', help='путь к zip (по умолчанию tmp/redpen-<doc>-offline.zip)')
    parser.add_argument('--stage-dir', help='каталог для промежуточного дерева (по умолчанию рядом с --out)')
    parser.add_argument('--name', help='имя каталога внутри архива (по умолчанию redpen-<doc>-offline)')
    parser.add_argument('--no-zip', action='store_true', help='оставить дерево, не паковать')
    parser.add_argument('--keep-stage', action='store_true', help='не удалять промежуточное дерево после упаковки')
    args = parser.parse_args(argv)

    bundle_name = args.name or ('redpen-%s-offline' % args.doc)
    out_path = args.out or os.path.join(PROJECT_ROOT, 'tmp', bundle_name + '.zip')
    stage_dir = args.stage_dir or os.path.join(
        os.path.dirname(os.path.abspath(out_path)) or '.', '_offline-stage')
    os.makedirs(stage_dir, exist_ok=True)

    print('=== Сборка офлайн-архива: %s ===' % args.doc)
    root, stats = stage_bundle(args.site_dir, args.doc, stage_dir, bundle_name)
    print('[+] дерево собрано: %s' % root)
    print('    страниц: %(pages)s, аннотаций: %(annotations)s' % stats)
    if not stats['vendoredMarked']:
        print('[!] js/vendor/marked.min.js нет — разметка аннотаций офлайн будет'
              ' упрощённой (fallback в comment-content.js)')

    problems = check_no_network_refs(root)
    if problems:
        print('[!] в бандле остались сетевые ссылки:')
        for p in problems:
            print('    -', p)
        return 1
    print('[+] сетевых ссылок в html/js нет')

    if args.no_zip:
        print('[+] --no-zip: архив не собираю')
        return 0

    print('[*] пакую...')
    write_zip(root, out_path)
    size = os.path.getsize(out_path)
    digest = sha256_of(out_path)
    manifest = dict(stats, file=os.path.basename(out_path), size=size, sha256=digest)
    with open(out_path + '.json', 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    if not args.keep_stage:
        shutil.rmtree(root, ignore_errors=True)

    print('[+] %s (%.1f МБ)' % (out_path, size / 1024 / 1024))
    print('[+] sha256: %s' % digest)
    print('[+] манифест: %s.json' % out_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
