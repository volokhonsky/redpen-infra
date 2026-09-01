#!/usr/bin/env python3
"""
build_website.py

A comprehensive script for building and publishing the website.

This script:
1. Converts markdown remarks to JSON
2. Runs remark position tests to verify correct positioning
3. Publishes data (images, text, remarks) to the target directory (default: redpen-publish)
4. Optionally commits and pushes changes to the redpen-publish repository (if target is the default redpen-publish)

Usage:
    python scripts/build_website.py [--skip-tests] [--skip-push] [--target-dir TARGET_DIR] [--document DOCUMENT] [--folders FOLDERS]

Options:
    --skip-tests    Принимается для совместимости (браузерных тестов в сборке больше нет)
    --skip-push     Skip pushing changes to the redpen-publish submodule
    --target-dir    Specify a target directory for the build output (default: redpen-publish)
    --document      Specify a document to build (default: build all documents)
    --folders       Comma-separated list of specific folders to deploy (default: all folders)
"""

import os
import sys
import argparse
import subprocess
import importlib.util
import json
import shutil
import glob
import datetime

# ===== Helper functions for backup/clean/compare of publish directory =====

def snapshot_paths(root):
    """Return a sorted list of file paths (relative to root), excluding any .git entries (dirs or files)."""
    paths = []
    if not os.path.isdir(root):
        return paths
    for dirpath, dirnames, filenames in os.walk(root):
        # Do not descend into .git directories
        dirnames[:] = [d for d in dirnames if d != '.git']
        rel_dir = os.path.relpath(dirpath, root)
        for name in filenames:
            # Skip .git files (submodule pointer)
            if name == '.git':
                continue
            # Build relative path, normalize to avoid './'
            p = os.path.normpath(os.path.join(rel_dir, name))
            if p != '.':
                paths.append(p)
    return sorted(paths)


def backup_publish_dir(publish_dir):
    """Copy contents of publish_dir (excluding .git) to tmp/redpen-publish-backup-<timestamp>."""
    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = os.path.join(project_root, 'tmp', f'redpen-publish-backup-{ts}')
    os.makedirs(backup_dir, exist_ok=True)
    print(f"\n=== Backing up {publish_dir} -> {backup_dir} (excluding .git) ===")
    if os.path.isdir(publish_dir):
        for entry in os.listdir(publish_dir):
            if entry == '.git':
                continue
            src_path = os.path.join(publish_dir, entry)
            dst_path = os.path.join(backup_dir, entry)
            if os.path.isdir(src_path):
                shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
            else:
                # copy2 preserves metadata where possible
                shutil.copy2(src_path, dst_path)
    return backup_dir


def clean_publish_dir(publish_dir):
    """Remove all entries in publish_dir except .git."""
    if not os.path.isdir(publish_dir):
        return
    print(f"\n=== Cleaning {publish_dir} (keeping .git) ===")
    for entry in os.listdir(publish_dir):
        if entry == '.git':
            continue
        p = os.path.join(publish_dir, entry)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
        except Exception as e:
            print(f"[!] Failed to remove {p}: {e}")


def compare_path_sets(before_paths, after_paths):
    """Compare two path lists; print differences; return True if nothing was lost."""
    before = set(before_paths)
    after = set(after_paths)
    missing = sorted(before - after)
    added = sorted(after - before)
    print("\n=== Path Set Comparison ===")
    print(f"Files before: {len(before)}, after: {len(after)}")
    if missing:
        print("Missing after rebuild:")
        for p in missing:
            print("  -", p)
    if added:
        print("New after rebuild:")
        for p in added:
            print("  +", p)
    return len(missing) == 0

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import blog as blog_builder  # noqa: E402  (после правки sys.path)
import chapters  # noqa: E402
import page_html  # noqa: E402
import sitemap  # noqa: E402

def get_document_folders(specific_folders=None):
    """
    Get a list of document folders from redpen-content directory.

    Args:
        specific_folders (list): Optional list of specific folders to include.
                                If provided, only these folders will be returned if they exist.

    Returns:
        list: List of document folder names
    """
    content_dir = os.path.join(project_root, 'redpen-content')

    # If specific folders are provided, filter by them
    if specific_folders:
        folders = []
        for folder in specific_folders:
            folder_path = os.path.join(content_dir, folder)
            if os.path.isdir(folder_path):
                folders.append(folder)
            else:
                print(f"Warning: Specified folder '{folder}' not found in redpen-content")
        return folders

    # Otherwise, get all folders in the content directory
    folders = []
    for item in glob.glob(os.path.join(content_dir, '*')):
        if os.path.isdir(item):
            folder_name = os.path.basename(item)
            # Check if this is a valid document folder (has images, text, or remarks subdirectory)
            if any(os.path.isdir(os.path.join(item, subdir)) for subdir in ['images', 'text', 'remarks']):
                folders.append(folder_name)

    if not folders:
        print("Warning: No document folders found in redpen-content directory")

    # Порядок glob не определён; сортируем, чтобы сборка была воспроизводимой,
    # а карточки на титульной не прыгали от сборки к сборке.
    return sorted(folders)

# Import required modules
def import_module_from_file(module_name, file_path):
    """Import a module from a file path"""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Import the remark converter and publish_data modules
remark_converter = import_module_from_file(
    'remark_converter', 
    os.path.join(project_root, 'scripts', 'remark_converter.py')
)
publish_data = import_module_from_file(
    'publish_data',
    os.path.join(project_root, 'scripts', 'publish_data.py')
)
generate_page_manifest = import_module_from_file(
    'generate_page_manifest',
    os.path.join(project_root, 'scripts', 'generate_page_manifest.py')
)

