#!/usr/bin/env bash
set -euo pipefail

# Configure SSH if keys are mounted
if [ -f /root/.ssh/id_ed25519 ]; then
  chmod 600 /root/.ssh/id_ed25519 || true
  echo "[content-sync] SSH key present: /root/.ssh/id_ed25519" >&2
fi
if [ -f /root/.ssh/known_hosts ]; then
  chmod 644 /root/.ssh/known_hosts || true
fi

# Ensure env vars
: "${GIT_REPO:?GIT_REPO is required}"
: "${GIT_REF:=main}"
: "${API_BASE_URL:=}"

sync_log() { echo "[content-sync] $*" >&2; }

initial_clone_and_publish() {
  sync_log "Initial sync: repo=${GIT_REPO} ref=${GIT_REF}"
  if [ ! -d /srv/repo/.git ]; then
    rm -rf /srv/repo
    mkdir -p /srv/repo
    git clone --depth 1 --branch "${GIT_REF}" "${GIT_REPO}" /srv/repo
  else
    cd /srv/repo
    git fetch --all --prune
    git reset --hard "origin/${GIT_REF}" || git checkout -f "${GIT_REF}" || true
  fi

  publish_from_repo
}

publish_from_repo() {
  set -e
  sync_log "Publishing to /srv/public via staging"
  rm -rf /srv/staging && mkdir -p /srv/staging
  rsync -a --delete --exclude ".git" /srv/repo/ /srv/staging/

  # Generate app-config.js into staging
  if [ -n "${API_BASE_URL}" ]; then
    printf 'window.APP_CONFIG={apiBaseUrl:%q};' "${API_BASE_URL}" > /srv/staging/app-config.js
  fi

  # Ensure app-config.js is referenced by HTML files and patch bootstrap js
  /usr/bin/env python3 /app/content_sync.py --mutate-only --staging /srv/staging || true

  # Sync to public (shared volume)
  rsync -a --delete /srv/staging/ /srv/public/
  sync_log "Publish complete"
}

# Perform initial sync
initial_clone_and_publish || sync_log "Initial publish failed (continuing to serve existing content)"

# Start webhook server (python stdlib) on port 9000
exec /usr/bin/env python3 /app/content_sync.py --server --repo /srv/repo --public /srv/public --staging /srv/staging --addr 0.0.0.0 --port 9000
