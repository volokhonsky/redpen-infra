# RedPen Deployment Summary - 2026-05-17

## ✅ УСПЕШНОЕ РАЗВЕРТЫВАНИЕ

**Сервер полностью работает:**
- 🌐 **Frontend**: https://medinsky.net (HTTP/2 200 ✓)
- 🔧 **API**: https://api.medinsky.net/api/health ({"status":"ok"} ✓)
- 🪝 **Webhook**: https://medinsky.net/.hooks/redpen-publish (настроен ✓)

---

## 📊 Финальная конфигурация

### Сервер
- **IP**: 70.34.202.231
- **ОС**: Ubuntu 24.04.4 LTS
- **Docker**: 29.5.0
- **Docker Compose**: v5.1.3

### Сервисы (все запущены)
| Сервис | Статус | Порты | Функция |
|--------|--------|-------|---------|
| caddy | ✅ Up | 80, 443 | Reverse proxy + HTTPS (Let's Encrypt) |
| frontend | ✅ Up | 80 (internal) | Nginx - статический сайт |
| api | ✅ Up | 8080 (internal) | FastAPI - обработка аннотаций |
| content-sync | ✅ Up | 9000 (internal) | Webhook + синхронизация с GitHub |

### Домены
- `medinsky.net` → frontend (nginx)
- `api.medinsky.net` → API (FastAPI)
- `medinsky.net/.hooks/redpen-publish` → content-sync webhook

---

## 🔐 Секреты и ключи

### SSH Deploy Key (GitHub → redpen-publish)
**Публичный ключ:**
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDZyVM/SDZL2OzSW2M5d2idlIGW1e+wISVwcicVlp4ir redpen-deploy@medinsky.net
```
**Расположение на сервере:**
- Приватный: `/root/apps/redpen/secrets/content-ssh/id_ed25519` (chmod 600)
- Публичный: `/root/apps/redpen/secrets/content-ssh/id_ed25519.pub` (chmod 644)

**Добавлен в GitHub:**
- Репозиторий: `volokhonsky/redpen-publish`
- Права: Read-only

### Webhook Secret
```
WEBHOOK_SECRET=XOw44ZgxE1GcyuDRkzmoSvSAeR7rSh5lcDraWoaWRq4=
```
**Расположение**: `/root/apps/redpen/infra/.env.secrets` (chmod 600)

**Настроен в GitHub:**
- Репозиторий: `volokhonsky/redpen-publish`
- URL: `https://medinsky.net/.hooks/redpen-publish`
- Content type: `application/json`
- Events: push

---

## 📁 Структура на сервере

```
/root/apps/redpen/
├── config/                         # Конфигурационные файлы (не перезатираются)
│   ├── docker-compose.yml          # Конфигурация Docker Compose
│   ├── .env                        # Переменные окружения
│   ├── .env.secrets                # Секреты (не в Git)
│   ├── caddy/
│   │   └── Caddyfile               # Конфигурация Caddy
│   ├── scripts/                    # Python скрипты API
│   ├── frontend/                   # Dockerfile для frontend
│   ├── content-sync/               # Dockerfile и скрипты синхронизации
│   └── templates/                  # Шаблоны
├── data/
│   └── redpen-content/             # Клонированный репозиторий (изолирован)
└── secrets/
    └── content-ssh/                # SSH ключи для GitHub
        ├── id_ed25519              # Приватный ключ
        ├── id_ed25519.pub          # Публичный ключ
        └── known_hosts             # GitHub host key
```

---

## 🔄 Volumes (Docker)

| Volume | Назначение | Монтируется в |
|--------|-----------|---------------|
| `redpen_public` | Статические файлы сайта | content-sync:/srv/public (rw)<br>frontend:/usr/share/nginx/html (ro) |
| `caddy_data` | Данные Caddy (сертификаты) | caddy:/data |
| `caddy_config` | Конфиг Caddy | caddy:/config |

---

## 🐛 Известные проблемы и решения

### ✅ РЕШЕНО: Конфликт директорий
**Проблема**: Content-sync клонировал репозиторий в ту же директорию, где хранятся конфигурационные файлы, что приводило к их перезаписи при `docker compose down`.

**Решение**: Разделены директории:
- Конфигурация: `/root/apps/redpen/config/` (docker-compose.yml, .env, Caddyfile, Dockerfiles)
- Данные: `/root/apps/redpen/data/redpen-content/` (клонированный репозиторий content-sync)

**Миграция на сервере**:
```bash
# 1. Остановить контейнеры
cd /root/apps/redpen/infra
docker compose down

# 2. Создать новую структуру директорий
mkdir -p /root/apps/redpen/config
mkdir -p /root/apps/redpen/data/redpen-content

# 3. Переместить конфигурационные файлы
mv /root/apps/redpen/infra/* /root/apps/redpen/config/
rmdir /root/apps/redpen/infra

# 4. Загрузить обновленный docker-compose.yml
# (из локальной машины)
cd /Users/Vladimir.Volokhonsky/Documents/redpen
scp docker-compose.yml .env root@70.34.202.231:/root/apps/redpen/config/
scp caddy/Caddyfile root@70.34.202.231:/root/apps/redpen/config/caddy/
scp -r scripts frontend content-sync templates root@70.34.202.231:/root/apps/redpen/config/

# 5. Восстановить .env.secrets
ssh root@70.34.202.231 "echo 'WEBHOOK_SECRET=XOw44ZgxE1GcyuDRkzmoSvSAeR7rSh5lcDraWoaWRq4=' > /root/apps/redpen/config/.env.secrets && chmod 600 /root/apps/redpen/config/.env.secrets"

# 6. Запустить с новой конфигурацией
cd /root/apps/redpen/config
docker compose up -d
```

---

## 📋 Команды для управления

### Проверка статуса
```bash
ssh root@70.34.202.231
cd /root/apps/redpen/config
docker compose ps
```

### Просмотр логов
```bash
# Все сервисы
docker compose logs -f

# Конкретный сервис
docker compose logs -f caddy
docker compose logs -f api
docker compose logs -f content-sync
docker compose logs -f frontend
```

### Перезапуск сервисов
```bash
# Все сервисы
docker compose restart

# Конкретный сервис
docker compose restart caddy
```

### Остановка и запуск
```bash
# Остановить (конфиги больше не удаляются!)
docker compose down

# Запустить
docker compose up -d
```

### Проверка работоспособности
```bash
# HTTPS сайт
curl -I https://medinsky.net

# API health
curl https://api.medinsky.net/api/health

# Webhook (должен вернуть 401 без правильной подписи)
curl -i -X POST https://medinsky.net/.hooks/redpen-publish \
  -H 'Content-Type: application/json' \
  -H 'X-GitHub-Event: push' \
  -d '{}'
```

---

## 🚀 Автоматизация обновлений

### Через GitHub Webhook
1. Пушите изменения в `volokhonsky/redpen-publish`
2. GitHub автоматически отправляет webhook на сервер
3. Content-sync получает уведомление, делает `git pull` и обновляет `/srv/public`
4. Frontend (nginx) автоматически отдает новые файлы

### Ручное обновление
```bash
ssh root@70.34.202.231
cd /root/apps/redpen/config
docker compose restart content-sync
```

---

## ✅ Проверочный список развертывания

- [x] DNS записи настроены (medinsky.net → 70.34.202.231)
- [x] Docker и Docker Compose установлены
- [x] SSH ключ создан и добавлен в GitHub Deploy Keys
- [x] GitHub Webhook настроен с правильным секретом
- [x] HTTPS сертификаты получены автоматически (Let's Encrypt)
- [x] Frontend доступен на https://medinsky.net
- [x] API доступен на https://api.medinsky.net
- [x] Webhook работает на https://medinsky.net/.hooks/redpen-publish
- [x] Content-sync успешно клонировал репозиторий
- [x] Все Docker контейнеры запущены и работают

---

## 📞 Поддержка и устранение неполадок

### Сайт не отвечает
1. Проверить статус контейнеров: `docker compose ps`
2. Проверить логи Caddy: `docker compose logs caddy`
3. Убедиться что порты 80 и 443 открыты: `netstat -tulpn | grep -E ':(80|443)'`

### 403 Forbidden от nginx
1. Проверить что файлы есть в volume: `docker exec redpen-frontend-1 ls -la /usr/share/nginx/html/`
2. Проверить что content-sync опубликовал файлы: `docker exec redpen-content-sync-1 ls -la /srv/public/`
3. Перезапустить content-sync: `docker compose restart content-sync`

### Caddy не запускается
1. Проверить что Caddyfile существует: `ls -la /root/apps/redpen/config/caddy/Caddyfile`
2. Проверить логи: `docker logs redpen-caddy-1`
3. Восстановить Caddyfile и перезапустить

### Content-sync не может склонировать репозиторий
1. Проверить SSH ключ: `ssh root@70.34.202.231 "cat /root/apps/redpen/secrets/content-ssh/id_ed25519.pub"`
2. Убедиться что ключ добавлен в GitHub Deploy Keys
3. Проверить логи: `docker compose logs content-sync`

---

## 🎉 Успешно развернуто!

**Дата**: 2026-05-17
**Время развертывания**: ~2 часа
**Статус**: Полностью работает
