# RedPen Deployment Log

## Цель
Развернуть проект RedPen на чистом сервере с IP: 70.34.202.231

## Информация о сервере
- **IP:** 70.34.202.231
- **Домен:** medinsky.net (DNS настроен)
- **API домен:** api.medinsky.net
- **Целевая структура:** /root/apps/redpen/

## Архитектура проекта
- **frontend:** Nginx для статического сайта из redpen-publish
- **api:** FastAPI для обработки аннотаций и данных
- **caddy:** Reverse proxy с автоматическим HTTPS
- **content-sync:** Webhook listener для GitHub (синхронизация redpen-publish)

---

## Лог попыток развертывания

### 2026-05-17 - Начало развертывания

#### Шаг 1: Изучение проекта
- [x] Прочитал docker-compose.yml
- [x] Прочитал RedPenServerInstallScript.sh
- [x] Изучил структуру проекта и документацию
- [x] Понял зависимости между сервисами

#### Выводы:
- Проект использует Docker Compose для оркестрации
- Требуется SSH ключ для доступа к GitHub репозиторию redpen-publish
- Необходимо создать .env.secrets с WEBHOOK_SECRET
- Caddy автоматически получает SSL сертификаты

---

#### Шаг 2: Установка зависимостей
- [x] Проверен SSH доступ к серверу
- [x] Обнаружено: Docker 29.5.0, Docker Compose v5.1.3, git 2.43.0 уже установлены
- [x] Создана структура каталогов `/root/apps/redpen/{infra,secrets/content-ssh}`

#### Шаг 3: Настройка GitHub Deploy Key
- [x] Сгенерирован SSH ключ: `/root/apps/redpen/secrets/content-ssh/id_ed25519`
- [x] Настроены права доступа (600 для приватного, 644 для публичного)
- [x] Добавлен github.com в known_hosts

**Публичный ключ для добавления в GitHub:**
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDZyVM/SDZL2OzSW2M5d2idlIGW1e+wISVwcicVlp4ir redpen-deploy@medinsky.net
```

**Инструкция по добавлению:**
1. Перейти: https://github.com/volokhonsky/redpen-publish/settings/keys
2. "Add deploy key"
3. Title: `RedPen Server Deploy Key`
4. Key: вставить ключ выше
5. **Важно:** НЕ ставить галочку "Allow write access"

#### Шаг 4: Создание секретов
- [x] Сгенерирован WEBHOOK_SECRET: `XOw44ZgxE1GcyuDRkzmoSvSAeR7rSh5lcDraWoaWRq4=`
- [x] Создан файл `/root/apps/redpen/infra/.env.secrets` с правами 600

#### Шаг 5: Копирование файлов проекта
- [x] Скопированы все конфигурационные файлы на сервер
- [x] Файлы размещены в `/root/apps/redpen/infra/`

#### Шаг 6: Запуск Docker Compose
- [x] Собраны все Docker образы
- [x] Запущены все сервисы

#### Шаг 7: Проверка работоспособности
- [x] **Caddy**: Работает, HTTPS сертификат получен автоматически
  - `https://medinsky.net` - возвращает 403 (ждет файлов из redpen-publish)
  - SSL/TLS работает корректно
- [x] **API**: Полностью работает
  - `https://api.medinsky.net/api/health` - возвращает `{"status":"ok"}`
- [x] **Frontend**: Nginx работает, но нет контента (ожидается)
- [ ] **Content-sync**: Ждет добавления Deploy Key в GitHub
  - Ошибка: `Permission denied (publickey)`
  - После добавления ключа нужно перезапустить контейнер

---

## Текущий статус

### ✅ Работает:
- HTTPS с автоматическими сертификатами Let's Encrypt
- Reverse proxy через Caddy
- API endpoint на api.medinsky.net
- Все Docker контейнеры запущены

### ⏳ Ожидает действий:
1. **Добавить Deploy Key в GitHub** - см. публичный ключ выше
2. **Настроить GitHub Webhook** для автоматической синхронизации:
   - URL: `https://medinsky.net/.hooks/redpen-publish`
   - Content type: `application/json`
   - Secret: `XOw44ZgxE1GcyuDRkzmoSvSAeR7rSh5lcDraWoaWRq4=`
   - Events: Just the push event

