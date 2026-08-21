# План рефакторинга и уборки (август 2026)

Составлен 2026-08-21 после сплошного аудита кода и документации
(два субагента + перепроверка ключевых утверждений на живом проде).
Базовая точка: `pytest` — **345 passed** за 1.5 с, прод в рабочем состоянии.

Документ — план, а не журнал. Выполнение фиксировать в
`docs/deployment-log.md` (для выкладок) и отмечать здесь галочками.

---

## 0. Главная находка: два поколения фронтенда сосуществуют, и стык между ними
   уже сломан

### Что есть сейчас (проверено на https://medinsky.net)

| Адрес | Что реально отдаётся | Какой JS |
|---|---|---|
| `<doc>/index.html` | оглавление (`page_html.render_toc`) | `legacy-page-redirect.js` |
| `<doc>/pages/<label>/index.html` | постраничный просмотрщик | `page-view.js` |
| `<doc>/document_index.html` | **старый SPA** | `layout/comment-content/annotations/mobile/main` + `redpen-editor-panel/redpen-auth/redpen-editor-bootstrap` |

Механика сборки: шаг 3 `publish_website_data()` копирует
`templates/document_index.html` в `<doc>/index.html`, а шаг 3.6
`generate_page_html()` **перезаписывает** этот же файл оглавлением
([build_website.py:1266-1281](../scripts/build_website.py#L1266)).
SPA выживает только во второй копии, которая создаётся с комментарием
«Also create document_index.html for compatibility with tests»
([build_website.py:592](../scripts/build_website.py#L592),
[:1089](../scripts/build_website.py#L1089)).

Итого: **единственная production-роль старого SPA — быть носителем редактора**,
и держится она на файле, который заведён ради тестов. Нигде не задокументировано.

### Следствие — живой баг

Кабинет строит ссылку на редактор как `../<docId>/?p=<label>&editor=1&ann=<id>`
([cabinet.js:149-157](../templates/cabinet/cabinet.js#L149)).
Этот адрес — оглавление. `legacy-page-redirect.js` специально не уводит запросы
с `editor=1` («редактору старый просмотрщик по-прежнему нужен»,
[legacy-page-redirect.js:17](../templates/js/legacy-page-redirect.js#L17)), но
редакторских скриптов на оглавлении нет. Значит переход «из кабинета в редактор»
приводит на страницу, где редактор не запускается. Проверить руками на проде до
любых правок — если подтвердится, это отдельный хотфикс (см. 1.0).

### Решение (принято 2026-08-21): вариант A

**Старый SPA удаляется. Редактор переезжает в собственное приложение.**

Формулировка пользователя: редактор должен жить в отдельной реальности — он
работает с базой через API и не должен подчиняться ограничениям просмотрщика.
Это разводит два продукта, которые сейчас склеены одним DOM:

- **просмотрщик** — статика, ноль сетевых зависимостей, инвариант офлайна;
- **редактор** — обычное веб-приложение с API, авторизацией и состоянием,
  которому статические ограничения не нужны вовсе.

Что это даёт немедленно:
- минус ~1810 строк JS (`main.js` 580, `annotations.js` 547,
  `comment-content.js` 377, `mobile.js` 245, `layout.js` 61),
  минус `templates/document_index.html` (161) и часть CSS;
- **исчезает CDN-зависимость `marked.min.js`** (она только в
  `document_index.html:148`) — офлайн-инвариант перестаёт нарушаться сам собой,
  вендорить ничего не надо;
- исчезают кластеры дублей B/C/D/E (фильтр по тегам, карта размеров маркера,
  позиционирование попапа, масштабирование координат) — они существуют ровно
  потому, что путей рендера три;
- офлайн-бандл резко упрощается (см. раздел 3).

Порядок важен: **сначала новый редактор, потом снос SPA.** Пока редактор живёт
на `document_index.html`, удаление SPA обезоруживает редактирование.
Детали — раздел 5.

---

## 1. Безрисковая уборка (поведение не меняется) — выполнено 2026-08-21, кроме 1.0/1.6

- [ ] **1.0 (сначала проверить, потом чинить)** — воспроизвести переход
      «кабинет → редактор» на проде. Если редактор не открывается — хотфикс:
      либо ссылка на `document_index.html` (вариант B), либо ждать переноса
      (вариант A).
- [x] **1.1** Удалить второе, недостижимое определение `snapshot_paths`,
      `backup_publish_dir`, `clean_publish_dir`, `compare_path_sets` —
      [build_website.py:1326-1403](../scripts/build_website.py#L1326), после
      `if __name__ == "__main__"`. Побайтовый дубль блока :35-109.
- [x] **1.2** Удалить `templates/index.html` — мёртвый файл: корневой лендинг
      генерируется inline-строкой в `create_index_page()`
      ([build_website.py:706-1110](../scripts/build_website.py#L706)), а в
      публикацию из `templates/` копируются только `css/*.css`, `js/*.js`,
      `favicon.svg`, `cabinet/*` ([:616-636](../scripts/build_website.py#L616)).
- [x] **1.3** (код удалён; DDL таблицы оставлен с TODO — нужна проверка на проде)
      Удалить подсистему рецензирования в
      [db.py:834-1069](../scripts/api/db.py#L834) — `summarize_reviews`,
      `derive_status`, `list_reviews`, `reviews_for_annotations`,
      `_apply_quorum`, `upsert_review`, `delete_review`, `review_coverage`,
      `_review_row_to_dict` (~230 строк) плюс DDL `annotation_reviews`
      ([db.py:101-114](../scripts/api/db.py#L101)). Ни одной ссылки во всём
      репозитории: ни API, ни кабинета, ни тестов, ни доков, ни плана этапов.
      **Осторожно:** DDL убирать только после проверки, что на проде таблица
      пуста (`SELECT count(*) FROM annotation_reviews`); если непусто —
      удалить код, таблицу оставить до следующего именного бэкапа.
- [x] **1.4** Удалить мёртвые скрипты в `scripts/` (нет ссылок нигде, кроме
      `.idea/workspace.xml`): `rename.py`, `rename_files.py`,
      `rename_annotations.py`, `fix_file_numbering.py`,
      `restore_and_rename_files.py`, `create_docs_from_list.py`,
      `create_paragraph_docs.py`, `add_grid_to_images.py` (заменён на
      `make_grid_images.py`). `extract_pdf.py` — тоже без ссылок, но соседние
      `extract_images.py`/`extract_text.py`/`process_pdf.py` упомянуты в доках
      как исторический конвейер: решить пакетом (либо все четыре в
      `scripts/legacy/`, либо удалить `extract_pdf.py`).
      **Не трогать** `publish_data.py` — он подгружается динамически через
      `importlib` ([build_website.py:172-176](../scripts/build_website.py#L172)).
- [x] **1.5** Удалить/переписать shell-скрипты, работающие с submodules,
      которых больше нет: `scripts/push_changes.sh`,
      `scripts/update_and_build.sh`, `scripts/de_submodule_cleanup.sh`
      (последний — одноразовая миграция, своё отработал).
- [~] **1.6** (отложено до переезда редактора: `document_index.html` — носитель
      редактора. Сделано: удалена бессмысленная копия в корне сайта и
      исправлены вводящие в заблуждение комментарии.)
      Убрать генерацию дублей `document_index.html` «for test
      compatibility» ([:592](../scripts/build_website.py#L592),
      [:1089](../scripts/build_website.py#L1089)) — вместе с правкой тестов,
      которые их требуют. В варианте A файл уходит целиком; в варианте B
      остаётся один осмысленный экземпляр с честным комментарием «здесь живёт
      редактор».

Ожидаемый эффект раздела: −~700 строк Python, −~160 строк HTML, −8 файлов
в `scripts/`, минус одна целая неиспользуемая таблица БД.

## 2. Дедупликация

### 2.1 Python

- [ ] Разнести `publish_website_data()`: ветки «один документ»
      ([:476-538](../scripts/build_website.py#L476)) и «все документы»
      ([:539-613](../scripts/build_website.py#L539)) — копипаста одного блока.
      Свести к `_publish_one_document(doc_id, …)` в цикле.
- [ ] Разбить `create_index_page()` (404 строки, самая длинная функция репо) —
      как минимум вынести HTML-шаблон лендинга из Python-строки в
      `templates/landing.html` с подстановками. Это же снимает вопрос
      «почему `templates/index.html` мёртв».
- [ ] Общий обход файлов страниц: `PAGE_FILE_RE` + `_iter_doc_dirs` +
      `_iter_page_files` в [import_annotations.py:29-54](../scripts/api/import_annotations.py#L29)
      и `PAGE_MD_RE` + `iter_page_files` в
      [backfill_tags.py:33-47](../scripts/api/backfill_tags.py#L33) →
      один хелпер (`scripts/api/page_files.py`).

### 2.2 JavaScript

После сноса SPA пункты ниже растворяются сами: остаётся один путь рендера
(`page-view.js`) и редактор, который к нему не привязан. Поэтому **отдельно
дедуплицировать этот JS не нужно** — он удаляется вместе с SPA. Список оставлен
как чек-лист «что должно исчезнуть», чтобы при переносе ничего не потерялось:

- [ ] `tag-filter.js`: фильтр `?tags=`/`?notags=`/`?showDrafts=1` реализован
      дважды — [main.js:60-124](../templates/js/main.js#L60) (читает и query, и
      hash) и [page-view.js:52-101](../templates/js/page-view.js#L52) (только
      query). Расхождение уже фактическое, не гипотетическое.
- [ ] Константы маркера (`{main:90, comment:50, small:25}`) — три копии:
      [annotations.js:63](../templates/js/annotations.js#L63),
      [page-view.js:20](../templates/js/page-view.js#L20),
      [redpen-editor-bootstrap.js:903](../templates/js/redpen-editor-bootstrap.js#L903).
- [ ] Позиционирование попапа: [comment-content.js:66,284](../templates/js/comment-content.js#L66)
      vs [page-view.js:254,327](../templates/js/page-view.js#L254).
- [ ] `apiBase()` / `withJsonHeaders()` / загрузка Google Identity Services —
      дубли в [redpen-auth.js:10-15,106](../templates/js/redpen-auth.js#L10),
      [redpen-editor-bootstrap.js:258-263,382](../templates/js/redpen-editor-bootstrap.js#L258),
      [cabinet.js:40](../templates/cabinet/cabinet.js#L40) (последний уже
      делегирует в `RedPenAuth` — довести до конца остальных).

### 2.3 Тесты

- [ ] `find_free_port`/`start_http_server` определены заново в четырёх файлах
      (`editor_mode_tests.py:14`, `simple_test.py:16`,
      `annotation_position_tests.py:95`, `reproduce_404.py:23`) → `tests/_http_helpers.py`.
- [ ] Логин+CSRF обвязка `TestClient` — `test_api.py:51` и `test_auth.py:43-51`
      → фикстура в `conftest.py`.
- [ ] Решить судьбу `tests/reproduce_404.py` и `tests/simple_test.py`:
      это отладочные скрипты, а не тесты; либо в `tests/manual/`, либо удалить.
- [ ] `tests/annotation_position_tests.py` — по памяти проекта это заглушка,
      не подгружающая `annotations.js`; проверить и либо починить, либо снять
      из `build_website.py` шаг 2 (он гоняет их при каждой сборке).

## 3. Офлайн-инвариант (нарушен прямо сейчас)

- [ ] `templates/document_index.html:148` тянет `marked.min.js` с jsdelivr —
      IP читателя утекает третьей стороне при каждом открытии SPA-страницы, и
      это прямое нарушение инварианта из `CLAUDE.md`. Уходит вместе с SPA —
      вендорить `marked` больше не нужно, пункт 0 из «что дальше» в
      `offline-bundle-plan.md` можно закрыть как отпавший.
- [ ] Незакоммиченная ветка офлайн-бандла (`scripts/make_offline_bundle.py`,
      `templates/js/redpen-offline.js`, два теста, `docs/offline-bundle-plan.md`)
      — довести до коммита или явно отложить; сейчас она висит вне git и
      не отражена в `CLAUDE.md`. При доведении сверить трактовку `metadata.json`
      с `generate_page_manifest.py` (дублирующее перечисление страниц).
- [ ] **Пересмотреть бандл под новую архитектуру.** `redpen-offline.js` (шим
      `fetch`) и `offline-data.js` нужны только SPA: постраничный просмотрщик
      не делает ни одного `fetch` (запрет зафиксирован в шапке
      [page-view.js:9](../templates/js/page-view.js#L9)), аннотации приходят
      inline-блоком `redpen-page-data`. После сноса SPA бандл = прямая копия
      статики, шим и `offline-data.js` (3.6 МБ) выбрасываются.
      Плюс `rewrite_doc_index()` в
      [make_offline_bundle.py:104](../scripts/make_offline_bundle.py#L104)
      правит `<doc>/index.html` в расчёте на SPA, а это давно оглавление —
      цель уже уехала.

## 4. Документация — выполнено 2026-08-21

- [x] `docs/STATE_OVERVIEW.md:15-18,23,66,153-154` — описывает упразднённый тип
      `general` как живую фичу. Тип отвергается конвертером
      ([annotation_converter.py:136-143](../scripts/annotation_converter.py#L136)),
      в UI его нет ([redpen-editor-panel.js:17-18](../templates/js/redpen-editor-panel.js#L17)).
      Переписать раздел «Режим редактора»: только `main`/`comment`, `coords`
      обязательны. Заодно добавить туда правду о ролях `index.html` /
      `pages/<label>/` / `document_index.html` из раздела 0.
- [x] `scripts/api/README.md:75` — «черновики никогда не попадают в статический
      JSON» неверно с 2026-08-15: черновики лежат в общем `page_NNN.json` под
      тегом `draft` ([publisher.py:62-72](../scripts/api/publisher.py#L62)).
- [x] `scripts/api/README.md` — добавить `GET /api/tags`
      ([main.py:922](../scripts/api/main.py#L922)), поле `tags?` в теле
      POST/PUT `/api/editor/...` ([main.py:766,812](../scripts/api/main.py#L766)),
      и описание CLI `backfill_tags.py` рядом с `import/export_annotations.py`.
- [x] `docs/page-addressing-proposal.md:3-5` — шапка «ничего не реализовано»
      при реализованной и выложенной схеме. Добавить пометку «реализовано».
- [x] `docs/PROJECT_STRUCTURE.md:33` — «аннотации пишутся вручную и
      конвертируются» не отражает конвейер (агент → черновики → БД-канон,
      конвертация по умолчанию выключена).
- [x] `CLAUDE.md`, карта документации — не перечислены `PROJECT_STRUCTURE.md`,
      `local-docker-agent.md`, `general-migration-map.json`,
      `seo-page-urls-log.md`, `offline-bundle-plan.md`,
      `annotation-agent-run-prompt.template.md`.
- [x] `CLAUDE.md` — «~250+ тестов» → 345.
- [x] Пометить шапкой «историческое, топология устарела»
      `docs/agent-instructions-stage-0-1.md` (он ссылается на
      `server-setup-guide.md` как на источник актуальной топологии).

## 5. Редактор как отдельное приложение

Направление задано пользователем; детали дизайна — предмет отдельной сессии.
Здесь фиксируются только рамки и открытые вопросы, чтобы снос SPA не начался
раньше времени.

Рамки:
- редактор — отдельная точка входа (кандидат: `/editor/`, рядом с `/cabinet/`,
  который уже устроен именно так: собственный html/js/css, авторизация через
  `RedPenAuth`, все данные — из API);
- источник данных — только API (`GET /api/editor/{docId}/{pageNum}` уже отдаёт
  всё нужное, включая `serverPageSha` для оптимистической блокировки);
  картинка страницы — статический `images/<page>.png`, как и сейчас;
- просмотрщик про редактор не знает вовсе: параметр `?editor=1`, ветка в
  [legacy-page-redirect.js:17](../templates/js/legacy-page-redirect.js#L17)
  и вся обвязка `hasEditorFlag()` уходят;
- кабинет начинает ссылаться на `/editor/?doc=<docId>&page=<pageKey>&ann=<id>`
  вместо сломанного сегодня `<doc>/?p=…&editor=1`.

Что переиспользуется из существующего:
- [redpen-editor-panel.js](../templates/js/redpen-editor-panel.js) (282 строки)
  — форма редактирования, уже автономна, зависит только от своего DOM;
- [redpen-auth.js](../templates/js/redpen-auth.js) — общий модуль авторизации
  с кабинетом;
- из [redpen-editor-bootstrap.js](../templates/js/redpen-editor-bootstrap.js)
  (1173 строки) — логика координат, оптимистической блокировки и `annIdFromDom`;
  остальное (внедрение в чужой DOM, отложенная подстановка `general`, кэш
  общих комментариев) отмирает вместе с SPA.

Открытые вопросы (решить до реализации):
1. Один экран на страницу книги или список страниц слева (как в кабинете)?
2. Нужен ли редактору предпросмотр «как на сайте» — если да, он тянет за собой
   рендер маркеров, и это единственная причина переиспользовать `page-view.js`.
3. UI редактирования тегов (не сделан до сих пор) — делать сразу в новом
   редакторе или отдельно.
4. Судьба `?tags=`/`?notags=`/`?showDrafts=1` в редакторе: там нужен показ
   всего без фильтров, значит фильтр — это чисто просмотрщиковая вещь.

## 6. Порядок и проверка

1. Раздел 1 (уборка) одним коммитом-серией, после каждого шага `pytest`.
   Ожидание: 345 passed без изменений, кроме шага 1.6 (там тесты правятся).
2. Раздел 4 (доки) — отдельным коммитом, ничего не ломает, можно параллельно.
3. Новый редактор (раздел 5) — отдельная сессия с собственным планом.
   Только после того, как он работает и принят, — снос SPA
   (`document_index.html`, `main.js`, `annotations.js`, `comment-content.js`,
   `mobile.js`, `layout.js`, `css/global-comment.css`) и разделы 2.2 и 3.
4. Раздел 2.1 (Python) — после уборки, с прогоном
   `python scripts/build_website.py --skip-tests --skip-push --target-dir ./out`
   и diff-сравнением `./out` до и после (для этого в скрипте уже есть
   `--backup-publish`/`--compare-paths`).
5. На прод — только с явного подтверждения пользователя; перед любыми
   действиями с БД (шаг 1.3) — именной бэкап `VACUUM INTO`.

## Чего в плане намеренно нет

- Переписывание `db.py`/`main.py` на слои/ORM: файлы большие (1134/1000), но
  структура плоская и предсказуемая, дублей внутри аудит не нашёл — трогать
  без нужды дороже, чем терпеть.
- Смена формата опубликованных аннотаций: последняя такая смена каталась в две
  фазы и стоила отдельного журнала; вне задачи «уборка».
