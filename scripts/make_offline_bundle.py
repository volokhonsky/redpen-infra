#!/usr/bin/env python3
"""
make_offline_bundle.py

Собирает офлайн-архив разбора одной книги: самодостаточную копию сайта, которую
можно распаковать на флешку и читать без интернета (см.
``docs/offline-bundle-plan.md``).

Что делает сборщик, чего не делает обычная публикация: собирает каталог книги,
общую статику и точку входа в один архив и **проверяет грепом**, что в нём не
осталось ни одного адреса, ведущего наружу (``--check`` включён по умолчанию).

Раньше он делал больше: писал ``offline-data.js`` со всеми данными книги и
подключал шим ``js/redpen-offline.js``, потому что под ``file://`` запрещён
fetch к соседним файлам. Это было нужно старому SPA. Постраничный просмотрщик
не делает ни одного запроса вовсе — замечания приезжают инлайновым блоком
``redpen-page-data`` внутри самой страницы, — поэтому с 2026-08-30 бандл это
просто копия статики, а шим и ``offline-data.js`` (около 3.6 МБ) выброшены
вместе с SPA.

Использование:

    python3 scripts/make_offline_bundle.py --doc medinsky11klass \\
        --site-dir redpen-publish --out tmp/redpen-medinsky11klass-offline.zip
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# Хосты, которых в html/js офлайн-копии быть не должно. Страницы книги — тоже
# html, и в телах замечаний бывают ссылки на источники, но это ссылки наружу
# в тексте, а не наши хосты: список именно из наших.
FORBIDDEN_HOSTS = ('api.medinsky.net', 'cdn.jsdelivr.net', 'accounts.google.com')

# Скрипты редактора: офлайн они бесполезны и как раз они умеют ходить в API.
# Остальные («редакторская панель», «бутстрап») удалены вместе с SPA.
EDITOR_SCRIPTS = ('redpen-auth.js',)

# --------------------------------------------------------------------------
# сбор данных
# --------------------------------------------------------------------------

def _read_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def count_pages_and_remarks(doc_dir):
    """(страниц с замечаниями, замечаний) — только для строк отчёта и README.

    Черновики не считаем: в архиве они лежат, но читателю не видны, ровно как
    на сайте. Легаси-компаньоны page_NNN.drafts.json в архив не попадают вовсе.
    """
    ann_dir = os.path.join(doc_dir, 'remarks')
    pages = 0
    remarks = 0
    if not os.path.isdir(ann_dir):
        return 0, 0
    for name in sorted(os.listdir(ann_dir)):
        if not name.endswith('.json') or name.endswith('.drafts.json'):
            continue
        data = _read_json(os.path.join(ann_dir, name))
        if not isinstance(data, list):
            continue
        published = [a for a in data if isinstance(a, dict) and not a.get('draft')]
        if published:
            pages += 1
            remarks += len(published)
    return pages, remarks


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
    Это полная копия сайта medinsky.net: страницы, текст и замечания лежат
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


def render_readme(doc_id, metadata, built_at, pages, remarks_count):
    return """Красной ручкой — офлайн-копия разбора
=====================================

Книга: {title}
Страниц: {pages}
Замечаний: {anns}
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
  {doc}/     — сам разбор: страницы, текст, замечания
  css/, js/          — оформление и скрипты просмотрщика

Чего здесь нет
--------------
Редактирования: разбор правится только на сайте. В этой копии нет ни кода,
обращающегося к нашему серверу, ни счётчиков, ни аналитики — она ничего никуда
не отправляет.

Адреса страниц
--------------
Работают так же, как на сайте: index.html?p=17 откроет страницу 17.
""".format(title=metadata.get('title') or doc_id, pages=pages, anns=remarks_count,
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

    # Редакторские скрипты в архиве не нужны вовсе.
    for name in EDITOR_SCRIPTS:
        path = os.path.join(root, 'js', name)
        if os.path.exists(path):
            os.remove(path)

    # 2. каталог книги — прямой копией. Правка index.html больше не нужна:
    # оглавление и страницы не содержат ни одного сетевого вызова, а
    # document_index.html (старый SPA, который их и содержал) удалён.
    doc_dst = os.path.join(root, doc_id)
    shutil.copytree(
        doc_src, doc_dst,
        ignore=shutil.ignore_patterns('document_index.html', '*.drafts.json'))

    # 3. корневые файлы
    metadata = _read_json(os.path.join(doc_src, 'metadata.json'))
    built_at = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    ann_pages, ann_count = count_pages_and_remarks(doc_src)
    pages = len(metadata.get('pages') or []) or ann_pages
    with open(os.path.join(root, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(render_root_index(doc_id, metadata, built_at))
    with open(os.path.join(root, 'ЧИТАТЬ.html'), 'w', encoding='utf-8') as f:
        f.write(render_entry_redirect(doc_id))
    with open(os.path.join(root, 'README.txt'), 'w', encoding='utf-8') as f:
        f.write(render_readme(doc_id, metadata, built_at, pages, ann_count))

    stats = {
        'doc': doc_id,
        'pages': pages,
        'remarks': ann_count,
        'builtAt': built_at,
    }
    return root, stats


def check_no_network_refs(root):
    """Ни один html/js бандла (кроме данных) не должен упоминать наши хосты."""
    problems = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(('.html', '.js')):
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
    print('    страниц: %(pages)s, замечаний: %(remarks)s' % stats)
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