---

## Следующие шаги
1. **[ДЕЙСТВИЕ ТРЕБУЕТСЯ]** Добавить публичный SSH ключ как Deploy Key в GitHub
2. Перезапустить content-sync после добавления ключа: `docker compose restart content-sync`
3. Настроить GitHub Webhook с указанным секретом
4. Проверить, что content-sync успешно склонировал репозиторий
5. Проверить доступность сайта на https://medinsky.net

---

## 2026-07-08 — Деплой Этап 0 (безопасность) + Этап 1 (Google-аутентификация)

По плану `docs/editor-improvement-plan.md` / `docs/agent-instructions-stage-0-1.md`,
17 коммитов на `main`, 105 pytest тестов зелёные.

### Что сделано
- **Бэкенд**: scp обновлённых `scripts/api/*.py` (включая новый `db.py`) и
  `docker-compose.yml` в `/root/apps/redpen/infra`; `docker compose up -d --build api`.
  До этого на проде всё ещё крутился старый `main.py` с захардкоженными
  `VALID_TOKENS` — теперь заменён.
- **Сервер `.env`**: пользователь вписал `GOOGLE_CLIENT_ID`; агент добавил
  `ADMIN_EMAILS=volokhonsky@gmail.com` и новый `EDITOR_TOKENS` (старые
  токены считаются скомпрометированными — были в git-истории). Оригинал
  `.env` сохранён как `.env.bak-<timestamp>` перед правкой.
- **Volume**: новый именованный том `redpen_db` (SQLite users/sessions/allowlist),
  примонтирован в api на `/var/redpen-db`; владелец каталога — `app` (uid 10001),
  подтверждено `docker exec ... ls -la /var/redpen-db`.
- **Smoke-check** (все прошли): `GET /api/health` → 200; анонимный
  `POST /api/editor/...` → 401; анонимный `GET /logs` → 401; CORS preflight
  с `Origin: https://medinsky.net` → отражает origin + `allow-credentials: true`.
- **Фронтенд**: `python scripts/build_website.py --skip-tests` (локально) →
  закоммитил и запушил в `redpen-publish` (commit `28de1a6`) → на сервере
  `docker restart redpen-content-sync-1` подтянул `10b33ea..28de1a6`.
  Проверено: `medinsky.net/medinsky11klass/` отдаёт новый `document_index.html`
  с `REDPEN_GOOGLE_CLIENT_ID`, `js/redpen-editor-bootstrap.js` и
  `js/redpen-editor-panel.js` содержат новый auth-код.
- **Важно**: в локальном `redpen-publish/` перед сборкой обнаружились чужие
  незакоммиченные правки (тестовый `document_index.html` с заголовком
  "RedPen Test", правка `page_007.json`, файл `images/page_007.png`) —
  не связаны с этим деплоем, происхождение неясно (последние коммиты этих
  файлов датированы октябрём 2025). Застэшены (`git stash`, сообщение
  "pre-existing local edits unrelated to stage 0/1 auth deploy"), не
  запушены.
  - **Резолюция (2026-07-08, позже в тот же день)**: стэш разобран и удалён.
    `document_index.html` — тестовая заглушка ("RedPen Test"), в прод не годится;
    `images/page_007.png` — PNG 1×1 пиксель (отладочный мусор);
    правка `page_007.json` (удаление двойного пробела) — уже попала в HEAD
    автоматически при сборке 28de1a6 (сгенерирована из markdown-исходника).
    Ничего коммитить не потребовалось, стэш дропнут (был `5dec58e`).

### Не сделано (осознанно, вне рамок стадий 0/1)
- Ручная проверка входа через Google в браузере (агент не может пройти
  реальный Google OAuth flow) — стоит проверить руками:
  `https://medinsky.net/medinsky11klass/?editor=1`, кнопка Google, сохранение
  аннотации под editor-ролью (email должен быть в `ADMIN_EMAILS` или
  `editor_allowlist`, иначе роль будет `viewer` и запись даст 403).
