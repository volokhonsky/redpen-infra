"""Локальный стенд редактора: API на временной БД + статика на одном источнике.

Зачем: приёмка экрана страницы в /app/ (создание замечания кликом по скану,
перенос маркера, оптимистическая блокировка) требует живого API, живой статики
и одного источника для куки сессии — как на проде за Caddy. Docker для этого
избыточен, а разные источники ломают куки и CSRF.

    python3 tests/manual/editor_app_stand.py <каталог-сборки> [<каталог-стенда>]

<каталог-сборки> — результат `scripts/build_website.py --target-dir ...` с
картинками книги. Стенд печатает READY и порты, кладёт их в stand.json рядом с
собой и держится, пока его не убьют. Замечания берутся из redpen-publish:
черновики при импорте отваливаются (тег `draft` зарезервирован) — это ожидаемо,
для приёмки хватает опубликованных.

Ничего боевого стенд не трогает: своя временная БД, свой каталог публикации.
"""
import json, os, shutil, sys, threading, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
SP = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, 'out')
STAND = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(SP, 'stand')
shutil.rmtree(STAND, ignore_errors=True)
PUBLIC = os.path.join(STAND, 'public')
os.makedirs(PUBLIC, exist_ok=True)

# Статика: берём готовую сборку (там есть картинки, /app/ и /cabinet/)
for name in os.listdir(BUILD):
    src = os.path.join(BUILD, name)
    dst = os.path.join(PUBLIC, name)
    (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)

os.environ['DB_PATH'] = os.path.join(STAND, 'redpen.db')
os.environ['STORAGE_DIR'] = os.path.join(STAND, 'data')
os.environ['LOG_DIR'] = os.path.join(STAND, 'logs')
os.environ['PUBLISH_DIR'] = PUBLIC
os.environ['COOKIE_SECURE'] = 'false'
os.environ['IDENTITY_PEPPER'] = 'stand-pepper'
os.environ['AGENT_TOKENS'] = 'stand-token:stand-agent'
os.environ['RATE_LIMIT_PER_MINUTE'] = '0'
os.environ['RATE_LIMIT_AUTH_PER_MINUTE'] = '0'
os.environ['CORS_ALLOW_ORIGINS'] = '*'

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'api'))
import db, main, import_remarks  # noqa
db.init_db()

# Наполняем базу теми же замечаниями, что лежат в сборке
src_remarks = os.path.join(ROOT, 'redpen-publish')
stats = import_remarks.run(src_remarks, doc_id='medinsky11klass',
                           overwrite=False, dry_run=False)
print('import:', stats)

import uvicorn
from tests._http_helpers import find_free_port
api_port = find_free_port()
cfg = uvicorn.Config(main.app, host='127.0.0.1', port=api_port, log_level='warning')
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    if server.started: break
    time.sleep(0.1)

import http.server, socketserver, functools, urllib.request, urllib.error

class Handler(http.server.SimpleHTTPRequestHandler):
    """Статика + прокси /api на бэкенд, чтобы всё жило на одном источнике:
    так куки сессии и CSRF ведут себя как на проде за Caddy."""

    def _proxy(self, method):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(
            'http://127.0.0.1:%d%s' % (api_port, self.path), data=body, method=method)
        for h in ('Content-Type', 'Cookie', 'X-CSRF-Token', 'Accept'):
            if self.headers.get(h):
                req.add_header(h, self.headers[h])
        try:
            resp = urllib.request.urlopen(req)
            status, payload, headers = resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            status, payload, headers = e.code, e.read(), e.headers
        self.send_response(status)
        for k, v in headers.items():
            if k.lower() in ('content-type', 'set-cookie'):
                self.send_header(k, v)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.startswith('/api/'): return self._proxy('GET')
        return super().do_GET()

    def do_POST(self): return self._proxy('POST')
    def do_PUT(self): return self._proxy('PUT')
    def do_PATCH(self): return self._proxy('PATCH')
    def do_DELETE(self): return self._proxy('DELETE')

static_port = find_free_port()
handler = functools.partial(Handler, directory=PUBLIC)
socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.ThreadingTCPServer(('127.0.0.1', static_port), handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()

# Свежие шаблоны поверх сборки: приёмка гоняет то, что в рабочей копии.
for sub in ('app', 'cabinet'):
    for name in os.listdir(os.path.join(ROOT, 'templates', sub)):
        shutil.copy2(os.path.join(ROOT, 'templates', sub, name),
                     os.path.join(PUBLIC, sub, name))
for name in os.listdir(os.path.join(ROOT, 'templates', 'js')):
    shutil.copy2(os.path.join(ROOT, 'templates', 'js', name),
                 os.path.join(PUBLIC, 'js', name))

json.dump({'api': api_port, 'static': static_port, 'public': PUBLIC},
          open(os.path.join(SP, 'stand.json'), 'w'))
print('API :%d  STATIC :%d' % (api_port, static_port))
print('READY')
sys.stdout.flush()
while True:
    time.sleep(3600)
