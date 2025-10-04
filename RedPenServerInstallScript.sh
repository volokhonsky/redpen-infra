#!/usr/bin/env bash
# ============================================================================
# RedPen Server Install/Start Script (install.sh)
#
# What this script does (safe/optimized):
# - Verifies Docker + compose
# - Verifies required files: infra/docker-compose.yml, .env, .env.secrets
# - Verifies SSH secrets exist and have proper permissions (does NOT create or modify them)
# - Validates Caddyfile and brings up services (no destructive actions, no data/clone removal)
# - Reloads Caddy config to pick up latest changes
# - Runs lightweight smoke checks (HTTPS root, app-config.js, API health, webhook manual POST)
#
# One-time manual steps BEFORE running:
# 1) GitHub Webhook for redpen-publish repository:
#    - Payload URL: https://<DOMAIN>/.hooks/redpen-publish
#    - Content type: application/json
#    - Secret: generate a random string and put the same value into infra/.env.secrets:
#        WEBHOOK_SECRET=<your-random-secret>
#
# 2) GitHub Deploy Key (SSH) for redpen-publish:
#    - On the server, prepare files under: ~/apps/redpen/secrets/content-ssh/
#        id_ed25519        (private key, chmod 600)
#        id_ed25519.pub    (public key, chmod 644)
#        known_hosts       (should contain GitHub host key; e.g. `ssh-keyscan github.com > known_hosts`, chmod 644)
#    - Add id_ed25519.pub as a Deploy Key (Read-only) in GitHub → redpen-publish → Settings → Deploy keys.
#
# 3) Ensure files next to infra/docker-compose.yml:
#    - .env           (public config; committed to git is OK)
#      Must contain at least:
#        DOMAIN=your-domain.tld
#        API_SUBDOMAIN=api
#        API_HOST=${API_SUBDOMAIN}.${DOMAIN}
#        FRONTEND_ORIGIN=https://${DOMAIN}
#        API_BASE_URL=https://${API_HOST}
#        CONTENT_GIT_REPO=git@github.com:<ORG>/redpen-publish.git
#        CONTENT_GIT_REF=main
#    - .env.secrets   (NOT committed)
#      Must contain:
#        WEBHOOK_SECRET=<the-same-secret-as-in-GitHub-Webhooks>
#
# 4) Docker Compose mounts for content-sync must point to the WHOLE SSH folder:
#      ../secrets/content-ssh:/root/.ssh:ro
#    and set:
#      GIT_SSH_COMMAND=ssh -o BatchMode=yes -o IdentitiesOnly=yes -o UserKnownHostsFile=/root/.ssh/known_hosts -i /root/.ssh/id_ed25519
#
# Usage:
#   bash install.sh
#
# Environment overrides (optional):
#   ROOT=~/apps/redpen
#   INFRA_DIR=$ROOT/infra
#   SECRETS_DIR=$ROOT/secrets/content-ssh
#   ENV_FILE=$INFRA_DIR/.env
#   ENV_SECRETS_FILE=$INFRA_DIR/.env.secrets
#
# Notes:
# - This script is non-destructive: it does NOT delete existing clones/data/volumes.
# - If you use Cloudflare, allow/bypass path `/.hooks/*` in WAF/Firewall. During debugging,
#   you can switch DNS record to “DNS only” to avoid proxy-side interference.
# - Manual webhook POST without signature should return 401 (that’s OK). Real GitHub delivery should return 200.
# ============================================================================

set -euo pipefail
if (set -o) 2>/dev/null | grep -q pipefail; then set -o pipefail; fi

ROOT="${ROOT:-$HOME/apps/redpen}"
INFRA_DIR="${INFRA_DIR:-$ROOT/infra}"
SECRETS_DIR="${SECRETS_DIR:-$ROOT/secrets/content-ssh}"
ENV_FILE="${ENV_FILE:-$INFRA_DIR/.env}"
ENV_SECRETS_FILE="${ENV_SECRETS_FILE:-$INFRA_DIR/.env.secrets}"

log(){ printf "[install] %s\n" "$*"; }

# 0) Docker / compose
command -v docker >/dev/null || { log "Docker is required"; exit 1; }
if ! docker compose version >/dev/null 2>&1; then
  log "Installing docker-compose plugin (sudo apt-get install -y docker-compose-plugin) ..."
  sudo apt-get update -y && sudo apt-get install -y docker-compose-plugin
fi

# 1) Project files presence
[[ -f "$INFRA_DIR/docker-compose.yml" ]] || { log "Missing $INFRA_DIR/docker-compose.yml"; exit 1; }
[[ -f "$ENV_FILE" ]] || { log "Missing $ENV_FILE"; exit 1; }
[[ -f "$ENV_SECRETS_FILE" ]] || { log "Missing $ENV_SECRETS_FILE (create with WEBHOOK_SECRET=...)"; exit 1; }