- Этап 2 (аннотации в БД + публикация JSON-снапшотов в `redpen_public`) —
  до сих пор актуальны находки A2/A3 из аудита: запись через редактор
  попадает в примонтированную рабочую копию, а не в volume, который
  раздаёт nginx.

---

## 2026-07-09 — Деплой этапа 2 (части A и B): аннотации в SQLite + адресация страниц

По плану `docs/agent-instructions-stage-2.md`, 16 коммитов на `main`
(0071460..cada6d2, потом ещё 5c55df2 — фикс прав файлов, найденный в процессе
деплоя), 162 pytest-теста зелёные. Журнал разработки — `docs/stage-2-progress-log.md`.

### Расхождение с планом: топология `redpen-publish` на сервере

Runbook предполагал, что `/root/apps/redpen/infra/redpen-publish` (bind-mount
`STORAGE_DIR` в api) — git-клон с полным контентом. По факту это **не git-репозиторий**,
а маленькая директория-заглушка от старого API (единственный файл —
протухший `page_007.json` с `annotations: []`, самим API же и созданный при
первом `GET` до этапа 2). Настоящий опубликованный контент (449 файлов)
живёт (а) в самом `/root/apps/redpen/infra` (это отдельный git-клон
`redpen-publish.git`, использованный при первичном разворачивании инфры) и
(б) в volume `redpen_public` (`/srv/public`), который наполняет `content-sync`
из своего собственного клона в `/srv/repo`.

Из-за этого адаптировал два шага рантбука:
- **Импорт**: не из `/var/redpen-data` (там было почти пусто), а из
  `/srv/public` (после того как в `docker-compose.yml` добавился том
  `redpen_public:/srv/public` в сервис `api`) — это и есть текущий живой
  контент. `docker exec redpen-api-1 python scripts/api/import_annotations.py /srv/public`
  → `docs=1 pages=449 imported=33 skipped=0 errors=0`.
- **Экспорт снапшота в git**: `export_annotations.py --to /var/redpen-data`
  (как в плане) → затем вручную скопировал получившиеся файлы в
  git-репозиторий (`/root/apps/redpen/infra` на сервере не смог запушить —
  его deploy-ключ read-only; закоммитил и запушил из локального клона
  `redpen-publish/` на своей машине, коммит `c10f55a`). Разница — 2 файла
  (`page_017.json`, `page_204.json`): аннотациям с пустым `id` в исходном
  контенте импорт присвоил `srv-import-<hex>`, остальное не изменилось.

### Найденный и исправленный на лету баг: права 600 на публикуемых файлах

`publisher.py` (и `export_annotations.py`) писали через `tempfile.mkstemp()`
(режим 0600, только владелец) и `os.replace` — атомарно, но без `chmod`.
Пока запись шла в `STORAGE_DIR` (никогда не раздавался nginx напрямую), это
было незаметно. Как только `PUBLISH_DIR=/srv/public` (тот же volume, что
раздаёт `frontend`/nginx) — republish 15 существующих страниц при первом
`publish_all()` на старте **тут же дал 403 на живом сайте**
(`medinsky.net/medinsky11klass/annotations/page_007.json` и другие).
Пофиксил (`os.chmod(tmp_path, 0o644)` перед `os.replace`), задеплоил
отдельным циклом `scp` + `docker compose up -d --build api` **до** продолжения
рантбука, добавил regression-тесты (`test_publish_page_writes_world_readable_file`,
аналог в `test_export_annotations.py`), коммит `5c55df2`. Проверено: файлы
снова 644, `curl` → 200 по всей книге (выборочно проверил 001/006/007/008/020/100/200/300/400).

Тот же паттерн (`mkstemp` без `chmod`) есть и в `storage.py::save_inbox` —
не трогал (не раздаётся nginx, ниже приоритет), но стоит поправить отдельно.

### Что сделано (по шагам рантбука)

