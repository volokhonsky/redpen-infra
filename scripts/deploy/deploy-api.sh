#!/usr/bin/env sh
# Deploy RedPen API container
# - Updates git repository (ff-only)
# - Builds image from scripts/api
# - Stops/removes previous container
# - Runs new container with volume and env file
# - Performs health-check

# Strict mode: stop on errors, unset vars; enable pipefail if supported
set -eu
if (set -o) 2>/dev/null | grep -q pipefail; then
  set -o pipefail
fi
trap 'rc=$?; if [ "$rc" -ne 0 ]; then echo "[deploy] ERROR: exited with code $rc"; fi' EXIT

# Resolve paths
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# Defaults (can be overridden via environment)
IMAGE_NAME="${IMAGE_NAME:-redpen-api:prod}"
CONTAINER_NAME="${CONTAINER_NAME:-redpen-api}"
API_CONTEXT_DIR="${API_CONTEXT_DIR:-scripts/api}"
DATA_DIR="${DATA_DIR:-/var/redpen-data}"
PORT="${PORT:-8080}"
ENV_FILE="${ENV_FILE:-.env}"

echo "[deploy] repo root: $REPO_ROOT"
echo "[deploy] IMAGE_NAME=$IMAGE_NAME"
echo "[deploy] CONTAINER_NAME=$CONTAINER_NAME"
echo "[deploy] API_CONTEXT_DIR=$API_CONTEXT_DIR"
echo "[deploy] DATA_DIR=$DATA_DIR"
echo "[deploy] PORT=$PORT"
echo "[deploy] ENV_FILE=$ENV_FILE"

# Prepare data directory
echo "[deploy] preparing data dir: $DATA_DIR"
mkdir -p "$DATA_DIR"
# Set owner to uid:gid 10001:10001 (container user)
chown 10001:10001 "$DATA_DIR"
# Write for owner/group
chmod 770 "$DATA_DIR"

# Update repository (ff-only)
echo "[deploy] updating repository (fetch + ff-only pull)"
git fetch --all --prune
git pull --ff-only

# Resolve build context to absolute path
case "$API_CONTEXT_DIR" in
  /*) CTX_DIR="$API_CONTEXT_DIR" ;;
  *) CTX_DIR="$REPO_ROOT/$API_CONTEXT_DIR" ;;
esac

# Build image
echo "[deploy] building image $IMAGE_NAME from $CTX_DIR"
docker build -t "$IMAGE_NAME" "$CTX_DIR"

# Stop/remove previous container if exists
if docker ps -a --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}$"; then
  echo "[deploy] stopping existing container: $CONTAINER_NAME"
  docker stop "$CONTAINER_NAME" || true
  echo "[deploy] removing container: $CONTAINER_NAME"
  docker rm "$CONTAINER_NAME" || true
else
  echo "[deploy] no existing container to stop"
fi

# Resolve env file to absolute path
case "$ENV_FILE" in
  /*) EF="$ENV_FILE" ;;
  *) EF="$REPO_ROOT/$ENV_FILE" ;;
 esac

# Run container
echo "[deploy] starting container: $CONTAINER_NAME"
if [ -f "$EF" ]; then
  echo "[deploy] using env file: $EF"
  docker run -d \
    --name "$CONTAINER_NAME" \
    -p "${PORT}:8080" \
    -v "${DATA_DIR}:/data" \
    --restart unless-stopped \
    --env-file "$EF" \
    "$IMAGE_NAME"
else
  echo "[deploy] env file not found: $EF (starting without --env-file)"
  docker run -d \
    --name "$CONTAINER_NAME" \
    -p "${PORT}:8080" \
    -v "${DATA_DIR}:/data" \
    --restart unless-stopped \
    "$IMAGE_NAME"
fi

# Health-check (non-fatal)
HC_URL="http://localhost:${PORT}/api/health"
echo "[deploy] health-check: $HC_URL"
tries=0
max_tries=20
sleep 1
while [ "$tries" -lt "$max_tries" ]; do
  if command -v curl >/dev/null 2>&1; then
    resp="$(curl -fsS "$HC_URL" || true)"
  elif command -v wget >/dev/null 2>&1; then
    resp="$(wget -qO- "$HC_URL" || true)"
  else
    echo "[deploy] neither curl nor wget available for health-check"
    break
  fi
  if [ -n "$resp" ]; then
    echo "[deploy] health response: $resp"
    break
  fi
  tries=$((tries+1))
  sleep 1
done

echo "[deploy] done"
