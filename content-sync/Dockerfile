FROM python:3.12-alpine

# Install required tools: git, openssh-client for private repos, rsync for publishing
RUN apk add --no-cache git openssh-client rsync bash curl

# Prepare directories
RUN mkdir -p /app /srv/repo /srv/public /srv/staging
WORKDIR /app

# Copy app code
COPY entrypoint.sh /app/entrypoint.sh
COPY content_sync.py /app/content_sync.py

# Ensure entrypoint is executable
RUN chmod +x /app/entrypoint.sh

# Expose internal webhook port
EXPOSE 9000

# Environment variables
ENV GIT_REPO="" \
    GIT_REF="main" \
    WEBHOOK_SECRET="" \
    API_BASE_URL=""

ENTRYPOINT ["/app/entrypoint.sh"]