**Runbook A:**
1. Бэкапы: `tar` рабочей копии (там мало что было, см. выше) +
   `VACUUM INTO` снапшот БД (`/var/redpen-db/pre-stage2.db`), плюс сразу
   настроен и проверен `/etc/cron.daily/redpen-db-backup` (первый бэкап —
   `/root/backups/redpen-db/redpen-20260709.db`, 131 KB).
2. Код скопирован (`scp`) в `/root/apps/redpen/infra`: `scripts/api/*`,
   `content-sync/{content_sync.py,entrypoint.sh}`, `docker-compose.yml`.
   `docker compose up -d --build api content-sync`.
3. Импорт — см. выше, без ошибок.
4. `docker restart redpen-content-sync-1`: подтверждено, что аннотации **не
   затираются** (файл/права те же, что до рестарта) — критерий A2.6 подтверждён
   на проде.
5. `docker compose up -d --force-recreate api`: самолечение подтверждено
   (`startup publish_all pages=15 failed=0`).
6. E2E через реальный API (dev-токен из `.env`): `POST` тестовой аннотации на
   `page_-01` (нестандартная страница) → сразу видна на живом
   `medinsky.net/.../annotations/page_-01.json`; `DELETE` → сразу пропала;
   `annotation_history` содержит `create`+`delete`. Тестовые данные удалены,
   сессия закрыта (logout).
7. Снапшот в git — см. выше про топологию, коммит `c10f55a`.

**Runbook B:**
1. Бэкенд (B.4) был задеплоен вместе с частью A (тот же `scp scripts/api/*`).
   Smoke: `/api/editor/medinsky11klass/006` ≡ `.../6` (тот же `pageId`,
   те же аннотации); `.../000` и `.../-01` → 200.
2. Фронтенд: `python scripts/build_website.py --skip-tests --skip-push`
   локально по реальному `redpen-content` → diff показал ожидаемые 7 файлов
   (`js/main.js`, `js/mobile.js`, `js/redpen-editor-bootstrap.js`,
   `document_index.html`×2, `index.html`×2 — таймстемп + `page-input`
   `type="number"→"text"`); генератор манифеста подтвердил легаси-режим для
   `medinsky11klass` (нет `pageNumbering.printedStartFile` в `meta.json`).
   Запушено (коммит `35aa8d6`), `docker restart redpen-content-sync-1`.
3. Ручная проверка в браузере (claude-in-chrome, реальный `medinsky.net`):
   `?page=6` открывает ту же страницу, что раньше (кружки/попапы/общий
   комментарий на месте); поле «Стр.» + «Перейти» с числом `20` работает
   (SPA-навигация, `pushState`); пагинация показывает голые числа без
   `A1/A2` (легаси-режим, манифеста у документа нет); ошибок в консоли нет.
   `?p=`/буквенные метки на живом сайте пока не проверялись — у
   `medinsky11klass` нет манифеста (осознанно, см. `page-addressing-proposal.md`).
4. `TODO` — пункт про нумерацию решил пока не отмечать: манифест для
   `medinsky11klass` не сгенерирован (нужна секция `pageNumbering.printedStartFile`
   в `meta.json`, это решение о данных, не техническая задача), так что
   исходная жалоба (буквенная нумерация A1/A2/1/2/3) с точки зрения
   пользователя книги ещё не решена — решена только инфраструктура под неё.

### Итоговое состояние прод-сервера

- `redpen-api-1`, `redpen-content-sync-1` — пересобраны на стадии 2 (образ
  включает `db.py`/`publisher.py`/`import_annotations.py`/`export_annotations.py`,
  content-sync — с exclude `*/annotations/` и chown).
- БД: 33 аннотации (импортированы) + 2 (create/delete из E2E-теста, мягко
  удалена) = 35 строк в `annotation_history`.
- `/root/apps/redpen/infra` (git-клон) синхронизирован с `redpen-publish`
  origin/main (`35aa8d6`); удалён устаревший корневой `metadata.json`.
- Ежедневный бэкап БД настроен и подтверждён.
- Известные хвосты: `page_000`/`page_-01` для `medinsky11klass` существуют
  как файлы (`[]`), но в БД появятся только когда через них реально что-то
  отредактируют (это ожидаемо — импортировать в БД нечего, если аннотаций
  нет).

