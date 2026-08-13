# Запуск Claude-агента в локальном Docker (на личной подписке)

Инструкция для запуска headless-агента Claude Code в изолированном Docker-контейнере
так, чтобы расход шёл на **личную Claude-подписку**, а не на рабочую (JetBrains).
Проверено 2026-08-08.

> Область: это «дорожка Claude Code» (задачи, завязанные на Claude — генерация и
> деплой аннотаций RedPen и т. п.). Мультимодельная дорожка (разные провайдеры через
> LiteLLM) — отдельная история, см. раздел «Дальше».

## Зачем это нужно

На рабочем ноутбуке `~/.claude/settings.json` заворачивает весь трафик Claude через
локальный JetBrains-прокси `central`:

```
apiKeyHelper       = central proxy start --ensure-updated --return-key
env.ANTHROPIC_BASE_URL = http://127.0.0.1:19516/wire/.../claude-code/anthropic
```

Из-за этого любой `claude` на хосте (даже с личным токеном) бьётся на **рабочую**
подписку — маршрут, а не токен, определяет счёт. Так дважды случайно ушёл ночной
прогон §15–§18 (2026-08-08) на рабочий аккаунт.

Контейнер решает это радикально: внутри **нет** `~/.claude/settings.json`, нет
`central`, а `127.0.0.1` — это сам контейнер (хостовый прокси недоступен). Значит
`claude` идёт напрямую в `https://api.anthropic.com` с тем токеном, что мы дали,
и расход падает на личную подписку.

## Что лежит в репозитории

- `docker/claude-agent/Dockerfile` — образ `claude-agent:latest` (node:20-slim +
  git/ssh/python3/Pillow + CLI `@anthropic-ai/claude-code`; `ANTHROPIC_BASE_URL`
  прибит к настоящему API).
- `docker/claude-agent/run-agent.sh` — раннер: очищённое окружение, личный токен,
  монтирование репозитория и ssh-ключа, логирование.

Рантайм-каталог с **секретом и логами** — `~/.claude-agent-docker/` (вне git):
- `oauth-token` — личный OAuth-токен (0600), создаёшь ты (см. ниже);
- `<label>.log` — логи прогонов.

## Предпосылки

- Docker (проверялось на 27.5.1).
- Личная Claude-подписка ($20) и вход в неё в браузере.
- ssh-ключ `~/.ssh/id_ed25519` без парольной фразы (нужен, если агент будет
  деплоить в прод / пушить в git — оба канала headless проверены).

## 1. Собрать образ

```bash
docker build -t claude-agent:latest /Users/Vladimir.Volokhonsky/Documents/redpen/docker/claude-agent
```

## 2. Разово выпустить ЛИЧНЫЙ токен

`claude setup-token` привязывается к тому аккаунту, под которым ты залогинен в
**браузере**, — поэтому сначала убедись, что там личный claude.ai, а не рабочий.

```bash
claude setup-token
```

Скопируй напечатанный токен (обычно начинается с `sk-ant-oat0…`) и положи в файл 0600
(в чат никому не показывай):

```bash
mkdir -p ~/.claude-agent-docker
umask 077
pbpaste > ~/.claude-agent-docker/oauth-token && chmod 600 ~/.claude-agent-docker/oauth-token
```

Само-проверка, что попал токен, а не строка команды:

```bash
head -c 12 ~/.claude-agent-docker/oauth-token; echo
```

Должно показать начало токена (`sk-ant-oat0…`), а не `pbpaste`/`claude`.

Токен долгоживущий, но не вечный: если preflight начнёт падать («preflight auth
failed»), перевыпусти `claude setup-token` и обнови файл.

## 3. Проверить изоляцию (без единого запроса к модели)

```bash
docker run --rm claude-agent:latest bash -lc '
  claude --version
  env | grep -iE "anthropic|claude" || echo "(только заданное нами)"
  ls -la /root/.claude/ 2>/dev/null || echo "нет ~/.claude — чисто"
  curl -sS -m3 http://127.0.0.1:19516/ >/dev/null 2>&1 && echo "central ДОСТУПЕН ⚠" || echo "central недоступен ✅"
  curl -sS -m8 -o /dev/null -w "api.anthropic.com HTTP %{http_code}\n" -X POST https://api.anthropic.com/v1/messages
'
```

Ожидаемо: только `ANTHROPIC_BASE_URL=https://api.anthropic.com`, нет `~/.claude`,
`central недоступен`, реальный API отвечает `401` (дошли, нужен токен).

## 4. Проверить БИЛЛИНГ (обязательно, до реальной работы)

Крошечный запрос с меткой времени — потом ты сам сверяешь, что расход на личном
аккаунте, а на рабочем в этот момент тихо.

```bash
export CLAUDE_CODE_OAUTH_TOKEN="$(tr -d '[:space:]' < ~/.claude-agent-docker/oauth-token)"
date '+%Y-%m-%d %H:%M:%S %Z (local)  |  UTC %H:%M:%S' -u 2>/dev/null; date '+%Y-%m-%d %H:%M:%S %Z'
docker run --rm -e CLAUDE_CODE_OAUTH_TOKEN -e ANTHROPIC_BASE_URL=https://api.anthropic.com \
  claude-agent:latest bash -lc 'claude -p "reply with exactly one word: pong" --output-format text'
```

