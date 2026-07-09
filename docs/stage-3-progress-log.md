# Прогресс: Этап 3 — рабочий кабинет (docs/agent-instructions-stage-3.md)

Журнал ведётся по ходу работы агента, чтобы можно было продолжить после
остановки. Каждый пункт — коммит на `main` в локальном репо. Деплой на прод —
только после подтверждения пользователем (см. runbook в исходной инструкции).

Статусы: `[ ]` не начато, `[~]` в процессе, `[x]` готово (закоммичено, тесты зелёные).

## Бэкенд

- [x] C.1 db.py: cabinet-запросы (list_annotations/count_annotations,
      list_history/get_history_record, list_users, get_stats) +
      tests/test_cabinet_db.py — коммит 67e377d
- [x] C.2 Черновики: `status` в `_parse_annotation_body`, PUT без `status`
      сохраняет текущий статус, `GET /api/editor/{doc}/{page}` отдаёт
      черновики editor/admin (`get_optional_user`) — коммит f4b6ab8
- [x] C.3 `GET /api/annotations`, `GET /api/history`, `GET /api/stats` +
      tests/test_cabinet_api.py — коммит dc11716
- [x] C.4 `POST /api/history/{histId}/revert` — тот же коммит dc11716
- [x] C.5 `GET /api/admin/users` — тот же коммит dc11716

**Бэкенд завершён локально (не задеплоен).** 209 pytest тестов зелёные.

## Фронтенд

- [x] C.6 `templates/js/redpen-auth.js` (общий auth-модуль), делегирование из
      `redpen-editor-bootstrap.js` с фолбэком на старую инлайн-реализацию —
      коммит 1b74cdb. Проверено вручную в браузере (login по токену → csrf →
      save → logout, локальный API + собранный сайт) — регрессий не найдено.
- [x] C.7 + C.8 Кабинет `templates/cabinet/{index.html,cabinet.js,cabinet.css}`
      (профиль/вход, вкладки Аннотации/История/Админ, deep-link «Открыть»
      через `metadata.json`) — коммит 1bfa7b4. Проверено вручную: логин
      токеном, фильтры/пагинация, publish↔draft, delete, история+откат,
      вся админ-вкладка (allowlist/users/publish-all/logs), «Открыть» —
      и с манифестом (`?p=<label>`), и без (`?page=N` fallback).
- [x] C.9 `?ann=` deep-link в редакторе, чекбокс «Черновик», пунктирные
      маркеры черновиков — коммит 12dc80e. Проверено вручную: черновик →
      маркер с пунктиром → deep-link подгружает в форму с чекбоксом →
      снятие чекбокса и «Отправить» публикует, пунктир пропадает.

**Фронтенд завершён локально (не задеплоен).**

## Сборка и документация

- [x] C.10 `build_website.py` копирует `templates/cabinet/` →
      `<output>/cabinet/`; `tests/test_build_website.py` проверяет
      `cabinet/{index.html,cabinet.js,cabinet.css}`; `scripts/api/README.md`
      (новые эндпоинты), `docs/STATE_OVERVIEW.md` (раздел «Кабинет»),
      `docs/editor-improvement-plan.md` (этап 3 отмечен).

---

## Заметки по ходу работы

- Полный цикл (backend C.1–C.5, frontend C.6–C.9, build C.10) реализован и
  закоммичен локально на `main`. `pytest` — 209 тестов, все зелёные,
  `node --check` на всех изменённых/новых JS-файлах чист.
- Ручная проверка велась через локальный `uvicorn` (temp `STORAGE_DIR`/
  `LOG_DIR`/`DB_PATH`/`PUBLISH_DIR`, `EDITOR_TOKENS=devtoken:dev`) +
  `python scripts/build_website.py --skip-tests --skip-push --document
  medinsky11klass --target-dir <scratch>` + `python -m http.server`,
  управляемый через Claude Preview MCP. Все временные конфиги
  (`.claude/launch.json` записи на scratch-пути, `REDPEN_API_BASE`/
  `REDPEN_GOOGLE_CLIENT_ID`/`REDPEN_DEV_TOKEN_LOGIN` в скопированном
  `index.html`) — только в scratch-копии, в репозиторий не попали.
- **Найдены и НЕ исправлены (умышленно, вне рамок этапа 3) два
  пред-существующих бага в редакторе**, оба заведены как отдельные задачи
  (spawn_task в сессии, видны как чипы пользователю):
  1. Два вызова `snapshotDomMarkersToState()` внутри
     `onAnnotationsLoaded` (`redpen-editor-bootstrap.js`) обращаются к
     функции, объявленной в замыкании `init()`, откуда `onAnnotationsLoaded`
     её не видит — исключение молча гасится try/catch. В реальности
     `st.page.annotations` обновляется только один раз, при первой загрузке
     документа (через `setTimeout(..., 1000)` внутри `init()`), и не
     обновляется при последующей клиентской навигации по страницам
     (`loadPage()` в `main.js`).
  2. Клик по существующему **опубликованному** маркеру берёт id из DOM
     (`marker.id`), а `annotations.js` рендерит такие маркеры с префиксом
     `circle-` (`circle.id = 'circle-' + a.id`). Из-за этого сохранение
     после клика по опубликованному маркеру уходит на
     `PUT /api/editor/{doc}/{page}/circle-<realId>` — новый `ann_id`,
     реальная аннотация не обновляется, а рядом молча создаётся дубликат.
     Для своего deep-link (`?ann=`, C.9) обошёл проблему точечно
     (сопоставление и с «сырым», и с `circle-`-префиксным id, отправка
     сохраняется под настоящим id из URL), но сам `handleMarkerClick` не
     трогал — это отдельный, более рискованный фикс с широким blast radius
     (основной flow «кликнул маркер → отредактировал → сохранил»).
- `syncServerPageSha()` (уже существовавшая функция, теперь также сажающая
  черновики через `seedDraftAnnotations`) получила короткий retry (до 10 ×
  100мс), если `docId`/`pageKey` ещё не выставлены на момент вызова — это
  чинит гонку с `main.js`'s `loadPage()`, из-за которой черновики/deep-link
  иногда не подхватывались при первой загрузке страницы.
- Не сделано умышленно / вне рамок (см. «Что НЕ входит» в исходной
  инструкции): toast'ы, drag маркеров, автосохранение форм, список
  аннотаций в панели редактора, приватность черновиков, модерация сложнее
  draft/publish, переделка HTML-страницы `/logs`.

## Деплой

- [ ] Показать пользователю готовое локально состояние, дождаться подтверждения
- [ ] Runbook (docs/agent-instructions-stage-3.md, раздел «Runbook деплоя»)
      выполнен на проде

Ничего не задеплоено. Требуется подтверждение пользователя перед деплоем
(бэкап БД, обновление `scripts/api/*.py` в контейнере `api`, пересборка
сайта и push в `redpen-publish`, `docker restart redpen-content-sync-1`,
прод-проверка — см. runbook в `docs/agent-instructions-stage-3.md`).