### Критерии приёмки — сверка

Часть A: все пункты из `docs/agent-instructions-stage-2.md` подтверждены на
проде (см. шаги выше), включая непредусмотренный планом (но найденный и
закрытый) баг с правами доступа.
Часть B: подтверждены — легаси-URL не сломаны, ключ `6`≡`006`, `000`/`-01`
адресуемы через API, просмотр по-прежнему не ходит в API. `?p=`-режим
готов технически, но не активирован ни для одного документа (данных для
манифеста `medinsky11klass` пока нет).

---

## 2026-07-09 (продолжение) — Манифест страниц для medinsky11klass активирован

Решил вопрос о данных для манифеста самостоятельно (по просьбе пользователя),
без дополнительных уточнений.

### Как определил frontMatter/printedStartFile

Открыл первые страницы `redpen-content/medinsky11klass/images/page_{000..006}.png`
и последнюю (`page_447.png`) глазами:
- `page_000` — обложка (полноцветная иллюстрация), номера нет.
- `page_001` — титульный лист (авторы/название/класс), номера нет.
- `page_002` — рецензенты + выходные данные, номера нет.
- `page_003` — первая страница с видимым печатным номером **«3»**
  («Введение»).
- `page_004`…`page_447` — печатный номер = номер файла напрямую (проверил
  `page_004`="4", `page_006`="6" при этом совпадает с `chapters[0].startPage:5`
  для файла `page_005`, `page_202`="202", `page_447`="447" — последняя
  страница, «Оглавление»).

(Текстовые `text/page_NNN.json` оказались сдвинуты на +1 относительно
`images/page_NNN.png` — не соответствуют друг другу напрямую, поэтому
ориентировался только на сами изображения, не на OCR-текст.)

Записал в `redpen-content/medinsky11klass/meta.json` →
`pageNumbering.frontMatter: [000,001,002]`,
`printedStartFile: "003"`, `printedStartNumber: 3`. Старые
`physicalStart`/`logicalStart` оставил как есть (легаси-fallback).

### Деплой

1. Прогнал генератор точечно (`generate_page_manifest.build_manifest`) —
   448 записей, лейблы уникальны, без ошибок валидации.
2. `python scripts/build_website.py --skip-tests --skip-push` локально —
   diff всего 5 файлов (`metadata.json` документа + 4 таймстемпа), картинки/
   текст/аннотации не тронуты.
3. Проверил вручную в браузере на локальном сервере (`redpen-publish/`
   напрямую через `python -m http.server`): `?p=A1` → обложка,
   пагинация `A1 A2 A3 3 4 5…447`; `?p=3` → файл `page_003`, реальная
   аннотация из БД отрисовалась корректно; легаси `?page=6` → тот же файл
   `page_006`, что и раньше (метка «6»).
   (По пути словил случайный побочный эффект — свежая locally-served
   `metadata.json` не подтягивалась в уже открытой вкладке из-за HTTP-кэша
   браузера, не из-за бага в коде; подтвердил через `fetch(...,{cache:
   'no-store'})` и через новую вкладку.)
4. Закоммитил и запушил в `redpen-publish` (коммит `9d5e370`),
   `docker restart redpen-content-sync-1` на проде.
5. Проверил на живом `medinsky.net` (claude-in-chrome, с hard-reload —
   тот же браузерный HTTP-кэш дал знать о себе и на проде: вкладка,
   открытая до пуша, показала старые plain-номера, пока не сделал
   Cmd+Shift+R): `?p=A1` → обложка, пагинация `A1 A2 A3 … 447`, клик по
   «A3» → `?p=A3` с реальной аннотацией рецензента на странице.
   `curl https://medinsky.net/medinsky11klass/metadata.json` — 448 записей
   в `pages`, `annotations/page_007.json` по-прежнему 200.

### Итог

Исходная жалоба из `TODO` закрыта: `medinsky11klass` показывает печатные
номера книги (A1/A2/A3/3/4/5…447), а не номера файлов. `TODO` отмечен
выполненным.

---

