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
# ---------------------------------------------------------------------------
set -u
LABEL="${1:?usage: run-agent.sh <label> <prompt_file> [host_repo_dir]}"
PROMPT_FILE="${2:?prompt_file required}"
HOST_REPO="${3:-$HOME/Documents/redpen}"

BASE="$HOME/.claude-agent-docker"
LOG="$BASE/${LABEL}.log"
TOKEN_FILE="$BASE/oauth-token"

log() { print -r -- "$(date '+%F %T %Z') | $*" >>"$LOG"; }

[[ -r "$PROMPT_FILE" ]] || { echo "FATAL: prompt not readable: $PROMPT_FILE"; exit 10; }
if [[ ! -s "$TOKEN_FILE" ]]; then
  echo "FATAL: no personal token at $TOKEN_FILE."
  echo "  Under your PERSONAL claude.ai account: run 'claude setup-token', then"
  echo "  store it: chmod 600; see README.md."
  exit 20
fi
export CLAUDE_CODE_OAUTH_TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"

log "==================== START $LABEL (docker) ===================="

# ssh known_hosts (github + prod) so deploy/push work non-interactively.
KH="$BASE/known_hosts"
[[ -f "$KH" ]] || : > "$KH"

# claude refuses --dangerously-skip-permissions under root, so the CLI runs as an
# unprivileged user created at start with the HOST uid — that also keeps files it
# writes into the bind-mounted repo owned by us.
HOST_UID="$(id -u)"

docker run --rm --name "claude-agent-${LABEL}" \
  -e CLAUDE_CODE_OAUTH_TOKEN \
  -e ANTHROPIC_BASE_URL=https://api.anthropic.com \
  -e HOST_UID="$HOST_UID" \
  -e GIT_AUTHOR_NAME=volokhonsky -e GIT_AUTHOR_EMAIL=volokhonsky@gmail.com \
  -e GIT_COMMITTER_NAME=volokhonsky -e GIT_COMMITTER_EMAIL=volokhonsky@gmail.com \
  -v "$HOST_REPO":/work \
  -v "$PROMPT_FILE":/prompt.txt:ro \
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
    git config --system --add safe.directory /work || true
    cd /work
    # -w: keep the injected token/base-url across the privilege drop (never in argv).
    exec su agent -w CLAUDE_CODE_OAUTH_TOKEN,ANTHROPIC_BASE_URL \
      -c "cd /work && claude -p \"\$(cat /prompt.txt)\" --dangerously-skip-permissions --output-format text"
  ' >>"$LOG" 2>&1
RC=$?
log "==================== END $LABEL exit=$RC ===================="
exit $RC
