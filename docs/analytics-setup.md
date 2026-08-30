# Аналитика: Matomo + счётчики контента

Воспроизводимая инструкция по установке. Написана 2026-08-23; вынесена из
`docs/history/server-setup-guide.md` 2026-08-30, потому что тот документ
исторический целиком, а этот раздел — живой, и внутри «не обновлять» он уже
успел разойтись с кодом.

Замысел и обоснование — `docs/analytics-plan-2026-08.md`, что при этом хранится
о людях — `docs/anonymity-model.md`.

**Главное правило: счётчика на сайте нет и быть не может.** Просмотрщик не
обращается к сети — это главный инвариант проекта (сайт работает с флешки).
Любой счётчик, включая «безджаваскриптовый» пиксель, его ломает. Данные берутся
только из access-лога, постфактум.

## 1. Каталог для access-лога

```bash
mkdir -p /root/apps/redpen/data/caddy-logs
chmod 755 /root/apps/redpen/data/caddy-logs
```

Каталог монтируется в Caddy на запись (`/logs`) и в контейнер API на чтение
(`/var/log/caddy`).

## 2. Пароли базы Matomo

`MARIADB_PASSWORD` и `MATOMO_DATABASE_PASSWORD` — **одно и то же значение**:

```bash
cd /root/apps/redpen/infra
P=$(openssl rand -hex 24)
{ echo "MARIADB_ROOT_PASSWORD=$(openssl rand -hex 24)"
  echo "MARIADB_PASSWORD=$P"
  echo "MATOMO_DATABASE_PASSWORD=$P"; } >> .env.secrets
```

## 3. Access-лог в Caddyfile

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

## 4. Запуск сервисов

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

Порт Matomo — `127.0.0.1:8081`: контейнер сам в интернет не смотрит, и на этом
шаге открывается только пробросом ssh. Доступ снаружи (`stats.medinsky.net`,
под двумя замками) добавлен 2026-08-25 — см. раздел 9; до него в этом месте
стояло «наружу не выставлен» без оговорок, и это разошлось с `caddy/Caddyfile`.

## 5. Установка Matomo

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

Если дашборд нужен снаружи, а не через проброс, — раздел 11.9.

## 6. Токен для импортёра

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

## 7. Крон

```bash
install -m 755 /root/apps/redpen/infra/scripts/ops/redpen-stats /etc/cron.hourly/redpen-stats
```

Раз в час: счётчики контента → экспорт лога → импорт в Matomo → сдвиг позиции.
Порядок важен: позиция двигается **только после успешного импорта**, потому что
`import_logs.py` не возобновляемый и упавший импорт иначе унёс бы строки
навсегда. В час ночи дополнительно чистятся записи старше 400 дней.

## 8. Проверка

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

## 9. Доступ к дашборду снаружи

По умолчанию Matomo слушает только `127.0.0.1:8081` и открывается пробросом ssh.
Если нужен доступ из браузера без ssh — отдельный хост под двумя замками.

**Почему два.** Снаружи Matomo — это не только форма входа, но и **ручка
трекера**, открытая всем желающим налить выдуманных визитов, плюс PHP-админка
над базой, где лежат адреса читателей. `basic_auth` в Caddy отсекает запрос до
того, как он дойдёт до PHP. Наш импорт идёт по внутренней сети docker
(`http://matomo/`) и пароля не видит.

1. **DNS**: запись `A` — `stats` → адрес сервера, **прокси выключен** (у
   Cloudflare серое облако): оранжевое поставило бы посредника в середину.
2. **Пароль**. Хеш генерируется интерактивно, пароль не попадает ни в историю,
   ни в аргументы:

   ```bash
   docker run --rm -it caddy:alpine caddy hash-password
   ```

3. **`/root/apps/redpen/infra/.env.stats`** (права 600) — отдельно от
   `.env.secrets`: контейнеру Caddy незачем видеть перец и токены.

   ```
   STATS_HOST=stats.medinsky.net
   STATS_USER=redpen
   STATS_PASSWORD_HASH=$$2a$$14$$...
   ```

   > **Каждый `$` в хеше удваивается.** Docker Compose интерполирует значения из
   > `env_file`, и `$2a$14$LhBjQ...` он принимает за подстановку переменной: до
   > Caddy доезжает огрызок, пароль молча не работает. Признак беды — в выводе
   > `docker compose config` строка «The "LhBjQ…" variable is not set».
   > Проверка после запуска: `docker exec redpen-caddy-1 printenv
   > STATS_PASSWORD_HASH | cut -c1-7` должно дать `$2a$14$`.

4. **Блок хоста в Caddyfile** — `basic_auth` перед `reverse_proxy matomo:80`,
   заголовки безопасности и `X-Robots-Tag: noindex` (см. `caddy/Caddyfile`).
   Конфигурацию проверить до перезапуска; в проверку надо передать и env-файл:

   ```bash
   docker run --rm -v /root/apps/redpen/config/caddy:/etc/caddy:ro \
     --env-file /root/apps/redpen/infra/.env.stats \
     -e DOMAIN=medinsky.net -e API_HOST=api.medinsky.net \
     caddy:alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
   ```

5. **Matomo за прокси** — в `config/config.ini.php`, секция `[General]`:

   ```
   trusted_hosts[] = "stats.medinsky.net"
   assume_secure_protocol = 1
   ```

   Без первого Matomo не отвечает на незнакомом хосте; без второго строит
   абсолютные ссылки по `http://` и ломает https-страницу смешанным контентом.
   Побочный эффект: проброс `http://localhost:8081` после этого работает плохо
   (ссылки ведут на https) — это осознанный размен в пользу внешнего доступа.

6. **Проверка снаружи**:

   ```bash
   curl -sI https://stats.medinsky.net/ | head -3
   curl -s -o /dev/null -w "%{http_code}\n" "https://stats.medinsky.net/matomo.php?idsite=1&rec=1"
   ```

   Ожидаемо `401` в обоих случаях, в заголовках — `WWW-Authenticate: Basic` и
   `X-Robots-Tag: noindex`. С паролем — форма входа Matomo.

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
