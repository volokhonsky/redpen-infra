# Прогресс: Этап 2 + адресация страниц (docs/agent-instructions-stage-2.md)

Журнал ведётся по ходу работы агента, чтобы можно было продолжить после
остановки (usage limit). Каждый пункт — коммит на `main` в локальном репо.
Деплой на прод — только после подтверждения пользователем (см. runbook в
исходном плане).

Статусы: `[ ]` не начато, `[~]` в процессе, `[x]` готово (закоммичено, тесты зелёные).

## Часть A — SQLite + публикация

- [ ] A2.1 Схема БД: annotations + annotation_history (db.py)
- [ ] A2.2 Публикатор scripts/api/publisher.py
- [ ] A2.3 Импорт scripts/api/import_annotations.py
- [ ] A2.4 Перевод editor-эндпоинтов на БД (main.py, удаление storage.py page-функций)
- [ ] A2.5 POST /api/admin/publish-all + publish_all() на старте
- [ ] A2.6 content-sync: exclude annotations + chown
- [ ] A2.7 export_annotations.py + флаг --annotations-from-md в build_website.py
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
