"""Сквозная приёмка экрана страницы в редакторе /app/.

Проверяет ровно то, что до 2026-08-30 умел только старый SPA: создание
замечания кликом по скану, перенос маркера перетаскиванием, оптимистическую
блокировку по serverPageSha — и что результат доезжает до читателя (JSON
страницы и инлайновый redpen-page-data в HTML).

    python3 tests/manual/editor_app_stand.py <каталог-сборки> &   # ждать READY
    python3 tests/manual/editor_app_acceptance.py

Требует playwright с chromium. Печатает PASS/FAIL построчно и код возврата.
Стенд после прогона содержит созданное замечание — для повторного прогона его
надо перезапустить (он поднимает базу с нуля).
"""
import json, os, sys
from playwright.sync_api import sync_playwright

SP = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(SP, 'stand.json')))
BASE = 'http://127.0.0.1:%d' % cfg['static']
DOC, PAGE = 'medinsky11klass', '065'
ok = []
fail = []

def check(name, cond, extra=''):
    (ok if cond else fail).append(name + (' — ' + str(extra) if extra else ''))
    print(('PASS ' if cond else 'FAIL ') + name + (' | ' + str(extra) if extra else ''))

PAGE_REF = {}

def api(path):
    """Читаем от имени вошедшего редактора: анонимному API черновиков не отдаёт."""
    return PAGE_REF['page'].evaluate(
        "async (p) => (await fetch(p, {credentials:'include'})).json()", path)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={'width': 1500, 'height': 1000})
    page = ctx.new_page()
    PAGE_REF['page'] = page
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))

    # вход агентским токеном (Google в стенде нет)
    page.goto(BASE + '/app/?api=' + str(cfg['static']))
    page.evaluate("""async () => {
      await fetch('/api/auth/login', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({token:'stand-token'}), credentials:'include'});
    }""")

    page.goto(BASE + '/app/?api=' + str(cfg['static']) + '#/page/%s/%s' % (DOC, PAGE))
    page.wait_for_selector('#view-page:not([hidden])', timeout=10000)
    page.wait_for_timeout(1200)

    before = len(page.query_selector_all('#pg-overlay .circle'))
    check('маркеры отрисованы', before >= 1, 'кружков: %d' % before)
    check('картинка страницы загрузилась',
          page.evaluate("() => { const i=document.getElementById('pg-image'); return !!(i && i.naturalWidth); }"))

    # --- создание кликом по скану ---
    page.click('#pg-new')
    check('режим выбора точки включён',
          page.evaluate("() => document.getElementById('pg-scan').classList.contains('is-placing')"))
    box = page.query_selector('#pg-image').bounding_box()
    page.mouse.click(box['x'] + box['width'] * 0.4, box['y'] + box['height'] * 0.3)
    page.wait_for_timeout(300)
    check('форма нового замечания раскрылась',
          page.evaluate("() => !document.getElementById('pg-form').hidden"))
    coords_text = page.inner_text('#pg-coords')
    check('координаты показаны', 'Координаты' in coords_text, coords_text)
    ghost = page.evaluate("() => !!document.getElementById('circle-__new__')")
    check('призрачный маркер показан на выбранной точке', ghost)

    page.fill('#pg-text', 'Приёмочное замечание, создано кликом по скану.')
    page.select_option('#pg-category', 'omission')
    page.select_option('#pg-kind', 'major')
    page.click('#pg-create')
    page.wait_for_timeout(2500)

    data = api('/api/editor/%s/%s' % (DOC, PAGE))
    created = [a for a in data['remarks'] if 'Приёмочное замечание' in (a.get('text') or '')]
    check('замечание создано через POST', len(created) == 1, 'найдено: %d' % len(created))
    if not created:
        b.close(); sys.exit(1)
    new_ann = created[0]
    check('координаты сохранены', isinstance(new_ann.get('coords'), list) and len(new_ann['coords']) == 2,
          new_ann.get('coords'))
    check('вид major сохранён', new_ann.get('kind') == 'major', new_ann.get('kind'))
    check('категория omission сохранена', new_ann.get('category') == 'omission', new_ann.get('category'))
    check('новое замечание — черновик', new_ann.get('draft') is True, new_ann.get('draft'))
    check('после создания открылась карточка', '/ann/' in page.url, page.url)

    # --- перенос маркера ---
    page.goto(BASE + '/app/?api=' + str(cfg['static']) + '#/page/%s/%s' % (DOC, PAGE))
    page.wait_for_selector('#view-page:not([hidden])')
    page.wait_for_timeout(1200)
    sel = '#circle-' + new_ann['id'].replace('.', '\\.')
    handle = page.query_selector(sel)
    check('маркер нового замечания на скане', handle is not None, sel)
    if handle:
        mb = handle.bounding_box()
        page.mouse.move(mb['x'] + mb['width']/2, mb['y'] + mb['height']/2)
        page.mouse.down()
        page.mouse.move(mb['x'] + mb['width']/2 + 120, mb['y'] + mb['height']/2 + 90, steps=10)
        page.mouse.up()
        page.wait_for_timeout(2500)
        after = api('/api/editor/%s/%s' % (DOC, PAGE))
        moved = [a for a in after['remarks'] if a['id'] == new_ann['id']][0]
        check('координаты изменились перетаскиванием',
              moved['coords'] != new_ann['coords'], '%s -> %s' % (new_ann['coords'], moved['coords']))
        check('текст при переносе не пострадал', moved['text'] == new_ann['text'])
        check('вид при переносе не пострадал', moved.get('kind') == 'major', moved.get('kind'))
        check('категория при переносе не пострадала', moved.get('category') == 'omission')
        check('черновик остался черновиком', moved.get('draft') is True)

        # ревизия «перенос маркера» в журнале
        tl = api('/api/remarks/%s/%s/%s/timeline?limit=50' % (DOC, PAGE, new_ann['id']))
        acts = [i.get('actions') for i in tl.get('items', [])]
        flat = [a for sub in acts if sub for a in sub]
        check('в журнале есть перенос маркера', 'coords' in flat, flat)
        check('в журнале есть создание', 'create' in flat, flat)

    # --- оптимистическая блокировка: 409 ---
    sha = api('/api/editor/%s/%s' % (DOC, PAGE))['serverPageSha']
    conflict = page.evaluate("""async ([doc, pageKey, id, staleSha]) => {
      const csrf = await (await fetch('/api/auth/csrf', {credentials:'include'})).json();
      const res = await fetch('/api/editor/'+doc+'/'+pageKey+'/'+id, {
        method:'PUT', credentials:'include',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrf.csrfToken},
        body: JSON.stringify({kind:'minor', text:'конфликтная правка', status:'draft',
                              category:'other', coords:[100,100], clientPageSha: staleSha})});
      return res.status;
    }""", [DOC, PAGE, new_ann['id'], 'sha-которого-нет'])
    check('устаревший clientPageSha даёт 409', conflict == 409, conflict)

    # свежий sha проходит
    fresh = page.evaluate("""async ([doc, pageKey, id, sha]) => {
      const csrf = await (await fetch('/api/auth/csrf', {credentials:'include'})).json();
      const res = await fetch('/api/editor/'+doc+'/'+pageKey+'/'+id, {
        method:'PUT', credentials:'include',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrf.csrfToken},
        body: JSON.stringify({kind:'major', text:'Приёмочное замечание, создано кликом по скану.',
                              status:'published', category:'omission', coords:[300,300],
                              clientPageSha: sha})});
      return res.status;
    }""", [DOC, PAGE, new_ann['id'], sha])
    check('свежий clientPageSha принимается', fresh == 200, fresh)

    check('ошибок в консоли нет', not errors, errors)
    b.close()

# --- публикация доехала до статики ---
pub = cfg['public']
jpath = os.path.join(pub, DOC, 'remarks', 'page_%s.json' % PAGE)
items = json.load(open(jpath))
found = [a for a in items if a.get('id') == new_ann['id']]
check('замечание попало в remarks/page_%s.json' % PAGE, len(found) == 1)
if found:
    check('в статике оно опубликовано (не черновик)', not found[0].get('draft'), found[0].get('draft'))
    check('координаты в статике те, что сохранили', found[0].get('coords') == [300, 300], found[0].get('coords'))

label = None
meta = json.load(open(os.path.join(pub, DOC, 'metadata.json')))
for pg in meta.get('pages', []):
    if pg['file'] == 'page_' + PAGE:
        label = str(pg['label'])
html_path = os.path.join(pub, DOC, 'pages', label or PAGE, 'index.html')
html = open(html_path, encoding='utf-8').read()
check('замечание доехало до инлайнового redpen-page-data',
      new_ann['id'] in html and 'Приёмочное замечание' in html, html_path)

print('\n=== ИТОГ: %d пройдено, %d провалено ===' % (len(ok), len(fail)))
for f in fail: print('  FAIL', f)
sys.exit(1 if fail else 0)
