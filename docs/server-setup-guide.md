# RedPen Server Setup Guide

## Требования

### Системные требования
- Ubuntu/Debian сервер
- Docker и Docker Compose
- Git
- SSH доступ
- Открытые порты: 80, 443

### DNS настройки
- `medinsky.net` → 70.34.202.231
- `api.medinsky.net` → 70.34.202.231

---

## Пошаговая инструкция по установке

### 1. Подготовка сервера

#### 1.1 Подключение к серверу
```bash
ssh root@70.34.202.231
```

#### 1.2 Обновление системы
```bash
apt update && apt upgrade -y
```

#### 1.3 Установка необходимых пакетов
```bash
# Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Docker Compose plugin
apt-get install -y docker-compose-plugin

# Git
apt-get install -y git

# Проверка установки
docker --version
docker compose version
git --version
```

---

### 2. Создание структуры каталогов

```bash
mkdir -p /root/apps/redpen/{infra,secrets/content-ssh}
cd /root/apps/redpen
```

Структура:
```
/root/apps/redpen/
├── infra/              # Основные файлы проекта
│   ├── docker-compose.yml
│   ├── .env
│   ├── .env.secrets
│   └── caddy/          # Конфигурация Caddy
└── secrets/
    └── content-ssh/    # SSH ключи для GitHub
        ├── id_ed25519
        ├── id_ed25519.pub
        └── known_hosts
```

---

### 3. Настройка GitHub Deploy Key

#### 3.1 Создание SSH ключа
```bash
ssh-keygen -t ed25519 -f /root/apps/redpen/secrets/content-ssh/id_ed25519 -N "" -C "redpen-deploy@medinsky.net"
```

#### 3.2 Настройка прав доступа
```bash
chmod 700 /root/apps/redpen/secrets/content-ssh
chmod 600 /root/apps/redpen/secrets/content-ssh/id_ed25519
chmod 644 /root/apps/redpen/secrets/content-ssh/id_ed25519.pub
```

#### 3.3 Добавление GitHub в known_hosts
```bash
ssh-keyscan github.com > /root/apps/redpen/secrets/content-ssh/known_hosts
chmod 644 /root/apps/redpen/secrets/content-ssh/known_hosts
```

#### 3.4 Добавление ключа в GitHub
1. Вывести публичный ключ:
   ```bash
   cat /root/apps/redpen/secrets/content-ssh/id_ed25519.pub
   ```

2. В GitHub:
   - Перейти в репозиторий `volokhonsky/redpen-publish`
   - Settings → Deploy keys → Add deploy key
   - Title: `RedPen Server Deploy Key`
   - Key: вставить содержимое `id_ed25519.pub`
   - Права: Read-only (галочку на Write НЕ ставить)

---

### 4. Настройка конфигурации

#### 4.1 Создание .env файла
```bash
cat > /root/apps/redpen/infra/.env << 'EOF'
# Domain configuration
DOMAIN=medinsky.net
API_SUBDOMAIN=api
API_HOST=${API_SUBDOMAIN}.${DOMAIN}
FRONTEND_ORIGIN=https://${DOMAIN}
API_BASE_URL=https://${API_HOST}

# API service runtime
STORAGE_DIR=/var/redpen-data
LOG_LEVEL=info
CORS_ALLOW_ORIGINS=https://medinsky.net

# Content sync configuration
CONTENT_GIT_REPO=git@github.com:volokhonsky/redpen-publish.git
CONTENT_GIT_REF=main
EOF
```

#### 4.2 Создание .env.secrets файла
```bash
# Генерация случайного WEBHOOK_SECRET
WEBHOOK_SECRET=$(openssl rand -base64 32)

cat > /root/apps/redpen/infra/.env.secrets << EOF
WEBHOOK_SECRET=${WEBHOOK_SECRET}
EOF

chmod 600 /root/apps/redpen/infra/.env.secrets

# Сохранить секрет для настройки GitHub webhook
echo "WEBHOOK_SECRET для GitHub webhook:"
echo $WEBHOOK_SECRET
```

**ВАЖНО:** Сохраните этот WEBHOOK_SECRET - он понадобится для настройки GitHub webhook!

---

### 5. Копирование файлов проекта

#### 5.1 Клонирование репозитория infra
```bash
cd /root/apps/redpen
git clone git@github.com:volokhonsky/redpen-infra.git infra-temp
cp -r infra-temp/* infra/
rm -rf infra-temp
```

Или копирование файлов с локальной машины:
```bash
# На локальной машине
cd /Users/Vladimir.Volokhonsky/Documents/redpen
scp -r docker-compose.yml caddy scripts frontend content-sync templates root@70.34.202.231:/root/apps/redpen/infra/
```

---

### 6. Настройка Caddyfile

Файл должен находиться в `/root/apps/redpen/infra/caddy/Caddyfile`

