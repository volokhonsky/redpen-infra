# Прогресс: Этап 2 + адресация страниц (docs/agent-instructions-stage-2.md)

Журнал ведётся по ходу работы агента, чтобы можно было продолжить после
остановки (usage limit). Каждый пункт — коммит на `main` в локальном репо.
Деплой на прод — только после подтверждения пользователем (см. runbook в
исходном плане).

Статусы: `[ ]` не начато, `[~]` в процессе, `[x]` готово (закоммичено, тесты зелёные).

## Часть A — SQLite + публикация

- [x] A2.1 Схема БД: annotations + annotation_history (db.py) — коммит 0071460
- [x] A2.2 Публикатор scripts/api/publisher.py — коммит 1d540ef
- [x] A2.3 Импорт scripts/api/import_annotations.py — коммит e8afd25
- [x] A2.4 Перевод editor-эндпоинтов на БД (main.py, удаление storage.py page-функций) — коммит 93e0c05
- [x] A2.5 POST /api/admin/publish-all + publish_all() на старте — коммит 6cd3b94
- [x] A2.6 content-sync: exclude annotations + chown — коммит 7c28f3d (патч и entrypoint.sh — там был дублирующий bash-rsync)
- [x] A2.7 export_annotations.py + флаг --annotations-from-md в build_website.py — коммит 2338125
- [ ] A2.8 Документация и конфиги (docker-compose, .env.sample, README, STATE_OVERVIEW)

## Часть B — адресация страниц

- [ ] B.1 Фикс pageKey в редакторе (main.js / redpen-editor-bootstrap.js)
- [ ] B.2 Генератор манифеста scripts/generate_page_manifest.py
- [ ] B.3 Просмотрщик: манифест + ?p=
- [ ] B.4 API: _validate_page_key вместо _validate_page_num
- [ ] B.5 Мелочи (корневой metadata.json, доки)

## Деплой

- [ ] Показать пользователю готовое локально состояние, дождаться подтверждения
- [ ] Runbook A выполнен на проде
- [ ] Runbook B выполнен на проде

---

## Заметки по ходу работы

(добавляются по мере выполнения)