def run_command(command, cwd=None):
    """Run a shell command and return the result"""
    print(f"Running command: {command}")
    result = subprocess.run(
        command, 
        shell=True, 
        cwd=cwd or project_root,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"Command failed with exit code {result.returncode}")
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
        return False, result.stdout, result.stderr

    return True, result.stdout, result.stderr

def convert_remarks(target_dir=None, document=None, specific_folders=None):
    """
    Convert markdown remarks to JSON

    Args:
        target_dir (str): Target directory for output
        document (str): Specific document to convert
        specific_folders (list): List of specific folders to convert
    """
    print(f"\n=== Converting Markdown Remarks to JSON for {document or (', '.join(specific_folders) if specific_folders else 'all documents')} ===")

    documents = [document] if document else get_document_folders(specific_folders)
    publish_root = target_dir or os.path.join(project_root, 'redpen-publish')
    success = True

    for doc in documents:
        md_dir = os.path.join(project_root, 'redpen-content', doc, 'remarks')
        json_dir = os.path.join(publish_root, doc, 'remarks')
        os.makedirs(json_dir, exist_ok=True)
        try:
            remark_converter.md_to_json(md_dir, json_dir)
        except Exception as e:
            print(f"Error converting remarks for {doc}: {e}")
            success = False

    return success

def generate_page_manifests(target_dir=None, document=None, specific_folders=None):
    """
    Generate the `pages` manifest in metadata.json for each document (stage 2 /
    B.2). Documents whose redpen-content/<doc>/meta.json has no
    pageNumbering.printedStartFile/printedStartNumber section are left
    untouched (legacy mode) -- this is the common case today, not an error.

    Returns False (aborting the build) only if a document THAT HAS opted in
    fails manifest validation.
    """
    documents = [document] if document else get_document_folders(specific_folders)
    output_root = target_dir if target_dir else os.path.join(project_root, 'redpen-publish')

    success = True
    for doc in documents:
        doc_dir = os.path.join(output_root, doc)
        meta_path = os.path.join(project_root, 'redpen-content', doc, 'meta.json')
        if not os.path.isdir(doc_dir) or not os.path.isfile(meta_path):
            continue
        try:
            generate_page_manifest.generate(doc_dir, meta_path)
        except generate_page_manifest.ManifestError as e:
            print(f"Error generating page manifest for {doc}: {e}")
            success = False

    return success

def generate_page_html(target_dir=None, document=None, specific_folders=None):
    """
    Rebuild metadata.json's `chapters` from paragraphs_list.txt, then render one
    static HTML file per page (<doc>/pages/<label>/index.html) with the
    published remarks inlined as real text.

    This is what makes the corpus indexable: before it, the whole book was a
    single HTML document that fetched everything over JSON, so a crawler saw
    ~159 characters of text for all 1257 remarks. Must run after
    generate_page_manifests() -- it needs the `pages` manifest -- and after
    remarks/*.json are in place.

    Documents without a pages manifest or a paragraphs_list.txt are skipped,
    the same way legacy documents are skipped elsewhere.
    """
    documents = [document] if document else get_document_folders(specific_folders)
    output_root = target_dir if target_dir else os.path.join(project_root, 'redpen-publish')
    current_timestamp = datetime.datetime.now().strftime('%d.%m.%Y')
    auto_hdr = page_html.AUTO_HEADER

    success = True
    for doc in documents:
        doc_dir = os.path.join(output_root, doc)
        if not os.path.isdir(doc_dir):
            continue
        paragraphs_path = os.path.join(project_root, 'redpen-content', doc, 'paragraphs_list.txt')
        try:
            chapters.generate(doc_dir, paragraphs_path)
            page_html.build_pages(doc_dir, current_timestamp, auto_header=auto_hdr)
        except chapters.ChaptersError as e:
            print(f"Error building chapters for {doc}: {e}")
            success = False
        except (OSError, ValueError, KeyError) as e:
            print(f"Error building per-page HTML for {doc}: {e}")
            success = False

    return success

def _publish_one_document(doc, output_dir, templates_dir):
    """Опубликовать содержимое одного документа в <output_dir>/<doc>/."""
    content_root = os.path.join(project_root, 'redpen-content', doc)
    images_dir = os.path.join(content_root, 'images')
    text_dir = os.path.join(content_root, 'text')
    # Замечания не трогаем: канон — БД, в publish их кладёт API (publisher.py).
    remarks_dir = None

    doc_output_dir = os.path.join(output_dir, doc)
    os.makedirs(doc_output_dir, exist_ok=True)

    publish_data.publish_data(images_dir, text_dir, remarks_dir, doc_output_dir)

    # Иллюстрации, если они есть, ложатся в тот же images/
    illustrations_dir = os.path.join(content_root, 'illustrations')
    if os.path.exists(illustrations_dir) and os.path.isdir(illustrations_dir):
        images_output = os.path.join(doc_output_dir, "images")
        publish_data.copy_files(illustrations_dir, images_output, "*")
        print(f"[+] Published illustrations from {illustrations_dir} to {images_output}")

    meta_json_path = os.path.join(content_root, 'meta.json')
    metadata_json_path = os.path.join(doc_output_dir, 'metadata.json')
    if os.path.exists(meta_json_path):
        shutil.copy2(meta_json_path, metadata_json_path)
        print(f"[+] Copied meta.json to {metadata_json_path}")

    # <doc>/index.html пишет шаг 3.6 (page_html.build_pages) — это оглавление.
    # Здесь раньше публиковался старый SPA (document_index.html), последнее
    # место, где жил режим редактора ?editor=1; редактор переехал в /work/,
    # и SPA удалён 2026-08-30.


