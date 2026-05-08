#!/usr/bin/env bash
# Preflight: enforce the docker-compose layering policy from issue #53.
#
# Rule: the main `docker-compose.yml` (committed to git) must NOT mount
# any sibling-directory path (`../...`). Personal and test-only mounts
# go in `docker-compose.override.yml`, which is gitignored.
#
# Why: the main file should bootstrap on any environment that doesn't
# have the sibling repos cloned. A `:ro` mount whose source doesn't
# exist will fail `docker compose up` with a non-obvious error, and the
# convention only lives in .gitignore comments + issue #53 — invisible
# to a new contributor or a stray paste.
#
# This script is deliberately narrow:
# - Inspects ONLY `docker-compose.yml`. The override file is the
#   correct place for sibling mounts and is left alone.
# - Looks at `volumes:` entries (lines starting with a dash).
# - Flags any source path beginning with `../`.
#
# Usage:
#   ./scripts/preflight.sh           # exits 0 on clean, 1 on policy violation
#   ./scripts/preflight.sh --quiet   # same, but no success message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.yml"

QUIET=0
case "${1:-}" in
    --quiet|-q) QUIET=1 ;;
esac

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "preflight: docker-compose.yml not found at $COMPOSE_FILE" >&2
    exit 2
fi

# Match volume entries pointing at sibling directories. We accept both
# the explicit `- ../foo:/bar` short-form and the `source: ../foo` long-form.
# `grep -n` gives line numbers for the error message.
violations=$(
    grep -nE '^[[:space:]]*-[[:space:]]*\.\./|source:[[:space:]]*\.\./' "$COMPOSE_FILE" || true
)

if [ -n "$violations" ]; then
    cat >&2 <<EOF
❌ preflight failed: sibling-directory mount(s) in docker-compose.yml.

Found:
$violations

Per issue #53, the main compose file must not depend on '../...' paths
(other environments may not have those siblings cloned). Move these
mounts to docker-compose.override.yml (gitignored).

  See: https://github.com/devyoon91/az-cliproxy-docker/issues/53
       README.md → "로컬 커스텀 마운트 (docker-compose.override.yml)"

EOF
    exit 1
fi

if [ "$QUIET" -eq 0 ]; then
    echo "✓ preflight: no sibling-directory mounts in docker-compose.yml"
fi
