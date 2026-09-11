#!/bin/zsh
# ---------------------------------------------------------------------------
# Run a Claude Code agent in an ISOLATED Docker container on the PERSONAL
# subscription. No host ~/.claude/settings.json, no JetBrains central proxy —
# claude talks straight to https://api.anthropic.com with the injected token,
# so billing lands on the personal account (verify before trusting).
#
# Token: personal, minted with `claude setup-token` while logged into the
# PERSONAL account, stored 0600 at ~/.claude-agent-docker/oauth-token.
# Never in the image, never in argv (passed via inherited env).
#
# Usage: run-agent.sh <label> <prompt_file> [<host_repo_dir>]
#   prompt_file is mounted read-only at /prompt.txt; the repo at /work.
#   Prompts must reference container paths (/work), not host paths.
#
# Result check: EXPECT_FILES (or $BASE/<label>.expect, one path per line) lists the
# files without which the job is NOT done. claude exiting 0 is not proof of work —
# on 2026-09-04 the factchecker closed one paragraph of two, announced it would
# "continue in the background" (it has none) and exited 0. Missing files send the
# run back with a resume message naming them, up to INCOMPLETE_MAX (default 3)
# times, then the run ends with exit=3.
#
# Session limit: a Pro session limit ends the run, and the refusal usually NAMES
# the reset time ("resets 5:30am (UTC)") — the runner parses it and sleeps until
# then, instead of knocking on a closed door every RETRY_INTERVAL. Without a
# usable hint it falls back to RETRY_INTERVAL (default 20 min). Bounded twice:
# MAX_ATTEMPTS (default 24) and MAX_WALL (default 12 h from start), because a
# sleep-until-reset can be hours long. Retries RESUME the same conversation —
# claude's state dir lives on the host ($BASE/state/<label>) and is mounted as
# CLAUDE_CONFIG_DIR, so --continue picks up where the run stopped; if there is
# no conversation to continue, the attempt falls back to a fresh start.
# Any non-limit failure stops the loop — retrying a broken task is waste.
#   RETRY_INTERVAL=600 MAX_ATTEMPTS=6 run-agent.sh ...   # override
# ---------------------------------------------------------------------------
set -u
LABEL="${1:?usage: run-agent.sh <label> <prompt_file> [host_repo_dir]}"
PROMPT_FILE="${2:?prompt_file required}"
HOST_REPO="${3:-$HOME/Documents/redpen}"

BASE="$HOME/.claude-agent-docker"
LOG="$BASE/${LABEL}.log"
TOKEN_FILE="$BASE/oauth-token"
STATE_DIR="$BASE/state/$LABEL"

# launchd hands us a bare PATH (/usr/bin:/bin:/usr/sbin:/sbin) with no
# /usr/local/bin, where the Docker CLI symlink lives. Without this, `docker info`
# fails as "command not found", ensure_docker misreads it as a dead daemon, waits
# out the full 300s and then `docker run` exits 127 — which is NOT in RETRY_RE, so
# the whole scheduled run dies without a retry. (Seen 2026-08-15, §29 at 16:15.)
export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.pyenv/shims:$PATH"

RETRY_INTERVAL="${RETRY_INTERVAL:-1200}"   # seconds between attempts (20 min)
MAX_ATTEMPTS="${MAX_ATTEMPTS:-24}"         # 24 × 20 min ≈ 8 h of waiting out the limit
# Сон до сброса лимита бывает многочасовым, поэтому одного счётчика попыток мало:
# без общего срока прогон может растянуться на сутки. MAX_WALL — предел по часам
# от старта, после которого раннер не ложится спать заново.
MAX_WALL="${MAX_WALL:-43200}"              # 12 часов

# Claude может выйти с кодом 0, не записав результат: 2026-09-04 фактчекер закрыл
# один параграф из двух, отчитался «работа идёт в фоне» (фона у него нет) и вышел
# успешно — раннер счёл задание выполненным. EXPECT_FILES перечисляет файлы, без
# которых работа НЕ сделана; пути относительные считаются от каталога репозитория.
# Список берётся из аргумента, из переменной окружения или из $BASE/<label>.expect.
EXPECT_FILES="${EXPECT_FILES:-}"
[[ -z "$EXPECT_FILES" && -r "$BASE/${LABEL}.expect" ]] && EXPECT_FILES="$(grep -v '^[[:space:]]*#' "$BASE/${LABEL}.expect" | tr '\n' ' ')"
INCOMPLETE_MAX="${INCOMPLETE_MAX:-3}"      # столько раз просим дописать недостающее

