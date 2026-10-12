#!/usr/bin/env bash
# ============================================================================
#  Mewvia task runner.  `make` is not required.
#  Usage:  ./dev.sh <command>       e.g.  ./dev.sh up
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

DC="docker compose"

# Tooling runs in throwaway containers rather than `exec` into the running bot:
# the bot may legitimately be down (bad token, crash loop) and lint/tests must
# still work. `--no-deps` skips postgres/redis for checks that don't need them.
RUN_ISOLATED="$DC run --rm --no-deps -T bot"
RUN_WITH_DEPS="$DC run --rm -T bot"

usage() {
    cat <<'EOF'
Mewvia — available commands

  up            Build and start every service in the background
  down          Stop services (volumes are kept)
  reset         Stop services AND delete volumes (destroys the database)
  rebuild       Rebuild the bot image from scratch and restart
  logs          Follow the bot logs
  logs-all      Follow logs from every service
  ps            Show service status
  sh            Open a shell in a throwaway container (works even if the bot is down)
  psql          Open psql against the project database

  test          Run pytest inside the container
  lint          Run ruff check + ruff format --check
  fmt           Apply ruff formatting and fix what it can
  types         Run mypy
  check         lint + types + test

  migrate       Apply pending Alembic migrations, then seed
  seed          Re-apply seed data only (idempotent)
  drift         Check the models match the migrations (alembic check)
  revision M    Autogenerate a migration, e.g. ./dev.sh revision "add users"
  downgrade     Roll back one migration

  doctor        Verify the toolchain inside the container (ffmpeg, rlottie, DB, Redis)
EOF
}

need_env() {
    if [[ ! -f .env ]]; then
        echo "ERROR: .env is missing. Run:  cp .env.example .env  then fill in BOT_TOKEN." >&2
        exit 1
    fi
}

cmd="${1:-help}"
shift || true

case "$cmd" in
    up)        need_env; $DC up -d --build; $DC ps ;;
    down)      $DC down ;;
    reset)     $DC down -v ;;
    rebuild)   need_env; $DC build --no-cache bot; $DC up -d; $DC ps ;;
    logs)      $DC logs -f --tail=200 bot ;;
    logs-all)  $DC logs -f --tail=100 ;;
    ps)        $DC ps ;;
    sh)        $DC run --rm --no-deps bot bash ;;
    psql)      $DC exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' ;;

    # Tests talk to the real PostgreSQL (ON CONFLICT / RETURNING / FILTER are not
    # simulated), so they need the deps up. Lint and types do not.
    test)      $RUN_WITH_DEPS pytest "$@" ;;
    lint)      $RUN_ISOLATED sh -c 'ruff check . && ruff format --check .' ;;
    fmt)       $RUN_ISOLATED sh -c 'ruff format . && ruff check --fix .' ;;
    types)     $RUN_ISOLATED mypy app ;;
    # One container for all of it: separate `compose run` invocations cost more in
    # startup than the checks themselves.
    check)     $RUN_WITH_DEPS sh -c 'ruff check . && ruff format --check . && mypy app && pytest' ;;

    migrate)   $RUN_WITH_DEPS sh -c 'alembic upgrade head && python -m app.db.seed' ;;
    seed)      $RUN_WITH_DEPS python -m app.db.seed ;;
    drift)     $RUN_WITH_DEPS alembic check ;;
    revision)
        msg="${1:-}"
        [[ -z "$msg" ]] && { echo 'Usage: ./dev.sh revision "message"' >&2; exit 1; }
        # Autogenerate writes into the bind-mounted migrations/ directory, so the
        # generated file lands on the host.
        $RUN_WITH_DEPS alembic revision --autogenerate -m "$msg"
        ;;
    downgrade) $RUN_WITH_DEPS alembic downgrade -1 ;;

    doctor)    $RUN_WITH_DEPS python -m app.doctor ;;

    help|-h|--help) usage ;;
    *) echo "Unknown command: $cmd" >&2; echo; usage; exit 1 ;;
esac
