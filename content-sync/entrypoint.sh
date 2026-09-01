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
  # */remarks/ is owned by the API (stage 2: SQLite is canonical, the API's
  # publisher.py writes page_NNN.json straight into /srv/public). Excluding it
  # here (without --delete-excluded) also protects it from --delete below.
  # Исключение для */annotations/ снято в фазе 6 переименования (2026-08-30):
  # publisher туда больше не пишет, и первый же прогон после выкладки уносит
  # каталог с тома — это и есть уборка. Адреса из него переадресует nginx.
  # Исключение для */remarks/ снимать нельзя: там живые данные.
  rsync -a --delete --exclude ".git" --exclude "/*/remarks/" /srv/repo/ /srv/staging/

  # Правки статики на месте развёртывания. Подстановка адреса API отсюда ушла
  # вместе со старым SPA (2026-08-30): её единственным потребителем был
  # redpen-editor-bootstrap.js, а /work/ и /survey/ знают адрес сами.
  /usr/bin/env python3 /app/content_sync.py --mutate-only --staging /srv/staging || true

  # Sync to public (shared volume)
  rsync -a --delete --exclude "/*/remarks/" /srv/staging/ /srv/public/

  # Каталоги, куда пишет API (uid 10001): этот процесс работает от root, и после
  # rsync владельцем снова становится root.
  #
  #   remarks     — канон публикации, владелец API с этапа 2;
  #   pages       — HTML читателя. У него два писателя: сборка (через git и этот
  #                 rsync) и API, который перерисовывает затронутую страницу на
  #                 каждую правку. Просмотрщик читает замечания не из JSON, а из
  #                 инлайнового блока внутри этого HTML, поэтому без права записи
  #                 сюда правка через редактор до читателя не доезжает.
  if [ -d /srv/public ]; then
    for doc_dir in /srv/public/*/; do
      # Каталог книги опознаём по metadata.json: на первом уровне лежат ещё
      # js/, css/, work/, survey/ — им пустые remarks/ и pages/ ни к
      # чему (до 2026-08-31 цикл создавал их всем подряд).
      [ -f "${doc_dir}metadata.json" ] || continue
      for owned in remarks pages; do
        mkdir -p "${doc_dir}${owned}"
        chown -R 10001:10001 "${doc_dir}${owned}"
      done
    done
  fi

  sync_log "Publish complete"
}

# Perform initial sync
initial_clone_and_publish || sync_log "Initial publish failed (continuing to serve existing content)"

# Start webhook server (python stdlib) on port 9000
exec /usr/bin/env python3 /app/content_sync.py --server --repo /srv/repo --public /srv/public --staging /srv/staging --addr 0.0.0.0 --port 9000