# 2) .env sanity
set -a; . "$ENV_FILE"; set +a
: "${DOMAIN:?DOMAIN missing in .env}"
: "${API_HOST:?API_HOST missing in .env}"
: "${CONTENT_GIT_REPO:?CONTENT_GIT_REPO missing in .env}"
: "${CONTENT_GIT_REF:=main}"
log "DOMAIN=$DOMAIN"
log "API_HOST=$API_HOST"
log "CONTENT_GIT_REPO=$CONTENT_GIT_REPO"
log "CONTENT_GIT_REF=$CONTENT_GIT_REF"

# 3) Secrets check (no creation here)
grep -q '^WEBHOOK_SECRET=' "$ENV_SECRETS_FILE" || { log "WEBHOOK_SECRET is not set in $ENV_SECRETS_FILE"; exit 1; }
[[ -f "$SECRETS_DIR/id_ed25519" ]]      || { log "Missing $SECRETS_DIR/id_ed25519"; exit 1; }
[[ -f "$SECRETS_DIR/id_ed25519.pub" ]]  || { log "Missing $SECRETS_DIR/id_ed25519.pub"; exit 1; }
[[ -f "$SECRETS_DIR/known_hosts" ]]     || { log "Missing $SECRETS_DIR/known_hosts"; exit 1; }
chmod 700 "$ROOT/secrets" "$ROOT/secrets/content-ssh" 2>/dev/null || true
chmod 600 "$SECRETS_DIR/id_ed25519" 2>/dev/null || true
chmod 644 "$SECRETS_DIR/id_ed25519.pub" "$SECRETS_DIR/known_hosts" 2>/dev/null || true

# 4) Compose config & Caddyfile validate
log "docker compose config sanity ..."
docker compose -f "$INFRA_DIR/docker-compose.yml" config >/dev/null

log "Validating Caddyfile ..."
docker compose -f "$INFRA_DIR/docker-compose.yml" run --rm caddy \
  caddy validate --config /etc/caddy/Caddyfile

# 5) Bring up services (no destructive actions)
log "docker compose up -d (build if needed) ..."
docker compose -f "$INFRA_DIR/docker-compose.yml" up -d --build

# 6) Reload Caddy config (pick latest Caddyfile)
log "Reload Caddy config ..."
docker compose -f "$INFRA_DIR/docker-compose.yml" exec caddy \
  caddy reload --config /etc/caddy/Caddyfile || \
docker compose -f "$INFRA_DIR/docker-compose.yml" up -d --force-recreate --no-deps caddy

# 7) Optional: quick SSH visibility check (non-fatal)
log "Quick SSH visibility from content-sync ..."
set +e
docker compose -f "$INFRA_DIR/docker-compose.yml" exec content-sync sh -lc '
apk add --no-cache git openssh >/dev/null 2>&1 || true
echo "GIT_REPO=$GIT_REPO"
ls -l /root/.ssh || true
GIT_SSH_COMMAND="ssh -o BatchMode=yes -o IdentitiesOnly=yes -o UserKnownHostsFile=/root/.ssh/known_hosts -i /root/.ssh/id_ed25519" \
git ls-remote "$GIT_REPO" | head -n1 || true
' 2>/dev/null
set -e

# 8) Local (container) & external smoke checks
log "Local Caddy checks (via localhost with Host header) ..."
docker compose -f "$INFRA_DIR/docker-compose.yml" exec caddy sh -lc '
apk add --no-cache curl >/dev/null 2>&1 || true
echo "HTTP (expect 308 to HTTPS)"; curl -is -m 5 -H "Host: '"$DOMAIN"'" http://localhost/ | sed -n "1,8p"
echo "HTTPS root"; curl -is -m 5 -k -H "Host: '"$DOMAIN"'" https://localhost/ | sed -n "1,12p"
' || true

log "External checks ..."
set +e
curl -Is -m 8 "https://$DOMAIN" | sed -n '1,12p'
curl -s -m 8 "https://$DOMAIN/app-config.js" | sed -n '1,2p'
curl -s -m 8 "https://$API_HOST/api/health" | sed -n '1,4p'
# Manual webhook POST (expect 401 without signature)
curl -i -m 8 -X POST "https://$DOMAIN/.hooks/redpen-publish" \
  -H 'Content-Type: application/json' -H 'X-GitHub-Event: push' -d '{}' | sed -n '1,8p'
set -e

log "Done. If manual POST returns 401 — OK. Use GitHub Redeliver to get 200 and see publish logs in content-sync."