## 2026-07-10 — Публикация 192 новых аннотаций из annotations_draft (стр. 021–093)

Источник: `redpen-content/medinsky11klass/annotations_draft/page_*.md`
(72 файла нового `~~~meta`-формата с полями `tags`/`confidence` — конвертер
их игнорирует, они остаются только в md). Служебные `_check_*`/`_report_*`
файлы не публиковались.

Порядок: локальная конвертация md→JSON (annotation_converter, 72 стр. /
192 аннотации, 0 проблем) → именной бэкап БД `pre-draft-import-20260710.db` →
scp+docker cp на прод → `import_annotations.py` (dry-run, затем боевой:
imported=192 skipped=0 errors=0; импорт аддитивный — старые аннотации не
тронуты, проверено на page_029: старый `ann-page30-1` сосуществует с новыми,
и на page_007 вне диапазона) → `docker restart redpen-api-1`
(startup publish_all pages=87 failed=0) → curl-проверки живого сайта →
снапшот published-рендера скопирован в локальный клон `redpen-publish`
и запушен (коммит `7c83141`).

Заметки:
- `annotations_draft/` в `redpen-content` остаётся незакоммиченным (исходники
  этих 192 аннотаций!) — стоит закоммитить в content-репозиторий.
- Поля `tags`/`confidence` из нового md-формата в модель данных не переносятся —
  если нужны на сайте, это отдельная доработка схемы.

---

## 2026-08-01 — Деплой этапа 3 (кабинет/черновики/история) + режим `?showDrafts=1`

Задеплоено по явному подтверждению пользователя. Этап 3 катился **как есть**,
с незакрытым багом №2 (клик по опубликованному маркеру в редакторе → сохранение
уходит на `circle-<realId>` и создаёт дубликат вместо обновления; затрагивает
только редактора, данные восстановимы, история пишется). Пользователь выбрал
«деплой как есть», фикс — отдельной аккуратной задачей.

Новое в этой же выкладке — режим предпросмотра черновиков в статике:
публикатор рендерит файл-компаньон `annotations/page_<NNN>.drafts.json`,
просмотрщик грузит его только при `?showDrafts=1` (query/hash). Инвариант
статики сохранён (в API просмотрщик не ходит). Коммит инфры `5d4515e`.

### Бэкенд (api)
1. Именной бэкап БД: `docker exec redpen-api-1 ... VACUUM INTO
   /var/redpen-db/backups/pre-stage3-deploy-20260801.db` (745 КБ). Схема НЕ
   менялась (stage 3 добавил только read-функции; миграции не требовалось —
   сверено `CREATE TABLE`/`ALTER` diff пуст).
2. Изменились 3 файла: `scripts/api/{db.py,main.py,publisher.py}` — scp в
   `/root/apps/redpen/infra/scripts/api/` → `docker compose up -d --build api`.
3. Старт: `startup publish_all pages=87 failed=0`, `Application startup complete`.
   Проверки: `GET /api/stats` и `/api/annotations` → 401 (раньше 404 — значит
   stage-3 API поднялся), `annotations/page_007.json` → 200. В проде 0 черновиков.

### Контент (viewer/cabinet) — хирургически, БЕЗ `build_website.py`
`build_website.py` делает `clean_publish_dir` (rmtree всего кроме `.git`) —
рискует потерять images/annotations, поэтому НЕ запускался. Вместо этого в клон
`redpen-publish` скопированы только изменившиеся фронтенд-файлы:
- `js/`: `redpen-auth.js` (новый), `redpen-editor-bootstrap.js`,
  `redpen-editor-panel.js`, `annotations.js`, `comment-content.js`, `main.js`;
- `css/annotations.css`;
- `cabinet/` (новый: `cabinet.css`, `cabinet.js`, `index.html`);
- `medinsky11klass/index.html`: добавлен `<script ../js/redpen-auth.js>` перед
  bootstrap (пер-документный index — сборочный артефакт, регенерить целиком не
  стал, правка точечная).
Аннотации/картинки/текст/metadata НЕ трогались. Коммит `redpen-publish`
`746949f`, push → `docker restart redpen-content-sync-1` (reset --hard на
`746949f`, «Publish complete»).

