# Isolated Claude Code agent runner.
# Purpose: run `claude -p` on the PERSONAL Claude subscription, fully isolated
# from the host's JetBrains `central` work-proxy. The container has no
# ~/.claude/settings.json and no ANTHROPIC_BASE_URL pointing at 127.0.0.1:19516,
# so claude talks straight to https://api.anthropic.com with the token we inject.
FROM node:20-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      git openssh-client ca-certificates curl \
      python3 python3-pip \
 && rm -rf /var/lib/apt/lists/*

# Pillow: make_grid_images.py needs it if a future paragraph lacks grid images.
RUN pip3 install --no-cache-dir --break-system-packages Pillow || true

# Claude Code CLI.
RUN npm install -g @anthropic-ai/claude-code

# Sanity: no work-proxy env baked in.
ENV ANTHROPIC_BASE_URL=https://api.anthropic.com

WORKDIR /work