# Markers of "the subscription window is exhausted", not of a broken task.
LIMIT_RE='(session limit|usage limit|limit reached|rate.?limit|429|Please wait.*(before|until).*(try|retry))'
# A dead Docker daemon is a transient obstacle too (scheduled run with Docker Desktop
# not up: 2026-08-14 19:20, §26 died instantly with exit=125) — retry it as well.
RETRY_RE="${LIMIT_RE}|Cannot connect to the Docker daemon|docker daemon is not running|Is the docker daemon running"

log() { print -r -- "$(date '+%F %T %Z') | $*" >>"$LOG"; }

# Печатает недостающие из EXPECT_FILES (пусто = всё на месте / проверять нечего).
missing_expected() {
  local f p out=""
  [[ -n "$EXPECT_FILES" ]] || return 0
  for f in ${=EXPECT_FILES}; do
    [[ "$f" == /* ]] && p="$f" || p="$HOST_REPO/$f"
    [[ -s "$p" ]] || out="$out $f"
  done
  print -r -- "${out# }"
}

# Claude печатает время сброса окна подписки прямо в сообщении об отказе:
#   "You've hit your session limit · resets 5:30am (UTC)"
# Спать до этого времени вместо слепых RETRY_INTERVAL — единственный способ не
# долбиться в закрытую дверь каждые 20 минут (16 пустых попыток за ночь на
# 2026-09-04). Печатает секунды сна на stdout; молчит, если подсказки нет или
# она в незнакомом часовом поясе — тогда зовущий берёт RETRY_INTERVAL.
sleep_until_reset() {
  local file="$1" hint hh mm ampm target now secs today
  hint="$(grep -oiE 'resets [0-9]{1,2}(:[0-9]{2})? ?(am|pm) \(UTC\)' "$file" | tail -1)" || return 1
  [[ -n "$hint" ]] || return 1
  hh="${${hint#resets }%%[:ap ]*}"
  mm="$(print -r -- "$hint" | grep -oE ':[0-9]{2}' | tr -d ':')"
  [[ -n "$mm" ]] || mm=00
  ampm="$(print -r -- "$hint" | grep -oiE '(am|pm) \(UTC\)' | cut -c1-2 | tr 'A-Z' 'a-z')"
  (( hh = 10#$hh % 12 ))
  [[ "$ampm" == "pm" ]] && (( hh += 12 ))
  today="$(date -u +%Y-%m-%d)"
  target="$(date -u -j -f '%Y-%m-%d %H:%M:%S' "$today $(printf '%02d' $hh):$mm:00" +%s 2>/dev/null)" || return 1
  now="$(date +%s)"
  # Время сброса уже прошло? Если только что (в пределах полутора часов) — окно
  # вот-вот откроется или уже открылось, ждать сутки бессмысленно: пробуем через
  # пять минут. Если давно — подсказка странная, пусть решает RETRY_INTERVAL.
  if (( target <= now )); then
    if (( now - target <= 5400 )); then
      print -r -- 300      # окно вот-вот откроется или уже открылось
      return 0
    fi
    (( target += 86400 ))  # прошло давно — значит названо завтрашнее время
  fi
  (( secs = target - now + 120 ))                # запас, чтобы не попасть в секунду сброса
  (( secs < 60 )) && secs=60
  (( secs > 28800 )) && return 1                 # больше 8 часов — не верим подсказке
  print -r -- "$secs"
}

[[ -r "$PROMPT_FILE" ]] || { echo "FATAL: prompt not readable: $PROMPT_FILE"; exit 10; }
if [[ ! -s "$TOKEN_FILE" ]]; then
  echo "FATAL: no personal token at $TOKEN_FILE."
  echo "  Under your PERSONAL claude.ai account: run 'claude setup-token', then"
  echo "  store it: chmod 600; see README.md."
  exit 20
fi
export CLAUDE_CODE_OAUTH_TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"

mkdir -p "$STATE_DIR"

STARTED_AT="$(date +%s)"
log "==================== START $LABEL (docker) ===================="

# ssh known_hosts (github + prod) so deploy/push work non-interactively.
KH="$BASE/known_hosts"
[[ -f "$KH" ]] || : > "$KH"

# claude refuses --dangerously-skip-permissions under root, so the CLI runs as an
# unprivileged user created at start with the HOST uid — that also keeps files it
# writes into the bind-mounted repo owned by us.
HOST_UID="$(id -u)"

# Scheduled runs can land while Docker Desktop is not up (the daemon is per-user and
# does not autostart) — bring it up and wait, instead of failing on exit=125.
ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    log "FATAL: no 'docker' on PATH ($PATH) — not a dead daemon, don't wait for one"
    return 1
  fi
  docker info >/dev/null 2>&1 && return 0
  log "docker daemon down — starting Docker Desktop"
  open -ga Docker 2>/dev/null || return 1
  local i
  for i in {1..60}; do          # up to ~5 minutes
    sleep 5
    if docker info >/dev/null 2>&1; then
      log "docker daemon up after $((i * 5))s"
      return 0
    fi
  done
  log "docker daemon still down after 300s"
  return 1
}

attempt=1
RC=1
incomplete=0
RESUME_REASON=""
while (( attempt <= MAX_ATTEMPTS )); do
  ensure_docker || true   # failure here surfaces as a retryable exit=125 below
  # First attempt starts the task; later ones resume the conversation that the
  # limit cut short (RESUME=1 is read inside the container).
  if (( attempt == 1 )); then RESUME=0; else RESUME=1; fi
  ATTEMPT_LOG="$(mktemp -t "agent-${LABEL}")"
  log "-------- attempt $attempt/$MAX_ATTEMPTS (resume=$RESUME) --------"

  docker rm -f "claude-agent-${LABEL}" >/dev/null 2>&1 || true
  docker run --rm --name "claude-agent-${LABEL}" \
    -e CLAUDE_CODE_OAUTH_TOKEN \
    -e ANTHROPIC_BASE_URL=https://api.anthropic.com \
    -e CLAUDE_CONFIG_DIR=/state \
    -e HOST_UID="$HOST_UID" \
    -e RESUME="$RESUME" \
    -e RESUME_REASON="$RESUME_REASON" \
    -e GIT_AUTHOR_NAME=volokhonsky -e GIT_AUTHOR_EMAIL=volokhonsky@gmail.com \
    -e GIT_COMMITTER_NAME=volokhonsky -e GIT_COMMITTER_EMAIL=volokhonsky@gmail.com \
    -v "$HOST_REPO":/work \
    -v "$PROMPT_FILE":/prompt.txt:ro \
    -v "$STATE_DIR":/state \
    -v "$HOME/.ssh/id_ed25519":/keys/id_ed25519:ro \
    -v "$KH":/keys/known_hosts \
    claude-agent:latest \
    bash -lc '
      set -e
      id -u agent >/dev/null 2>&1 || useradd -m -u "$HOST_UID" -s /bin/bash agent 2>/dev/null \
        || useradd -m -o -u "$HOST_UID" -s /bin/bash agent
      AH="$(getent passwd agent | cut -d: -f6)"
      mkdir -p "$AH/.ssh"
      cp /keys/id_ed25519 "$AH/.ssh/id_ed25519" 2>/dev/null || true
      cp /keys/known_hosts "$AH/.ssh/known_hosts" 2>/dev/null || true
      ssh-keyscan -H github.com 70.34.202.231 >> "$AH/.ssh/known_hosts" 2>/dev/null || true
      chown -R agent:agent "$AH/.ssh"; chmod 700 "$AH/.ssh"; chmod 600 "$AH/.ssh/id_ed25519" 2>/dev/null || true
      # claude state lives on the host so a retry can resume the cut-off conversation.
      mkdir -p /state; chown -R agent:agent /state
      git config --system --add safe.directory /work || true
      cd /work
      # -w: keep the injected token/base-url across the privilege drop (never in argv).
      exec su agent -w CLAUDE_CODE_OAUTH_TOKEN,ANTHROPIC_BASE_URL,CLAUDE_CONFIG_DIR,RESUME,RESUME_REASON -c '"'"'
        cd /work
        if [ "$RESUME" = "1" ]; then
          MSG="Продолжай прерванную работу с места остановки. ${RESUME_REASON:-Сессия оборвалась по лимиту подписки.}
Сначала проверь, что уже сделано (файлы на диске), и не переделывай готовое.
Исходное задание ниже.

$(cat /prompt.txt)"
          OUT=$(mktemp)
          claude -p "$MSG" --continue --dangerously-skip-permissions --output-format text 2>&1 | tee "$OUT"
          rc=${PIPESTATUS[0]}
          [ "$rc" = 0 ] && exit 0
          # Only a MISSING conversation justifies starting over (fresh state dir,
          # lost history). A limit hit must propagate, or the retry would burn a
          # new session instead of waiting the old one out.
          if grep -qiE "no conversation|no such session|not found" "$OUT"; then
            echo "[runner] nothing to --continue (rc=$rc), starting a fresh conversation"
            exec claude -p "$MSG" --dangerously-skip-permissions --output-format text
          fi
          exit $rc
        else
          exec claude -p "$(cat /prompt.txt)" --dangerously-skip-permissions --output-format text
        fi
      '"'"'
    ' >"$ATTEMPT_LOG" 2>&1
  RC=$?
  cat "$ATTEMPT_LOG" >>"$LOG"

  if (( RC == 0 )); then
    MISSING="$(missing_expected)"
    if [[ -z "$MISSING" ]]; then
      rm -f "$ATTEMPT_LOG"
      log "attempt $attempt finished OK"
      break
    fi
    rm -f "$ATTEMPT_LOG"
    (( incomplete++ ))
    log "attempt $attempt: агент вышел с кодом 0, но не записал:$MISSING"
    if (( incomplete > INCOMPLETE_MAX )); then
      log "недостающие файлы не появились после $INCOMPLETE_MAX просьб дописать — останавливаюсь"
      RC=3
      break
    fi
    if (( attempt == MAX_ATTEMPTS )); then
      log "попытки исчерпаны ($MAX_ATTEMPTS), результат неполон — останавливаюсь"
      RC=3
      break
    fi
    RESUME_REASON="Ты вышел, но результат не записан: нет файлов$MISSING. Никакого фонового процесса у тебя нет — контейнер уничтожается вместе с тобой, поэтому незаписанное пропадает. Допиши недостающее сейчас и убедись, что файлы на диске."
    log "прошу дописать недостающее (попытка ${incomplete} из ${INCOMPLETE_MAX})"
    sleep 30
    (( attempt++ ))
    continue
  fi

  if grep -qiE "$RETRY_RE" "$ATTEMPT_LOG"; then
    if (( attempt == MAX_ATTEMPTS )); then
      rm -f "$ATTEMPT_LOG"
      log "transient failure again, attempts exhausted ($MAX_ATTEMPTS) — giving up"
      break
    fi
    RESUME_REASON="Сессия оборвалась по лимиту подписки."
    ELAPSED=$(( $(date +%s) - STARTED_AT ))
    SLEEP="$(sleep_until_reset "$ATTEMPT_LOG")" || SLEEP=""
    if [[ -n "$SLEEP" ]]; then
      log "transient failure (exit=$RC); лимит сбросится через ${SLEEP}s — сплю до сброса, а не ${RETRY_INTERVAL}s"
    else
      SLEEP="$RETRY_INTERVAL"
      log "transient failure (exit=$RC: session limit or docker down); времени сброса в ответе нет — сплю ${SLEEP}s, потом продолжаю"
    fi
    rm -f "$ATTEMPT_LOG"
    if (( ELAPSED + SLEEP > MAX_WALL )); then
      log "прогон идёт уже ${ELAPSED}s, сон ещё ${SLEEP}s не уложится в MAX_WALL=${MAX_WALL}s — останавливаюсь"
      break
    fi
    sleep "$SLEEP"
    (( attempt++ ))
    continue
  fi

  rm -f "$ATTEMPT_LOG"
  log "attempt $attempt failed with exit=$RC, not a transient failure — no retry"
  break
done

log "==================== END $LABEL exit=$RC attempts=$attempt ===================="
exit $RC