def publish_website_data(target_dir=None, document=None, specific_folders=None):
    """
    Publish data to the target directory

    Args:
        target_dir (str): Target directory for output
        document (str): Specific document to publish
        specific_folders (list): List of specific folders to publish
    """
    print(f"\n=== Publishing Website Data for {document or (', '.join(specific_folders) if specific_folders else 'all documents')} ===")

    # Template directories
    templates_dir = os.path.join(project_root, 'templates')

    # Use target_dir if provided, otherwise use default redpen-publish
    if target_dir:
        output_dir = target_dir
    else:
        output_dir = os.path.join(project_root, 'redpen-publish')

    # Create the directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    try:
        # Одна ветка на любое число документов: раньше «один документ» и «все
        # документы» были двумя копиями одного блока, которые успели разойтись.
        documents = [document] if document else get_document_folders(specific_folders)

        for doc in documents:
            _publish_one_document(doc, output_dir, templates_dir)

        # Copy template files (CSS, JS, HTML, etc.)
        print("\n=== Copying Template Files ===")
        # Copy CSS files
        css_src = os.path.join(templates_dir, 'css')
        css_dest = os.path.join(output_dir, 'css')
        if os.path.exists(css_src):
            publish_data.copy_files(css_src, css_dest, "*.css")

        # Copy JS files
        js_src = os.path.join(templates_dir, 'js')
        js_dest = os.path.join(output_dir, 'js')
        if os.path.exists(js_src):
            publish_data.copy_files(js_src, js_dest, "*.js")

        # Copy favicon
        if os.path.exists(templates_dir):
            publish_data.copy_files(templates_dir, output_dir, "*.svg")

        # Рабочее место и опросник устроены одинаково: своя точка входа,
        # свой html/js/css, все данные из API. Просмотрщик о них не знает.
        # Опросник (`/survey/`) открыт кому угодно, рабочее место (`/work/`) —
        # только закрытому кругу, но к статике это отношения не меняет.
        # Заглушки-редиректы `cabinet`/`app` сняты 2026-09-01 (жили с 2026-08-31).
        # На томе публикации их каталоги убирает rsync `--delete` в content-sync,
        # как только они уходят из git redpen-publish (`*/remarks/` там исключён,
        # эти два — нет).
        for app_name in ('survey', 'work'):
            app_src = os.path.join(templates_dir, app_name)
            app_dest = os.path.join(output_dir, app_name)
            if os.path.exists(app_src):
                for pattern in ("*.html", "*.js", "*.css"):
                    publish_data.copy_files(app_src, app_dest, pattern)

        return True
    except Exception as e:
        print(f"Error publishing data: {e}")
        return False

def _document_subtitle(meta_data):
    """Build the "авторы · издательство, год" line for a document card."""
    parts = []
    authors = meta_data.get('authors') or []
    if isinstance(authors, list) and authors:
        parts.append(', '.join(str(a) for a in authors))
    imprint = []
    if meta_data.get('publisher'):
        imprint.append(str(meta_data['publisher']))
    published_at = str(meta_data.get('publishedAt') or '')
    if len(published_at) >= 4 and published_at[:4].isdigit():
        imprint.append(published_at[:4])
    if imprint:
        parts.append(', '.join(imprint))
    return ' · '.join(parts)


