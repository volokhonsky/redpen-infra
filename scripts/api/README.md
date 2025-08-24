# RedPen API (Step 1/5)

Минимальный REST API и Docker-образ.

Сборка:
- docker build -t redpen-api:dev scripts/api

Локальный запуск (с примонтированными данными):
- docker run --rm -p 8080:8080 -e STORAGE_DIR=/data -e LOG_LEVEL=INFO -e CORS_ALLOW_ORIGINS="*" -v "$(pwd)/.data:/data" redpen-api:dev

Эндпоинты:
- GET http://localhost:8080/api/health → {"status":"ok"}
- POST http://localhost:8080/api/store (JSON-объект в теле) → сохраняет в /data/inbox/YYYYMMDD/uuid.json, ответ {"status":"stored","path":"inbox/YYYYMMDD/uuid.json"}

Примеры:
- curl http://localhost:8080/api/health
- curl -X POST http://localhost:8080/api/store -H 'Content-Type: application/json' -d '{"hello":"world"}'