Пример конфигурации:
```caddy
{$DOMAIN} {
    reverse_proxy frontend:80
}

{$API_HOST} {
    reverse_proxy api:8000
}

{$DOMAIN}/.hooks/* {
    reverse_proxy content-sync:9000
}
```

---

### 7. Настройка GitHub Webhook

1. Перейти в репозиторий `volokhonsky/redpen-publish`
2. Settings → Webhooks → Add webhook
3. Настройки:
   - **Payload URL:** `https://medinsky.net/.hooks/redpen-publish`
   - **Content type:** `application/json`
   - **Secret:** использовать WEBHOOK_SECRET из .env.secrets
   - **Events:** Just the push event
   - **Active:** ✓

---

### 8. Запуск системы

#### 8.1 Проверка конфигурации Docker Compose
```bash
cd /root/apps/redpen/infra
docker compose config
```

#### 8.2 Проверка Caddyfile
```bash
docker compose run --rm caddy caddy validate --config /etc/caddy/Caddyfile
```

#### 8.3 Запуск сервисов
```bash
docker compose up -d --build
```

#### 8.4 Проверка статуса
```bash
docker compose ps
docker compose logs -f
```

---

### 9. Проверка работоспособности

#### 9.1 Проверка HTTPS
```bash
curl -I https://medinsky.net
```

#### 9.2 Проверка API
```bash
curl https://api.medinsky.net/api/health
```

#### 9.3 Проверка webhook endpoint
```bash
curl -i -X POST https://medinsky.net/.hooks/redpen-publish \
  -H 'Content-Type: application/json' \
  -H 'X-GitHub-Event: push' \
  -d '{}'
```
Ожидается: 401 Unauthorized (это нормально без правильной подписи)

#### 9.4 Тест GitHub webhook
В GitHub репозитории redpen-publish:
- Settings → Webhooks → выбрать webhook → Recent Deliveries → Redeliver

Должен вернуться статус 200 OK

---

### 10. Обслуживание

#### Просмотр логов
```bash
cd /root/apps/redpen/infra

# Все сервисы
docker compose logs -f

# Конкретный сервис
docker compose logs -f api
docker compose logs -f content-sync
docker compose logs -f caddy
```

#### Перезапуск сервисов
```bash
# Все сервисы
docker compose restart

# Конкретный сервис
docker compose restart api
```

#### Обновление конфигурации
```bash
# После изменения .env или docker-compose.yml
docker compose up -d --build

# Перезагрузка Caddy конфигурации
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

#### Остановка системы
```bash
docker compose down
```

#### Полная очистка (с удалением данных)
```bash
docker compose down -v
```

---

## Устранение проблем

### Проблема: Сервисы не запускаются
1. Проверить логи: `docker compose logs`
2. Проверить конфигурацию: `docker compose config`
3. Проверить доступность портов: `netstat -tulpn | grep -E ':(80|443)'`

### Проблема: SSL сертификаты не получены
1. Проверить DNS записи: `dig medinsky.net`, `dig api.medinsky.net`
2. Проверить логи Caddy: `docker compose logs caddy`
3. Убедиться, что порты 80 и 443 открыты в firewall

### Проблема: content-sync не может склонировать репозиторий
1. Проверить SSH ключ в контейнере: `docker compose exec content-sync ls -la /root/.ssh`
2. Проверить права доступа к ключам
3. Проверить, что Deploy Key добавлен в GitHub
4. Проверить логи: `docker compose logs content-sync`

### Проблема: Webhook возвращает 401
Это нормально при тестовом запросе без подписи. GitHub webhook с правильным секретом должен возвращать 200.

---

## Безопасность

### Рекомендации:
1. **Секреты:** Никогда не коммитить `.env.secrets` в Git
2. **SSH ключи:** Хранить только на сервере, не копировать локально
3. **Firewall:** Открыть только необходимые порты (22, 80, 443)
4. **Обновления:** Регулярно обновлять Docker образы и систему
5. **Backup:** Регулярно создавать резервные копии `/var/redpen-data`

### Настройка Firewall (UFW)
```bash
ufw allow 22/tcp   # SSH
ufw allow 80/tcp   # HTTP
ufw allow 443/tcp  # HTTPS
ufw enable
```

---

## Проверочный список перед запуском

- [ ] DNS записи настроены для medinsky.net и api.medinsky.net
- [ ] Docker и Docker Compose установлены
- [ ] Структура каталогов создана
- [ ] SSH ключ создан и добавлен как Deploy Key в GitHub
- [ ] known_hosts содержит github.com
- [ ] .env файл создан с правильными значениями
- [ ] .env.secrets создан с WEBHOOK_SECRET
- [ ] Caddyfile создан и валиден
- [ ] docker-compose.yml и все необходимые файлы скопированы
- [ ] GitHub webhook настроен с правильным секретом
- [ ] Порты 80 и 443 открыты в firewall
