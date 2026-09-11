#!/bin/zsh
# ---------------------------------------------------------------------------
# chain-run.sh <label> [max_wait_seconds]
#
# Один прогон агента в очереди: дождаться своей очереди, запустить run-agent.sh,
# снять замок и убрать за собой LaunchAgent, если он есть (одноразовые ночные
# задания не должны выстрелить второй раз).
#
# ОЧЕРЕДЬ — С НОМЕРКАМИ, а не «кто первый проснулся». До 2026-09-05 замок брал
# тот, чья десятиминутная проверка первой попала на освободившийся замок: в ночь
# на 2026-09-04 §10 обошёл вставшие раньше §9 и §8, а §8 в итоге выбрал восемь
# часов ожидания и отменился, не сделав ничего.
#
# Как теперь. При старте прогон кладёт номерок $BASE/agent.queue/<epoch>-<pid>.
# Замок (каталог $BASE/agent.lock, mkdir атомарен) берёт только тот, чей номерок
# самый ранний из живых; номерки мёртвых процессов подчищаются на каждом круге.
# Порядок — по времени постановки, при совпадении секунды — по pid.
#
# Ожидание ограничено max_wait_seconds (по умолчанию 8 часов): дальше прогон
# отказывается стартовать, чтобы ночное задание не наслаивалось на дневную работу.
#
# RUN_AGENT — путь к раннеру (для тестов очереди подставляется заглушка).
# ---------------------------------------------------------------------------
set -u
LABEL="${1:?usage: chain-run.sh <label> [max_wait_seconds]}"
MAX_WAIT="${2:-28800}"
POLL="${POLL:-600}"

BASE="$HOME/.claude-agent-docker"
LOCK="$BASE/agent.lock"
QUEUE="$BASE/agent.queue"
LOG="$BASE/${LABEL}.log"
PLIST="$HOME/Library/LaunchAgents/com.redpen.agent.${LABEL}.plist"
RUN_AGENT="${RUN_AGENT:-$BASE/run-agent.sh}"

export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.pyenv/shims:$PATH"

log() { print -r -- "$(date '+%F %T %Z') | [chain] $*" >>"$LOG"; }

mkdir -p "$QUEUE"
TICKET="$QUEUE/$(date +%s)-$$"
print -r -- "$LABEL" > "$TICKET"

cleanup() {
  rm -f "$TICKET"
  if [[ -f "$LOCK/pid" && "$(cat "$LOCK/pid" 2>/dev/null)" == "$$" ]]; then
    rm -rf "$LOCK"
    log "замок снят"
  fi
}
trap 'cleanup' EXIT INT TERM

# Самый ранний живой номерок; заодно подчищает номерки умерших процессов.
oldest_ticket() {
  local f base ep pid bestf="" bep=0 bpid=0
  for f in "$QUEUE"/*(N); do
    base="${f:t}"; ep="${base%%-*}"; pid="${base##*-}"
    if [[ "$ep" != <-> || "$pid" != <-> ]]; then rm -f "$f"; continue; fi
    if ! kill -0 "$pid" 2>/dev/null; then rm -f "$f"; continue; fi
    if [[ -z "$bestf" ]] || (( ep < bep || (ep == bep && pid < bpid) )); then
      bestf="$f"; bep=$ep; bpid=$pid
    fi
  done
  print -r -- "$bestf"
}

queue_position() {
  local f base ep pid n=0 myep myp
  myep="${${TICKET:t}%%-*}"; myp="${${TICKET:t}##*-}"
  for f in "$QUEUE"/*(N); do
    base="${f:t}"; ep="${base%%-*}"; pid="${base##*-}"
    [[ "$ep" == <-> && "$pid" == <-> ]] || continue
    kill -0 "$pid" 2>/dev/null || continue
    (( ep < myep || (ep == myep && pid < myp) )) && (( n++ ))
  done
  print -r -- $(( n + 1 ))
}

waited=0
while true; do
  if [[ "$(oldest_ticket)" == "$TICKET" ]] && mkdir "$LOCK" 2>/dev/null; then
    print -r -- "$$" > "$LOCK/pid"
    print -r -- "$LABEL" > "$LOCK/label"
    rm -f "$TICKET"
    log "замок взят после ожидания ${waited}s"
    break
  fi

  owner_pid="$(cat "$LOCK/pid" 2>/dev/null)"
  owner_label="$(cat "$LOCK/label" 2>/dev/null)"
  if [[ -d "$LOCK" ]]; then
    stale=0
    if [[ -n "$owner_pid" ]]; then
      kill -0 "$owner_pid" 2>/dev/null || stale=1
    else
      # pid пуст: либо замок взят миллисекунду назад и владелец ещё не успел его
      # записать, либо владелец умер между mkdir и записью. Разбираем по возрасту
      # каталога — минуту не трогаем.
      [[ -n "$(find "$LOCK" -maxdepth 0 -mmin +1 2>/dev/null)" ]] && stale=1
    fi
    if (( stale )); then
      log "замок брошен прогоном ${owner_label:-?} (pid=${owner_pid:-нет}) — забираю"
      rm -rf "$LOCK"
      continue
    fi
  fi

  if [[ -d "$LOCK" ]]; then
    pos="$(queue_position)"
    if (( pos == 1 )); then place="я следующий"; else place="передо мной ещё $(( pos - 1 ))"; fi
    log "жду прогон ${owner_label:-?} (pid=${owner_pid:-?}), $place, ждём уже ${waited}s из ${MAX_WAIT}s"
  else
    log "замок свободен, но впереди меня по номерку прогон $(cat "$(oldest_ticket)" 2>/dev/null); ждём уже ${waited}s из ${MAX_WAIT}s"
  fi

  if (( waited >= MAX_WAIT )); then
    log "очередь не подошла за ${MAX_WAIT}s — этот запуск отменён"
    rm -f "$PLIST"
    launchctl bootout "gui/$(id -u)/com.redpen.agent.${LABEL}" 2>/dev/null
    exit 75
  fi
  sleep "$POLL"
  (( waited += POLL ))
done

zsh "$RUN_AGENT" "$LABEL" "$BASE/${LABEL}-prompt.txt"
RC=$?
log "run-agent.sh завершился exit=$RC"

cleanup

# Снять одноразовое задание. rm ДО bootout: bootout сносит эту самую задачу и
# может прибить скрипт сигналом, всё после него может не выполниться.
if [[ -f "$PLIST" ]]; then
  rm -f "$PLIST"
  log "LaunchAgent снят (exit=$RC)"
  launchctl bootout "gui/$(id -u)/com.redpen.agent.${LABEL}" 2>/dev/null
fi
exit $RC
