#!/usr/bin/env bash
# Publish the built site (_site/) to the `gh-pages` branch as a single *orphan* commit — no
# parent, so the branch never accumulates history and the generated HTML stays out of `main`.
#
#   ./scripts/publish-pages.sh                 # writes the LOCAL gh-pages branch; does NOT push
#   PAGES_PUSH=1 ./scripts/publish-pages.sh    # also force-pushes gh-pages (CI publishes this way)
#
# GitHub Pages serves it from the branch root (Settings -> Pages -> Deploy from a branch ->
# gh-pages -> / (root)). Each publish *replaces* gh-pages, so it is always force-pushed.
#
# Implementation: a throwaway index + git plumbing (write-tree / commit-tree / branch -f), so
# the working tree, the real index, and the checked-out branch are all left untouched.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
SITE="$ROOT/_site"
BRANCH="${PAGES_BRANCH:-gh-pages}"
REMOTE="${PAGES_REMOTE:-origin}"
GIT_DIR_ABS="$ROOT/.git"

if [ ! -d "$SITE" ]; then
  echo "error: $SITE not found — run 'python scripts/build.py' first." >&2
  exit 1
fi

touch "$SITE/.nojekyll"
SRC_REF="$(git --git-dir="$GIT_DIR_ABS" rev-parse --short HEAD 2>/dev/null || echo unknown)"

TMP_DIR="$(mktemp -d -t ghpages-idx.XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT
export GIT_INDEX_FILE="$TMP_DIR/index"

cd "$SITE"
git --git-dir="$GIT_DIR_ABS" --work-tree="$SITE" add -A .
TREE="$(git --git-dir="$GIT_DIR_ABS" write-tree)"
COMMIT="$(printf 'Publish site (source %s)\n' "$SRC_REF" | git --git-dir="$GIT_DIR_ABS" commit-tree "$TREE")"
git --git-dir="$GIT_DIR_ABS" branch -f "$BRANCH" "$COMMIT"

echo "Published $(find "$SITE" -type f | wc -l) files to branch '$BRANCH' (orphan commit ${COMMIT:0:12})."

if [ "${PAGES_PUSH:-0}" = "1" ]; then
  echo "Force-pushing $BRANCH to $REMOTE ..."
  git --git-dir="$GIT_DIR_ABS" push --force "$REMOTE" "$BRANCH"
  echo "Published to $REMOTE/$BRANCH."
else
  echo "Local branch written. Push it with:  git push --force-with-lease $REMOTE $BRANCH"
fi