def _iter_published_pages(doc_output_dir):
    """Опубликованные замечания постранично: (имя файла, [замечание, ...]).

    Один обход <doc>/remarks/ на всех, кто считает статистику публикации.
    Черновики лежат в тех же page_NNN.json под флагом draft и не считаются:
    без ?showDrafts=1 читатель их не видит. Легаси-компаньоны
    page_NNN.drafts.json пропускаются по той же причине. Страницы без единого
    опубликованного замечания не выдаются вовсе.
    """
    ann_dir = os.path.join(doc_output_dir, 'remarks')
    if not os.path.isdir(ann_dir):
        return
    for name in sorted(os.listdir(ann_dir)):
        if not name.startswith('page_') or not name.endswith('.json'):
            continue
        if name.endswith('.drafts.json'):
            continue
        try:
            with open(os.path.join(ann_dir, name), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        published = [a for a in data if isinstance(a, dict) and not a.get('draft')]
        if published:
            yield name, published


def _methods_density(doc_output_dirs):
    """
    Числа для абзаца «Перед вами учебник пропаганды» на титульной.

    Считаем только то, что читатель реально видит: черновики исключены обходом
    _iter_published_pages. `method_pages` — страницы, где есть хотя бы одно
    замечание-приём, то есть категория не «Прочее»; именно на этом числе
    держится фраза «почти на каждой». Категория берётся из общей таблицы
    scripts/annotation_categories.py — той же, по которой красится просмотрщик.
    """
    import annotation_categories

    pages = 0
    method_pages = 0
    remarks = 0
    for doc_output_dir in doc_output_dirs:
        for _name, published in _iter_published_pages(doc_output_dir):
            pages += 1
            remarks += len(published)
            if any(annotation_categories.category_for(a) != annotation_categories.OTHER
                   for a in published):
                method_pages += 1
    return {'pages': pages, 'method_pages': method_pages, 'remarks': remarks}


def _methods_density_sentence(stats):
    """
    Фраза о плотности приёмов — считается по факту, а не зашита в текст.

    Пока опубликована малая часть разбора, «практически на каждой странице»
    было бы преувеличением: на 2026-08 читателю видны 224 замечания на 84
    страницах из 313, остальные 1048 лежат черновиками. Поэтому сильная
    формулировка включается сама, когда доля разобранных страниц с приёмом
    переваливает за 4/5 — то есть после публикации черновиков.
    """
    pages = stats.get('pages') or 0
    method_pages = stats.get('method_pages') or 0
    remarks = stats.get('remarks') or 0
    if not pages or not remarks:
        return 'Разбор ещё готовится.'

    page_word = _plural_ru(pages, 'страницу', 'страницы', 'страниц')
    ann_word = _plural_ru(remarks, 'замечание', 'замечания', 'замечаний')
    head = (f'Мы разобрали {pages} {page_word}, на них {remarks} {ann_word}')
    if method_pages >= pages * 0.8:
        return head + ' — и практически на каждой работает хотя бы один приём, а обычно два или три сразу.'
    return head + f' — и на {method_pages} из них работает хотя бы один приём, а обычно два или три сразу.'


def _count_published_remarks(doc_output_dir):
    """
    Count published remarks in a built document directory.

    Returns {'remarks': N, 'pages': M} — M is the number of pages that actually
    carry at least one remark. Drafts are excluded by _iter_published_pages.
    """
    result = {'remarks': 0, 'pages': 0}
    for _name, published in _iter_published_pages(doc_output_dir):
        result['remarks'] += len(published)
        result['pages'] += 1
    return result

def _plural_ru(n, one, few, many):
    """Russian plural: 1 замечание / 2 замечания / 5 замечаний."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return few
    return many


def create_index_page(target_dir=None, specific_folders=None):
    """
    Create the main index page with document selection menu

    Args:
        target_dir (str): Target directory for output
        specific_folders (list): List of specific folders to include in the index
    """
    print("\n=== Creating Index Page with Document Selection Menu ===")

    # Use target_dir if provided, otherwise use default redpen-publish
    if target_dir:
        output_dir = target_dir
    else:
        output_dir = os.path.join(project_root, 'redpen-publish')

    # Create the index.html file
    index_path = os.path.join(output_dir, 'index.html')

    auto_hdr = page_html.AUTO_HEADER

    # Get the list of document folders
    document_folders = get_document_folders(specific_folders)

    # Create document entries with titles from meta.json if available
    documents = []
    for doc_id in document_folders:
        # Default title if meta.json is not available
        title = doc_id
        icon_path = None
        meta_data = {}

        # Try to get title from meta.json
        meta_json_path = os.path.join(project_root, 'redpen-content', doc_id, 'meta.json')
        if os.path.exists(meta_json_path):
            try:
                with open(meta_json_path, 'r', encoding='utf-8') as f:
                    meta_data = json.load(f)
                    if 'title' in meta_data:
                        title = meta_data['title']
            except Exception as e:
                print(f"Warning: Could not read title from meta.json for {doc_id}: {e}")
                meta_data = {}

        # Find the first PNG image in the book's images directory
        images_dir = os.path.join(project_root, 'redpen-content', doc_id, 'images')
        if os.path.exists(images_dir):
            try:
                from PIL import Image
                png_files = sorted([f for f in os.listdir(images_dir) if f.lower().endswith('.png')])
                if png_files:
                    # Get the first PNG file
                    first_png = png_files[0]
                    source_image_path = os.path.join(images_dir, first_png)

                    # Create the target directory if it doesn't exist
                    doc_publish_dir = os.path.join(output_dir, doc_id)
                    os.makedirs(doc_publish_dir, exist_ok=True)

                    # Resize the image to 150px width and save as cover.png
                    target_image_path = os.path.join(doc_publish_dir, 'cover.png')
                    img = Image.open(source_image_path)

                    # Calculate new height to maintain aspect ratio
                    width_percent = (150 / float(img.size[0]))
                    new_height = int((float(img.size[1]) * float(width_percent)))

                    # Resize and save
                    img = img.resize((150, new_height), Image.LANCZOS)
                    img.save(target_image_path)

                    # Set the icon path relative to the document directory
                    icon_path = 'cover.png'
                    print(f"[+] Created cover image for {doc_id}: {target_image_path}")
            except Exception as e:
                print(f"Warning: Could not process image for {doc_id}: {e}")

        # Если пересобрать обложку не удалось (нет Pillow и т.п.), но она уже
        # лежит в целевом каталоге с прошлой сборки — используем её.
        if not icon_path and os.path.exists(os.path.join(output_dir, doc_id, 'cover.png')):
            icon_path = 'cover.png'

        documents.append({
            'id': doc_id,
            'title': title,
            'icon': icon_path,
            'subtitle': _document_subtitle(meta_data),
            'description': meta_data.get('description') or '',
            'stats': _count_published_remarks(os.path.join(output_dir, doc_id)),
        })

    # Разобранные книги — вперёд, «в работе» — в конец; внутри групп по названию.
    documents.sort(key=lambda d: (0 if (d.get('stats') or {}).get('remarks') else 1, d['title']))

    # Get current timestamp
    current_timestamp = datetime.datetime.now().strftime('%d.%m.%Y')
    books_modifier = ' books--single' if len(documents) == 1 else ''

    # Плотность приёмов для секции «Перед вами учебник пропаганды»: считается по
    # опубликованным (не черновым) замечаниям, поэтому формулировка усиливается
    # сама по мере публикации разбора.
    methods_density_sentence = _methods_density_sentence(
        _methods_density(os.path.join(output_dir, doc['id']) for doc in documents)
    )

    # Блог рендерим здесь же: страницы blog/ пишутся в тот же output_dir, а
    # титульной нужна последняя запись для секции-анонса.
    blog_posts = blog_builder.build_blog(
        output_dir,
        source_dir=os.path.join(project_root, blog_builder.BLOG_SOURCE_DIRNAME),
        timestamp=current_timestamp,
        auto_header=auto_hdr)
    blog_section_html = blog_builder.render_latest_section(blog_posts)

    from html import escape as _esc

    # Create the HTML content - header part
    html_content = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <title>Мединский.нет — антимифы к единому учебнику</title>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <meta name="description" content="Антимифы к единому учебнику истории: постраничный разбор с фактчеком, разбором манипуляций и умолчаний прямо поверх страниц оригинала."/>
  <link rel="canonical" href="https://medinsky.net/"/>
  <meta property="og:type" content="website"/>
  <meta property="og:site_name" content="Мединский.нет"/>
  <meta property="og:title" content="Мединский.нет — антимифы к единому учебнику"/>
  <meta property="og:description" content="Антимифы к единому учебнику истории: постраничный разбор с фактчеком, разбором манипуляций и умолчаний прямо поверх страниц оригинала."/>
  <meta property="og:url" content="https://medinsky.net/"/>
  <link rel="stylesheet" href="css/main.css">
  <link rel="stylesheet" href="css/landing.css">
  <link rel="stylesheet" href="css/blog.css">
  <link rel="icon" href="favicon.svg">
  <style>
    /* Каркас (.landing/.prose/.btn/footer) — в css/landing.css, он общий
       со страницами блога. Здесь только специфика титульной. */
    .hero { padding-top: 28px; }
    .hero__eyebrow {
      margin: 0;
      text-transform: uppercase;
      letter-spacing: 0.09em;
      font-size: 0.78rem;
      font-weight: 700;
      color: #DC143C;
    }
    /* Подпись сайта — только на титульной; шапку страниц держим однострочной. */
    .hero__subtitle {
      margin: -8px 0 16px;
      font-size: 1.25rem;
      font-weight: 600;
      line-height: 1.35;
      color: #8a3a4a;
    }
    .hero__lead {
      font-size: 1.1rem;
      margin: 0;
      color: #333;
    }

    /* Библиотека разборов */
    .books__grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
      gap: 18px;
    }
    /* Один учебник — не растягиваем карточку на всю ширину сетки. */
    .books--single .books__grid { max-width: 780px; margin: 0 auto; }
    .document-card {
      display: flex;
      gap: 18px;
      align-items: flex-start;
      background-color: #fff;
      border: 1px solid #e6e2dd;
      border-left: 5px solid #DC143C;
      border-radius: 6px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.06);
      padding: 20px;
    }
    .document-card--pending { border-left-color: #c9c4bd; }
    .document-card__cover { flex-shrink: 0; display: block; }
    .document-card__cover img {
      display: block;
      width: 104px;
      height: auto;
      border-radius: 3px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.18);
    }
    .document-card--pending .document-card__cover img { filter: grayscale(0.8); opacity: 0.75; }
    /* Карточки в ряду одной высоты, кнопка прижата к низу. */
    .document-card__body {
      min-width: 0;
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      flex: 1;
    }
    .document-card__body .btn { margin-top: auto; }
    .document-card h3 {
      margin: 0 0 6px;
      font-size: 1.1rem;
      line-height: 1.3;
    }
    .document-card__meta,
    .document-card__stats {
      margin: 0 0 6px;
      font-size: 0.88rem;
      color: #6b6b6b;
    }
    .document-card__stats { color: #DC143C; font-weight: 600; }
    .document-card--pending .document-card__stats { color: #8a8a8a; font-weight: 400; font-style: italic; }
    .document-card__desc { margin: 10px 0 16px; font-size: 0.95rem; }

    /* Легенда маркеров */
    .legend { list-style: none; margin: 0; padding: 0; }
    .legend li {
      position: relative;
      padding-left: 34px;
      margin-bottom: 14px;
    }
    .legend .dot {
      position: absolute;
      left: 0;
      top: 3px;
      width: 18px;
      height: 18px;
      border-radius: 50%;
      border: 3px solid #DC143C;
    }
    /* Модификаторы намеренно НЕ вложены в .legend: те же классы используются
       в списке приёмов выше. Заливка вместо обводки — .dot--cat. */
    .legend .dot--cat { border: none; }
    .dot--omission  { background: var(--cat-omission); }
    .dot--language  { background: var(--cat-language); }
    .dot--sides     { background: var(--cat-sides); }
    .dot--evidence  { background: var(--cat-evidence); }
    .dot--apparatus { background: var(--cat-apparatus); }
    .dot--today     { background: var(--cat-today); }
    .dot--other     { background: var(--cat-other); }

    /* Секция «Перед вами учебник пропаганды» */
    .methods__lead { font-size: 1.05rem; }
    .methods__list { list-style: none; margin: 24px 0 0; padding: 0; }
    .methods__list li {
      position: relative;
      padding-left: 34px;
      margin-bottom: 18px;
    }
    .methods__list .dot {
      position: absolute;
      left: 0;
      top: 4px;
      width: 18px;
      height: 18px;
      border-radius: 50%;
      border: none;
    }
    .methods__list strong { display: block; margin-bottom: 2px; }
    .methods__other {
      margin-top: 26px;
      padding-top: 18px;
      border-top: 1px solid #e6e2dd;
      color: #4a4a4a;
    }
    .rules { margin: 0; padding-left: 22px; }
    .rules li { margin-bottom: 12px; }
    .note {
      background: #fff;
      border: 1px solid #e6e2dd;
      border-radius: 6px;
      padding: 16px 20px;
    }
    .note p { margin: 0; }
    @media (max-width: 560px) {
      .document-card { flex-direction: column; gap: 16px; }
      .document-card__cover img { width: 110px; }
    }
  </style>
</head>
<body>"""

    # Add header with dynamic timestamp
    html_content += f"""
  <header>Мединский.нет <span id="timestamp" style="font-size: 0.7rem; font-weight: normal; opacity: 0.8;">Последнее обновление: {current_timestamp}</span></header>

  <main class="landing">
    <section class="hero prose">
      <p class="hero__eyebrow">Постраничный разбор учебников</p>
      <h1>Мединский.нет</h1>
      <p class="hero__subtitle">Антимифы к единому учебнику. Проверяем избыточные победы.</p>
      <p class="hero__lead">Мы читаем школьные учебники истории страницу за страницей и выносим на поля то, что написал бы внимательный преподаватель: где факт передёрнут, где оценка выдана за установленный факт, а где о важном просто умолчали. Замечания стоят прямо на развороте — у того места, к которому относятся.</p>
    </section>

    <section class="methods prose">
      <h2>Перед вами учебник пропаганды</h2>
      <p class="methods__lead">Это не фигура речи и не оценка «в целом». Мы прочли книгу страницу за страницей и обнаружили, что она собрана из небольшого набора повторяющихся приёмов. Их шесть. {methods_density_sentence}</p>
      <ul class="methods__list">
        <li><span class="dot dot--omission"></span><strong>Умолчание</strong>Факт не искажён — он вынут. Волго-Донской канал «построен в кратчайшие сроки», и из фразы выпало, кто его строил. У балерины перечислены звания — и не сказано, что её отца расстреляли. Слова «антисемитизм» нет во всей книге ни разу.</li>
        <li><span class="dot dot--language"></span><strong>Обтекаемый язык</strong>Факт на месте, но упакован так, чтобы не сработать. «Проводилась проверка» — это фильтрационные лагеря НКВД. «По идеологическим причинам был закрыт ряд направлений» — кем закрыт, не спрашивайте. А там, где учебник впервые за 167 страниц произносит слово «цензура», он тут же гасит его оборотом «несмотря на».</li>
        <li><span class="dot dot--sides"></span><strong>Двойной стандарт</strong>Одно и то же действие называется по-разному в зависимости от того, чьё оно. Один лидер — «ставленник», другой — «поддержанный СССР». У одной державы «забота о поясе дружественных государств», у другой «полный контроль». Причина собственных неудач при этом почти всегда находится снаружи.</li>
        <li><span class="dot dot--evidence"></span><strong>Нечем подтвердить</strong>Утверждение есть, опоры под ним нет. «Признан одним из семи чудес России» — по итогам SMS-голосования. «Считается лучшим вратарём в истории» — считается кем? Начало холодной войны проиллюстрировано тремя советскими карикатурами подряд, и школьника просят пересказать плакат.</li>
        <li><span class="dot dot--apparatus"></span><strong>Ответ подсказан заранее</strong>Учебник не только рассказывает — он тренирует. Главный вопрос параграфа содержит готовый ответ, задание просит «привести два аргумента, которыми можно подтвердить данную точку зрения», а рубрика «Подведём итоги» бодро сообщает обратное тому, что было в тексте двумя страницами раньше.</li>
        <li><span class="dot dot--today"></span><strong>История под сегодняшнюю политику</strong>Рассказ о 1945 годе обрывается претензией к нынешним властям Польши и Чехии. Параграф о 1954-м сообщает, что присоединение Крыма в 2014 году «восстановило историческую справедливость». Советскую мораль предлагается мерить цитатой действующего президента.</li>
      </ul>
      <p class="methods__other">Седьмой цвет, серый, — не приём. Им помечено то, что мы просто добавляем от себя: недостающий контекст или исправление прямой ошибки. Таких замечаний примерно каждое восьмое, и мы намеренно не смешиваем их с остальными — чтобы счёт был честным.</p>
      <p>Каждое замечание привязано к своему месту на странице и по возможности снабжено ссылкой на источник. Проверяйте.</p>
    </section>
{blog_section_html}
    <section class="books{books_modifier}">
      <h2>Учебники</h2>
      <div class="books__grid">
"""

    # Add document cards
    for doc in documents:
        stats = doc.get('stats') or {}
        has_remarks = bool(stats.get('remarks'))

        # Add cover if available
        cover_html = ""
        if doc.get('icon'):
            cover_html = f"""
          <a class="document-card__cover" href="{doc['id']}/index.html">
            <img src="{doc['id']}/{doc['icon']}" alt="Обложка: {_esc(doc['title'], quote=True)}" width="104">
          </a>"""

        subtitle_html = ""
        if doc.get('subtitle'):
            subtitle_html = f"""
            <p class="document-card__meta">{_esc(doc['subtitle'])}</p>"""

        description_html = ""
        if doc.get('description'):
            description_html = f"""
            <p class="document-card__desc">{_esc(doc['description'])}</p>"""

        if has_remarks:
            ann_n = stats['remarks']
            pages_n = stats['pages']
            ann_word = _plural_ru(ann_n, 'замечание', 'замечания', 'замечаний')
            page_word = _plural_ru(pages_n, 'странице', 'страницах', 'страницах')
            stats_html = f"""
            <p class="document-card__stats">{ann_n} {ann_word} на {pages_n} {page_word}</p>"""
            button_html = f"""<a class="btn" href="{doc['id']}/index.html">Открыть разбор</a>"""
            card_class = "document-card"
        else:
            # Книга уже опубликована постранично, но разбора ещё нет — честно
            # говорим об этом и всё равно даём открыть сам учебник.
            stats_html = """
            <p class="document-card__stats">Разбор ещё готовится</p>"""
            button_html = f"""<a class="btn btn--ghost" href="{doc['id']}/index.html">Смотреть страницы</a>"""
            card_class = "document-card document-card--pending"

        html_content += f"""        <article class="{card_class}">{cover_html}
          <div class="document-card__body">
            <h3>{_esc(doc['title'])}</h3>{subtitle_html}{stats_html}{description_html}
            {button_html}
          </div>
        </article>
"""

    html_content += """      </div>
"""

    # Close HTML tags with optional editor flag propagation script
    html_content += f"""    </section>

    <section class="how prose">
      <h2>Как читать</h2>
      <p>Цвет кружка на странице — это приём, а не важность замечания. Тот же цвет стоит у номера в списке под сканом.</p>
      <ul class="legend">
        <li><span class="dot dot--cat dot--omission"></span><strong>Синий</strong> — умолчание: о чём учебник не сказал.</li>
        <li><span class="dot dot--cat dot--language"></span><strong>Бирюзовый</strong> — обтекаемый язык: названо так, чтобы не заметили.</li>
        <li><span class="dot dot--cat dot--sides"></span><strong>Янтарный</strong> — двойной стандарт: своим одно, чужим другое.</li>
        <li><span class="dot dot--cat dot--evidence"></span><strong>Фиолетовый</strong> — нечем подтвердить: ни источника, ни автора оценки.</li>
        <li><span class="dot dot--cat dot--apparatus"></span><strong>Малиновый</strong> — ответ подсказан заранее: вывод выдан вместе с заданием.</li>
        <li><span class="dot dot--cat dot--today"></span><strong>Бордовый</strong> — история под сегодняшнюю политику.</li>
        <li><span class="dot dot--cat dot--other"></span><strong>Серый</strong> — не приём: недостающий контекст или исправление ошибки.</li>
      </ul>
      <p>Размер кружка по-прежнему показывает вес замечания: крупный — разбор фрагмента, мелкий — уточнение к детали.</p>
      <p>Наведите курсор на кружок — разбор появится прямо поверх страницы; щелчок закрепит его. Под сканом те же замечания идут списком, по порядку, — так разбор страницы можно прочитать подряд.</p>
      <p>У каждой страницы учебника свой адрес вида <code>/medinsky11klass/pages/17/</code> — такую ссылку удобно давать в споре, чтобы собеседник открыл ровно тот же разворот. Листать можно ссылками «вперёд» и «назад» внизу страницы или из оглавления учебника.</p>
    </section>

    <section class="rules-section prose">
      <h2>Правила разбора</h2>
      <ol class="rules">
        <li><strong>Привязка к месту.</strong> Замечание относится к конкретному абзацу, а не к учебнику вообще: рядом всегда видно, что именно разбирается.</li>
        <li><strong>Проверяемость.</strong> Утверждение без ссылки на источник — не разбор, а мнение. Ссылки стоят в тексте самих замечаний.</li>
        <li><strong>Факт отдельно, оценка отдельно.</strong> Мы отмечаем, где учебник подаёт оценку как установленный факт, и стараемся не делать того же сами.</li>
        <li><strong>Умолчание — тоже приём.</strong> Прежде чем написать «в учебнике об этом не сказано», мы ищем по всему тексту книги.</li>
      </ol>
    </section>

    <section class="offline prose">
      <h2>Этот сайт можно унести с собой</h2>
      <div class="note">
        <p>Чтобы читать, не нужно ни регистрироваться, ни входить: здесь нет ни счётчиков, ни аналитики, ни рекламы — мы не следим за тем, кто какую страницу открыл. Страницы, текст и замечания — обычные файлы, лежащие рядом: браузер скачивает их ровно так же, как картинки на любом другом сайте, и ничего больше никуда не отправляет. К нашему API — тому, через который разбор редактируется, — просмотрщик не обращается вовсе.</p>
        <p>Поэтому сайт не привязан к нашему серверу: он одинаково работает из любой папки. Архив с полной копией разбора, который можно положить на флешку и читать без интернета, мы сейчас готовим.</p>
      </div>
    </section>

    <footer class="prose">Последнее обновление: {current_timestamp}</footer>
  </main>
"""

    html_content += """  <script>
  (function(){
    var g = window;
    function hasEditorFlag(){
      try {
        var usp = new URLSearchParams(g.location.search || '');
        var hsp = new URLSearchParams((g.location.hash || '').replace(/^#/, ''));
        var qp = usp.get('editor');
        var hp = hsp.get('editor');
        return (qp === '1' || qp === 'true') || (hp === '1' || hp === 'true') || g.REDPEN_EDITOR === true;
      } catch(e) { return g.REDPEN_EDITOR === true; }
    }
    if (!hasEditorFlag()) return;
    // Propagate editor=1 to document links on the selector page
    var links = document.querySelectorAll('.document-card a[href]');
    links.forEach(function(a){
      try {
        var url = new URL(a.getAttribute('href'), g.location.href);
        if (!url.searchParams.get('editor')) {
          url.searchParams.set('editor','1');
        }
        a.setAttribute('href', url.pathname + url.search + url.hash);
      } catch(e) {}
    });
  })();
  </script>
</body>
</html>
"""

    # Write the HTML content to the files
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(auto_hdr + html_content)

    print(f"[+] Created index page at {index_path}")

    # Clean up old structure
    print("\n=== Cleaning Up Old Structure ===")
    old_dirs = ['remarks', 'images', 'text']
    for old_dir in old_dirs:
        old_path = os.path.join(output_dir, old_dir)
        if os.path.exists(old_path) and os.path.isdir(old_path):
            try:
                shutil.rmtree(old_path)
                print(f"[+] Removed old directory: {old_path}")
            except Exception as e:
                print(f"[!] Error removing directory {old_path}: {e}")

    return True

def push_to_submodule(target_dir=None):
    """Commit and push changes to the redpen-publish repository"""
    # Only push if target_dir is None or is the default redpen-publish directory
    default_publish_dir = os.path.join(project_root, 'redpen-publish')

    if target_dir and os.path.abspath(target_dir) != os.path.abspath(default_publish_dir):
        print("\n=== Skipping push to redpen-publish (custom target directory used) ===")
        return True

    print("\n=== Pushing changes to redpen-publish repository ===")

    submodule_path = os.path.join(project_root, 'redpen-publish')

    # Check if there are changes to commit
    success, stdout, stderr = run_command("git status --porcelain", cwd=submodule_path)
    if not success:
        print("Failed to check git status")
        return False

    if not stdout.strip():
        print("No changes to commit in redpen-publish")
        return True

    # Add all changes
    success, stdout, stderr = run_command("git add .", cwd=submodule_path)
    if not success:
        print("Failed to add changes")
        return False

    # Commit changes
    success, stdout, stderr = run_command(
        'git commit -m "Update website content via build script"', 
        cwd=submodule_path
    )
    if not success:
        print("Failed to commit changes")
        return False

    # Push changes
    success, stdout, stderr = run_command("git push", cwd=submodule_path)
    if not success:
        print("Failed to push changes")
        return False

    print("Successfully pushed changes to redpen-publish")
    return True

def main():
    """Main function to build and publish the website"""
    parser = argparse.ArgumentParser(description="Build and publish the website")
    # Флаг ничего не выключает с 2026-08-30: браузерные проверки, которые
    # сборка гоняла (позиции маркеров и режим редактора), целились в старый SPA
    # и удалены вместе с ним. Аргумент принимается, чтобы не ломать команды из
    # доков и журналов, где он написан в каждой второй строке.
    parser.add_argument("--skip-tests", action="store_true",
                        help="Принимается для совместимости; сборка браузерных тестов больше не гоняет")
    parser.add_argument("--skip-push", action="store_true", help="Skip pushing changes to the redpen-publish repository")
    parser.add_argument("--target-dir", help="Specify a target directory for the build output (default: redpen-publish)")
    parser.add_argument("--document", help="Specify a document to build (default: build all documents)")
    parser.add_argument("--folders", help="Comma-separated list of specific folders to deploy (default: all folders)")
    parser.add_argument("--backup-publish", action="store_true", help="Backup current redpen-publish contents (excluding .git) before build")
    parser.add_argument("--clean-publish", action="store_true", help="Clean redpen-publish (remove all except .git) before build")
    parser.add_argument("--compare-paths", action="store_true", help="After build, compare file path sets before vs after (requires --backup-publish)")
    parser.add_argument(
        "--remarks-from-md",
        action="store_true",
        help=(
            "Convert redpen-content/<doc>/remarks/*.md to JSON (legacy). "
            "Off by default since stage 2: remarks/*.json in redpen-publish "
            "are exported from the SQLite DB (scripts/api/export_remarks.py); "
            "md is archive-only and converting it here would overwrite fresher data."
        ),
    )

    args = parser.parse_args()

    # Use the specified target directory or default to redpen-publish
    target_dir = args.target_dir
    if target_dir:
        # Create absolute path if relative path is provided
        if not os.path.isabs(target_dir):
            target_dir = os.path.abspath(os.path.join(os.getcwd(), target_dir))
        # Create the directory if it doesn't exist
        os.makedirs(target_dir, exist_ok=True)
        print(f"Using target directory: {target_dir}")

    # Resolve publish dir path
    publish_dir = target_dir if target_dir else os.path.join(project_root, 'redpen-publish')

    # Optional: backup and clean before building
    before_paths = []
    if args.backup_publish:
        before_paths = snapshot_paths(publish_dir)
        backup_dir = backup_publish_dir(publish_dir)
        print(f"Backup created at: {backup_dir}")
    if args.clean_publish:
        clean_publish_dir(publish_dir)

    # Process specific folders if provided
    specific_folders = None
    if args.folders:
        specific_folders = [folder.strip() for folder in args.folders.split(',')]
        print(f"Building specific folders: {', '.join(specific_folders)}")

    # Use the specified document or build all documents
    document = args.document
    if document:
        print(f"Building document: {document}")
        # If both --document and --folders are specified, --document takes precedence
        specific_folders = None

    # Step 1: Convert markdown remarks to JSON (legacy/archive path -- see
    # --remarks-from-md help text above). Skipped by default so a routine
    # build never overwrites remarks/*.json exported from the DB.
    if args.remarks_from_md:
        if not convert_remarks(target_dir, document, specific_folders):
            print("Failed to convert remarks. Aborting.")
            sys.exit(1)
    else:
        print("Skipping markdown->JSON remark conversion (pass --remarks-from-md to force; md is archive-only)")

    # Step 3: Publish data
    if not publish_website_data(target_dir, document, specific_folders):
        print("Failed to publish website data. Aborting.")
        sys.exit(1)

    # Step 3.5: Generate the page-addressing manifest (stage 2 / B.2). No-op
    # for documents without a pageNumbering.printedStartFile section.
    if not generate_page_manifests(target_dir, document, specific_folders):
        print("Failed to generate page manifest. Aborting.")
        sys.exit(1)

    # Step 3.6: Rebuild chapters from the table of contents and render the
    # per-page HTML that search engines actually index.
    if not generate_page_html(target_dir, document, specific_folders):
        print("Failed to generate per-page HTML. Aborting.")
        sys.exit(1)

    # Step 4: Create index page with document selection menu
    if not document:
        # Only create the index page when building all documents
        create_index_page(target_dir, specific_folders)

        # Step 4.5: sitemap.xml + robots.txt. Last, so the landing page, the
        # blog and every per-page file already exist -- the sitemap is built by
        # scanning the output and skipping anything marked noindex.
        sitemap.generate(publish_dir, os.getenv('REDPEN_SITE_URL', 'https://medinsky.net'))

    # Step 6: Push changes to redpen-publish repository (if not skipped)
    if not args.skip_push:
        if not push_to_submodule(target_dir):
            print("Failed to push changes to redpen-publish repository. Aborting.")
            sys.exit(1)
    else:
        print("Skipping push to redpen-publish repository")

    # Optional: compare file path sets after build
    if args.backup_publish and args.compare_paths:
        after_paths = snapshot_paths(publish_dir)
        ok = compare_path_sets(before_paths, after_paths)
        if not ok:
            print("[!] Some files present before rebuild are missing after rebuild.")
        else:
            print("[+] Path comparison OK: no files lost.")

    print("\n=== Website Build Completed Successfully ===")

if __name__ == "__main__":
    main()
