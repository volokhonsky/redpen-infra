
#!/usr/bin/env bash
# Purpose: Remove nested repos (redpen-content, redpen-publish) from the parent repo's index
# so that GitHub stops rendering them as submodule links, WITHOUT deleting the directories
# or touching their own Git data. Safe to run multiple times.
#
# What it does:
#   - Verifies we are in a Git working tree and on a branch
#   - Ensures no uncommitted changes in the parent (unless --force)
#   - Runs git rm --cached on redpen-content and redpen-publish if they are tracked
#   - Removes .gitmodules from index if present
#   - Ensures .gitignore contains entries to ignore the nested repos
#   - Commits and prints next steps to push
#
# Usage:
#   bash scripts/de_submodule_cleanup.sh
#   bash scripts/de_submodule_cleanup.sh --force    # proceed even if parent has uncommitted changes
set -euo pipefail

FORCE=${1:-}
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Basic checks
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "[!] Not inside a git repository: $ROOT_DIR" >&2
  exit 1
fi

# Check worktree cleanliness
if [[ "$FORCE" != "--force" ]]; then
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "[!] Parent repo has uncommitted changes. Commit or stash them, or re-run with --force." >&2
    git status --short
    exit 1
  fi
fi

changed_something=0

# Helper to untrack a path if tracked
untrack_if_tracked() {
  local path="$1"
  if git ls-files --error-unmatch "$path" >/dev/null 2>&1; then
    echo "[-] Removing from index (keeping working tree): $path"
    git rm -r --cached "$path" || true
    changed_something=1
  else
    echo "[i] Not tracked in index: $path"
  fi
}

untrack_if_tracked "redpen-content"
untrack_if_tracked "redpen-publish"

# Remove .gitmodules from index if present
if [[ -f .gitmodules ]]; then
  if git ls-files --error-unmatch .gitmodules >/dev/null 2>&1; then
    echo "[-] Removing .gitmodules from index"
    git rm --cached .gitmodules || true
    changed_something=1
  else
    echo "[i] .gitmodules exists but not tracked in index"
  fi
else
  echo "[i] No .gitmodules file present"
fi

# Ensure .gitignore has the entries
ensure_ignore() {
  local pattern="$1"
  if ! grep -qxF "$pattern" .gitignore 2>/dev/null; then
    echo "$pattern" >> .gitignore
    echo "[+] Added to .gitignore: $pattern"
    git add .gitignore
    changed_something=1
  else
    echo "[i] .gitignore already contains: $pattern"
  fi
}

ensure_ignore "/redpen-content/"
ensure_ignore "/redpen-publish/"

if [[ "$changed_something" -eq 1 ]]; then
  msg="chore: de-submodule redpen-content and redpen-publish; ignore nested repos"
  echo "[*] Committing: $msg"
  git commit -m "$msg"
  echo
  echo "Done. Now push to remote so GitHub stops showing submodule links:"
  echo "  git push"
else
  echo "[i] Nothing to change. Index already clean and .gitignore updated."
fi
