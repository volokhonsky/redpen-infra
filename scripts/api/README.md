# RedPen API (Step 1/5)

Минимальный REST API и Docker-образ.

Сборка:
- docker build -t redpen-api:dev scripts/api

Локальный запуск (с примонтированными данными):
- docker run --rm -p 8080:8080 -e STORAGE_DIR=/data -e LOG_LEVEL=INFO -e CORS_ALLOW_ORIGINS="*" -v "$(pwd)/.data:/data" redpen-api:dev

Эндпоинты:
- GET http://localhost:8080/api/health → {"status":"ok"}
- POST http://localhost:8080/api/store (JSON-объект в теле) → сохраняет в /data/inbox/YYYYMMDD/uuid.json, ответ {"status":"stored","path":"inbox/YYYYMMDD/uuid.json"}
- GET http://localhost:8080/api/pages/{pageId} → вернуть JSON страницы с serverPageSha
- POST http://localhost:8080/api/pages/{pageId}/annotations → принять {annType,text,coords?[,id?]}, сохранить/добавить, вернуть {id,serverPageSha}
- PUT http://localhost:8080/api/pages/{pageId}/annotations/{id} → обновить аннотацию, вернуть {id,serverPageSha}

Примеры:
- curl http://localhost:8080/api/health
- curl -X POST http://localhost:8080/api/store -H 'Content-Type: application/json' -d '{"hello":"world"}'
- curl http://localhost:8080/api/pages/007
- curl -X POST http://localhost:8080/api/pages/007/annotations -H 'Content-Type: application/json' -d '{"annType":"general","text":"Привет"}'
- curl -X PUT http://localhost:8080/api/pages/007/annotations/srv-123456 -H 'Content-Type: application/json' -d '{"annType":"comment","text":"Текст","coords":[100,200]}'

Развёртывание (deploy):
- Создайте .env в корне репозитория с переменными приложения, например:
  STORAGE_DIR=/data
  LOG_LEVEL=INFO
  CORS_ALLOW_ORIGINS=*
- Запуск деплоя: ./scripts/deploy/deploy-api.sh
- Переопределение параметров деплоя: переменные окружения перед запуском, например:
  IMAGE_NAME=redpen-api:v1 PORT=9090 DATA_DIR=/var/redpen-data ./scripts/deploy/deploy-api.sh
