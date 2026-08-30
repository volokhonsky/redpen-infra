#!/usr/bin/env python3
"""Геометрия маркеров у читателя — браузерная проверка без бейзлайна.

Заменяет прежние remark_position_tests.py: те строили синтетический
document_index.html с зашитыми в него кружками и проверяли, по сути, окружение,
а не код проекта. SPA удалён 2026-08-30, и вместе с ним ушла та проверка.

Здесь проверяется настоящий путь читателя: страница из сборки, замечания из
инлайнового redpen-page-data, кружки рисует page-view.js через общий
redpen-markers.js. Ожидание выводится из данных, а не из сохранённого снимка:
центр маркера обязан совпасть с coords, умноженными на масштаб картинки, —
поэтому проверка не устаревает от правки шапки или полей страницы.

    python3 scripts/build_website.py --skip-push --target-dir ./out
    # (в ./out нужны remarks/: скопируйте их из redpen-publish до сборки)
    python3 tests/manual/page_view_markers.py ./out [<docId>]

Заодно ловит нарушение офлайн-инварианта: любой запрос страницы наружу.
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')))
from tests._http_helpers import find_free_port, start_http_server  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

TOLERANCE = 1.5  # px: округление раскладки, не более


def page_labels(root, doc_id, limit=6):
    """Метки страниц, на которых есть опубликованные замечания."""
    meta = json.load(open(os.path.join(root, doc_id, 'metadata.json'), encoding='utf-8'))
    by_file = {p['file']: str(p['label']) for p in meta.get('pages', [])}
    out = []
    ann_dir = os.path.join(root, doc_id, 'remarks')
    for name in sorted(os.listdir(ann_dir)):
        if not name.endswith('.json') or name.endswith('.drafts.json'):
            continue
        data = json.load(open(os.path.join(ann_dir, name), encoding='utf-8'))
        published = [a for a in data if isinstance(a, dict) and not a.get('draft')
                     and isinstance(a.get('coords'), list)]
        if len(published) >= 2:
            label = by_file.get(name[:-5])
            if label:
                out.append(label)
        if len(out) >= limit:
            break
    return out


def main(argv):
    root = os.path.abspath(argv[1]) if len(argv) > 1 else 'out'
    doc_id = argv[2] if len(argv) > 2 else 'medinsky11klass'
    labels = page_labels(root, doc_id)
    if not labels:
        print('[!] в сборке нет страниц с двумя опубликованными замечаниями')
        return 1

    cwd = os.getcwd()
    port = find_free_port()
    srv = start_http_server(root, port)
    failures = []
    checked = 0
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for width in (1280, 800):
                page = browser.new_page(viewport={'width': width, 'height': 1000})
                outside = []
                page.on('request', lambda r: outside.append(r.url)
                        if not r.url.startswith(('http://localhost:%d' % port, 'data:', 'blob:')) else None)
                for label in labels:
                    page.goto('http://localhost:%d/%s/pages/%s/index.html' % (port, doc_id, label),
                              wait_until='networkidle')
                    page.wait_for_timeout(300)
                    result = page.evaluate('''() => {
                      const img = document.getElementById('page-image');
                      if (!img || !img.naturalWidth) return {error: 'нет картинки'};
                      const data = JSON.parse(
                        document.getElementById('redpen-page-data').textContent);
                      const shown = data.filter(a => !a.draft && Array.isArray(a.coords));
                      const sx = img.width / img.naturalWidth;
                      const sy = img.height / img.naturalHeight;
                      const imgRect = img.getBoundingClientRect();
                      return {expected: shown.length, scale: [sx, sy],
                        circles: Array.from(document.querySelectorAll('.circle')).map(c => {
                          const b = c.getBoundingClientRect();
                          const ann = shown.find(a => 'circle-' + a.id === c.id);
                          return {id: c.id, found: !!ann,
                                  gotX: b.left + b.width/2 - imgRect.left,
                                  gotY: b.top + b.height/2 - imgRect.top,
                                  wantX: ann ? ann.coords[0] * sx : null,
                                  wantY: ann ? ann.coords[1] * sy : null};
                        })};
                    }''')
                    where = '%dpx стр. %s' % (width, label)
                    if result.get('error'):
                        failures.append('%s: %s' % (where, result['error']))
                        continue
                    if len(result['circles']) != result['expected']:
                        failures.append('%s: маркеров %d, замечаний %d'
                                        % (where, len(result['circles']), result['expected']))
                    for c in result['circles']:
                        checked += 1
                        if not c['found']:
                            failures.append('%s: маркер %s без замечания' % (where, c['id']))
                            continue
                        dx, dy = abs(c['gotX'] - c['wantX']), abs(c['gotY'] - c['wantY'])
                        if dx > TOLERANCE or dy > TOLERANCE:
                            failures.append('%s: %s смещён на (%.1f, %.1f)px'
                                            % (where, c['id'], dx, dy))
                if outside:
                    failures.append('%dpx: запросы наружу: %s' % (width, sorted(set(outside))[:3]))
                page.close()
            browser.close()
    finally:
        srv.shutdown()
        os.chdir(cwd)

    print('Проверено маркеров: %d на %d страницах в двух ширинах' % (checked, len(labels)))
    for f in failures:
        print('FAIL', f)
    print('ИТОГ:', 'PASS' if not failures else 'FAIL (%d)' % len(failures))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