### Прод-проверки (curl + браузер)
- `js/redpen-auth.js`, `cabinet/`, `cabinet/cabinet.js` → 200;
  `medinsky11klass/` index, `annotations/page_007.json`, `images/page_007.png`
  → 200 (контент цел); `annotations/page_007.drafts.json` → 404 (черновиков нет).
- Вьюер `medinsky11klass?p=A3`: консоль без ошибок, `RedPenAuth.apiBase` →
  `https://api.medinsky.net`, `isDraftMode()` присутствует, 2 published-аннотации.
- `/cabinet/`: рендерится карточка «Вход» с кнопкой Google (GIS загрузился —
  `GOOGLE_CLIENT_ID` настроен), ошибок в консоли нет. Логин-флоу за авторизацией
  не тестировался (действие пользователя).

### Осталось / follow-up
- **Баг №2** (`handleMarkerClick` берёт `circle-`-префиксный id) — отдельная
  задача, не входила в эту выкладку.
- Черновиков в проде пока нет — draft-рендер в живом `?showDrafts=1` проверен
  только локально (см. журнал этой сессии). Появится компаньон-файл, как только
  редактор сохранит первую аннотацию со `status='draft'`.

---

## 2026-08-01 — Публикация 24 черновиков параграфа 1 (стр. 006–020) как `status='draft'`

Задеплоено по явному подтверждению пользователя («новые добавить как черновики
и сразу закоммитить в прод»). Это закрытие «дыры» в начале: §1 (стр. 6–20) был
пропущен на первом проходе аннотатора (2–7 опубликованы 2026-07-10). Существующие
опубликованные аннотации (`annotations/`, 225 published) НЕ тронуты — новые ушли
исключительно как черновики.

Источник: `redpen-content/medinsky11klass/annotations_draft/page_006–020.md`
(24 аннотации: 6/2, 7/1, 8/1, 9/1, 10/1, 11/3, 12/2, 13/2, 14/2, 15/2, 16/2,
17/2, 18/1, 19/1, 20/1). Все с проверенными источниками (веб-fetch каждого URL),
теги/confidence — в md; отчёт `_report_para_1.md`, задачник `_check_para_1.md`,
реестр `_typical_comments.md` пополнены (tc-usa-origin, tc-official-stats,
tc-famine-1946, tc-whatabout).

Инструментальное изменение: `scripts/api/import_annotations.py` получил флаг
`--status {published,draft}` (по умолчанию published — обратная совместимость).
Раньше импорт всегда писал `published`; для «добавить как черновики» нужен был
`draft`. Обновлённый скрипт положен и в `/root/apps/redpen/infra/scripts/api/`
на проде (переживёт rebuild), и в контейнер `redpen-api-1`.

Порядок (проверено):
1. Локально: конвертация md→JSON (annotation_converter, 15 стр. / 24 анн., 0 проблем),
   e2e-тест импорта на временной БД (`--status draft` → 24 draft, идемпотентный
   повтор → skipped=24).
2. Именной бэкап БД: `VACUUM INTO /var/redpen-db/backups/pre-para1-draft-import-20260801.db`
   (745 КБ).
3. `docker cp` скрипта и JSON в контейнер → `import_annotations.py /tmp/import_src
   --doc medinsky11klass --status draft` (dry-run, затем боевой: imported=24
   skipped=0 errors=0). После: published=225 (без изменений), draft=24, deleted=1.
4. `docker restart redpen-api-1` → `startup publish_all pages=96 failed=0`.
5. Прод-проверки (live через caddy): `page_007.json` = 6 published (не изменился);
   `page_007.drafts.json` = 1 (флаг `draft:true`); `page_015.drafts.json` = 2;
   `page_011.drafts.json` = 3. Черновики видны читателю только при `?showDrafts=1`,
   в публичный `page_NNN.json` не попали (инвариант статики сохранён).
6. Снапшот рендера (`page_006–020.json` + `.drafts.json`) скопирован в клон
   `redpen-publish` и запушен; исходники-черновики закоммичены в `redpen-content`.