Ответ `pong` = авторизация прошла через личный токен. У Claude Pro нет поштучного
журнала запросов, поэтому «где списалось» проверяется косвенно: там, где ты в
прошлый раз видел рабочий расход (портал JetBrains / рабочий аккаунт), в момент
запроса должно быть тихо.

## 5. Запустить задачу

```bash
~/.claude-agent-docker/run-agent.sh <label> <файл_с_промптом> [хост_каталог_репо]
```

- `label` — имя прогона (лог `~/.claude-agent-docker/<label>.log`).
- `файл_с_промптом` монтируется в контейнер как `/prompt.txt`.
- третий аргумент — каталог репозитория (по умолчанию `~/Documents/redpen`),
  монтируется как `/work`.

Раннер: читает токен, гоняет `claude` в `env -i` только с личным
`CLAUDE_CODE_OAUTH_TOKEN` (+ `ANTHROPIC_BASE_URL=https://api.anthropic.com`),
монтирует репозиторий (`/work`), промпт (`/prompt.txt`), ssh-ключ (ro),
прописывает git-identity, добавляет known_hosts для github и прод-сервера.

> **Внимание — пути в промпте.** Внутри контейнера репозиторий доступен как `/work`,
> НЕ как `/Users/Vladimir.Volokhonsky/Documents/redpen`. Промпты, написанные под
> хостовые абсолютные пути (например `~/.redpen-scheduled/para*-prompt.txt`), надо
> адаптировать под `/work` (скрипты — `python3 /work/scripts/...`, контент —
> `/work/redpen-content/...` и т. д.).

Для агента-аннотатора задание руками не пишется — генерируется уже под `/work`:

```bash
python3 scripts/make_agent_prompt.py 22 -o ~/.claude-agent-docker/para22-prompt.txt
zsh ~/.claude-agent-docker/run-agent.sh para22 ~/.claude-agent-docker/para22-prompt.txt
```

Почему: сжатое рукописное задание разошлось с `docs/annotation-agent-prompt.md`
и потеряло правило о ссылках — §20 (2026-08-08) вышел с 42 аннотациями и нулём
ссылок в телах.

## Раннер работает не под root

`claude` отказывается запускаться с `--dangerously-skip-permissions` от root
(«cannot be used with root/sudo privileges»), поэтому `run-agent.sh` создаёт внутри
контейнера пользователя `agent` с **host-uid** и сбрасывает на него привилегии
(`su -w CLAUDE_CODE_OAUTH_TOKEN,ANTHROPIC_BASE_URL`, токен не попадает в argv).
Побочный полезный эффект: файлы, записанные агентом в `/work`, принадлежат тебе,
а не root. ssh-ключ и `known_hosts` монтируются в `/keys` и копируются в домашний
каталог `agent` с правами 600.

## Ограничения и предостережения

- Работает на **личной** подписке только пока действует токен из
  `~/.claude-agent-docker/oauth-token`; протух — перевыпусти (шаг 2).
- **Квота сессии.** Один параграф ≈ 20—30 минут работы агента и заметная доля
  лимита Pro. Два прогона подряд в одно окно не помещаются: 2026-08-08 §20
  отработал, а стартовавший через полтора часа §21 умер на
  `You've hit your session limit`, не записав ни одного файла (лог остаётся,
  прогон надо повторить после сброса). Планируя два параграфа в день,
  разноси их по разным окнам лимита.
- Промпты — под контейнерные пути `/work` (см. выше).
- Для деплоя в прод/пуша в git ssh-ключ монтируется ro; ключ без пароля —
  проверено, что headless `git push` и `ssh root@70.34.202.231` проходят.
- Логи и токен — только в `~/.claude-agent-docker/` (в git не попадают).
- Секрет в чат не присылать, в plist/ENV с миром не класть (файл 0600).

## Как это ложится на конвейер RedPen

Контейнер монтирует репозиторий (`/work`) и ssh-ключ, значит может делать полный
цикл «генерация → деплой черновиками → коммит» по `docs/annotation-agent-prompt.md`
и процедуре из `docs/deployment-log.md` (запись 2026-08-01) — но промпт для
контейнера должен ссылаться на `/work/...`, а не на хостовые пути. Это чистая
замена «ночным launchd-прогонам»: та же работа, но на личной подписке и в изоляции.

## Дальше: мультимодельная дорожка (LiteLLM)

Личная Claude-подписка работает только через Claude Code. «Разные модели внутри»
(OpenAI/Gemini/локальные наравне с Claude) — это отдельный слой: вендор-нейтральный
агент за шлюзом **LiteLLM**, с **API-ключами** по провайдерам (оплата по токенам,
отдельно от $20-подписки). Разворачивать имеет смысл на отдельной VM (не на
прод-сервере RedPen). Эту дорожку соберём отдельной инструкцией.
