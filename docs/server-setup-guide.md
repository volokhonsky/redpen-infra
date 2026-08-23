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

### 11. Аналитика: Matomo + счётчики контента

Раздел актуален на 2026-08-23 (в отличие от остального документа — сверяйте с
`docs/deployment-log.md`). Замысел и обоснование — `docs/analytics-plan-2026-08.md`,
что при этом хранится о людях — `docs/anonymity-model.md`.

**Главное правило: счётчика на сайте нет и быть не может.** Просмотрщик не
обращается к сети — это главный инвариант проекта (сайт работает с флешки).
Любой счётчик, включая «безджаваскриптовый» пиксель, его ломает. Данные берутся
только из access-лога, постфактум.

#### 11.1 Каталог для access-лога

```bash
mkdir -p /root/apps/redpen/data/caddy-logs
chmod 755 /root/apps/redpen/data/caddy-logs
```

Каталог монтируется в Caddy на запись (`/logs`) и в контейнер API на чтение
(`/var/log/caddy`).

#### 11.2 Пароли базы Matomo

`MARIADB_PASSWORD` и `MATOMO_DATABASE_PASSWORD` — **одно и то же значение**:

```bash
cd /root/apps/redpen/infra
P=$(openssl rand -hex 24)
{ echo "MARIADB_ROOT_PASSWORD=$(openssl rand -hex 24)"
  echo "MARIADB_PASSWORD=$P"
  echo "MATOMO_DATABASE_PASSWORD=$P"; } >> .env.secrets
```

#### 11.3 Access-лог в Caddyfile

Внутри блока хоста сайта (`{$DOMAIN} { … }`), НЕ в глобальном и НЕ на хосте API —
на хосте API следы участников закрытого круга:

```
  log {
    output file /logs/access.log {
      roll_size 20mb
      roll_keep 5
      roll_keep_for 72h
      mode 0644
    }
    format json
  }
```

`mode 0644` обязателен: по умолчанию Caddy создаёт лог с правами 0600 от root, а
читает его контейнер API под uid 10001. Опция появилась в Caddy 2.7; если сборка
старее, строку убрать и запускать разбор от root (`docker exec -u 0 …`).

**Проверить конфигурацию до перезапуска** — битая роняет оба хоста, а не только
лог:

```bash
docker exec redpen-caddy-1 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

#### 11.4 Запуск сервисов

```bash
cd /root/apps/redpen/infra
docker compose up -d matomo-db matomo
docker compose up -d --build api
docker compose up -d caddy
```

После каждого шага сайт и API должны отвечать 200. Том `redpen_stats`
(`/var/redpen-stats`) создаётся сборкой API: каталог делается и отдаётся uid
10001 **до** `USER app`, иначе запись падает — свежий том наследует владельца
точки монтирования.

Порт Matomo — `127.0.0.1:8081`, **наружу он не выставлен**: это PHP-приложение с
админкой, и отчёты о читателях не должны быть публичными.

#### 11.5 Установка Matomo

С рабочей машины:

```bash
ssh -L 8081:localhost:8081 root@70.34.202.231
```

Не закрывая сессию, открыть `http://localhost:8081`. Реквизиты базы мастер
подставляет из окружения; если спросит — сервер `matomo-db`, пользователь
`matomo`, база `matomo`, пароль из `.env.secrets`.

- сайт — `https://medinsky.net`;
- **шаг «JavaScript Tracking-код» пропустить**, ничего на сайт не вставлять;
- учётную запись суперпользователя завести на **неличный** адрес (например
  `admin@medinsky.net`): она лежит в базе на том же диске, который изымают;
- сразу после установки: Администрирование → Приватность — обезличивание
  адресов и срок хранения сырых визитов. По умолчанию Matomo хранит больше,
  чем нужно.

#### 11.6 Токен для импортёра

Создать в Matomo: Администрирование → Личное → Токены безопасности. Записать в
файл, не проводя токен через командную строку (аргументы видны в `ps`):

```bash
ssh root@70.34.202.231 'cat > /root/matomo-auth.cfg'
```

Ввести две строки, затем `Enter` и `Ctrl-D`:

```
[auth]
token_auth = ТОКЕН
```

Перенести в том и стереть копию с хоста:

```bash
docker cp /root/matomo-auth.cfg redpen-api-1:/var/redpen-stats/matomo-auth.cfg
docker exec -u 0 redpen-api-1 sh -c 'chown 10001:10001 /var/redpen-stats/matomo-auth.cfg && chmod 600 /var/redpen-stats/matomo-auth.cfg'
shred -u /root/matomo-auth.cfg
```

Формат INI с секцией `[auth]` — так его читает `import_logs.py`; он же ругается,
если права на файле слишком открытые.

#### 11.7 Крон

```bash
install -m 755 /root/apps/redpen/infra/scripts/ops/redpen-stats /etc/cron.hourly/redpen-stats
```

Раз в час: счётчики контента → экспорт лога → импорт в Matomo → сдвиг позиции.
Порядок важен: позиция двигается **только после успешного импорта**, потому что
`import_logs.py` не возобновляемый и упавший импорт иначе унёс бы строки
навсегда. В час ночи дополнительно чистятся записи старше 400 дней.

#### 11.8 Проверка

```bash
sh /etc/cron.hourly/redpen-stats
```

Ожидаемо: `requests imported to 1 sites` и `matomo-export: позиция сдвинута`.
Отчёт по контенту (параграфы, спрос против покрытия, `?only=`):

```bash
docker exec redpen-api-1 python3 /app/scripts/ops/redpen_stats.py report --days 7
```

В Matomo отчёт по адресам страниц должен разворачиваться в иерархию
`книга → §N → страница`: параграф подставляется в адрес при подготовке лога.

#### Что в аналитику не попадает

Проверяется тестами (`tests/test_analytics.py`), а не обещанием:

- запросы к `/app/`, `/cabinet/`, `/api/`, `/.hooks/` и любой адрес с `?editor=1`;
- **запросы со ссылающейся страницы `/app/` или `/cabinet/`** — предпросмотр в
  редакторе грузит настоящую страницу читателя в iframe, ещё и с `?only=<id>`;
  по адресу это неотличимо от чтения, и без такой проверки собственная правка
  засчитывалась бы как визит, а разбор комментария — как пересылку;
- access-лог включён только на хосте сайта, на `api.medinsky.net` его нет.

Если после установки в базе оказался проверочный трафик, его можно убрать:
`TRUNCATE` таблиц `matomo_log_link_visit_action`, `matomo_log_visit`,
`matomo_log_action`, `DROP` таблиц `matomo_archive_*`, `DELETE FROM hits` в
`stats.db`. Позиция в логе при этом остаётся сдвинутой, повторно он не приедет.

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
- [ ] Аналитика (раздел 11): каталог логов создан, `log` добавлен в блок хоста
      сайта и конфигурация проверена `caddy validate`
- [ ] Matomo установлен, счётчик на сайт НЕ ставился, настройки приватности
      выставлены, суперпользователь на неличном адресе
- [ ] Токен импортёра лежит в `/var/redpen-stats/matomo-auth.cfg` (600, uid 10001)
- [ ] `/etc/cron.hourly/redpen-stats` установлен и прогнан вручную