Заметки:
- `import_annotations.py` с флагом `--status` — то, что нужно и запланированному
  на 15:00 автозапуску (параграф 8+ тоже пойдёт как `draft`).
- content-sync исключает `*/annotations/`, поэтому пуш аннотаций в `redpen-publish`
  живой сайт не затрагивает (аннотации на проде рендерит API) — коммит для
  консистентности git-снапшота.

---

## 2026-08-01 (вечер) — 139 черновиков §8–§10 (стр. 094–123) как `status='draft'`

Задеплоено по явному подтверждению пользователя («заверши всё незавершённое
и отправь их в режиме черновиков на прод»). Продолжение backfill'а: §1 закрыт
утром того же дня, §2–§7 опубликованы 2026-07-10, теперь закрыты §8, §9 и начало
§10. Опубликованные аннотации (225 published) не тронуты.

Генерация: три параллельных субагента Opus по 10 страниц (094–103 — 41 аннотация,
104–113 — 49, 114–123 — 49). Нарезка по 10 страниц режет границы параграфов
(§8 = 94–105, §9 = 106–121, §10 = 122–133), поэтому отчёты и задачники названы
по диапазонам: `_report_pages_<от>-<до>.md`, `_check_pages_<от>-<до>.md`.

**Приём для параллельного запуска:** агентам запрещено писать в общий
`annotations_draft/_typical_comments.md` — три параллельные записи затирают друг
друга. Каждый пишет `_typical_comments_add_<диапазон>.md`, координатор сводит
вручную. При сведении пришлось склеить два независимо предложенных tc об одном
приёме (`tc-censorship-unnamed` + `tc-censorship-invisible` → первый) и дописать
22 tc-тега в meta аннотаций: агенты 2 и 3 предложили новые tc, но в теги их
не проставили. Итог: 7 новых tc, пополнены 6 существующих.

Порядок (проверено):
1. Локально: конвертация md→JSON (`parse_markdown_annotation`, 30 стр. / 139 анн.,
   id уникальны, координаты в пределах листа, 0 проблем); e2e-импорт на временной
   БД (`DB_PATH=…/test.db`, `--status draft` → 139 draft; повтор → skipped=139).
2. Именной бэкап БД: `VACUUM INTO
   /var/redpen-db/backups/pre-para8-10-draft-import-20260801.db` (819 КБ).
3. `tar|scp` → `docker cp /tmp/import_src redpen-api-1:/tmp/import_src` →
   `docker exec -w /app/scripts/api redpen-api-1 python3 import_annotations.py
   /tmp/import_src --doc medinsky11klass --status draft` (dry-run, затем боевой:
   imported=139 skipped=0 errors=0). После: published=225 (без изменений),
   draft=163 (24 + 139), deleted=1.
4. `docker restart redpen-api-1` → `startup publish_all pages=126 failed=0`
   (было 96 страниц, +30).
5. Прод-проверки (live через caddy): все 30 `page_NNN.drafts.json` → 200,
   суммарно 139 аннотаций, флаг `draft:true`; `page_094.json`/`page_116.json` —
   пустые массивы (опубликованных на этих страницах нет); `page_007.json`
   не изменился (6 published). Вьюер `?page=116&showDrafts=1` — маркеры и метка
   `[черновик]` на месте; без флага на той же странице 0 маркеров (инвариант
   статики цел).
6. Снапшот рендера (30 × `.drafts.json`) скопирован в клон `redpen-publish`,
   коммит `5b0a55c`, push. Публичные `page_NNN.json` не изменились. Исходники —
   в `redpen-content` (`febc91e`), плюс догоняющий коммит `fbbe51a` с
   черновиками §2–§7, лежавшими незакоммиченными с 2026-07-10.

Заметки:
- Скрипт в контейнере лежит по `/app/scripts/api/import_annotations.py` (не `/app/`).
- Что осталось: хвост §10 (стр. 124–133) и далее — не сделан. В трёх задачниках
  30 открытых задач; в трёх аннотациях по 4 тега при норме 1–3 (на данные не
  влияет — `tags` в модель не переносятся).
