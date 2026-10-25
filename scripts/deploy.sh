#!/usr/bin/env bash
# Deploy with automatic rollback. Run on the server by .github/workflows/deploy.yml.
#
# - Build fails  -> exit 1; the running bot is never touched.
# - New version doesn't become healthy (/health) within HEALTH_TIMEOUT, or doesn't stay
#   healthy for STABLE_SECONDS -> restore the last good image *and* compose file, exit 1
#   (the workflow goes red).
# - Healthy and stable -> save this image and compose file as the new last-good, exit 0.
#
# Not rolled back: database migrations, and changes to other services (e.g. the postgres image or
# the dashboard), since only $SERVICE is recreated. Keep both backwards-compatible.
# The overrides below exist so the rollback path can be tested against a dummy service.
set -euo pipefail

DIR=${DEPLOY_DIR:-/home/ubuntu/serenity}
SERVICE=${DEPLOY_SERVICE:-serenity}
CONTAINER=${DEPLOY_CONTAINER:-serenity}
IMAGE=${DEPLOY_IMAGE:-serenity-serenity}
STATE=${DEPLOY_STATE:-/home/ubuntu/.serenity-deploy}  # outside $DIR: the copy step overwrites $DIR
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-180}
STABLE_SECONDS=${STABLE_SECONDS:-30}

cd "$DIR"
mkdir -p "$STATE"

state() {  # "<health or running> <restart count> <started at>"; empty if no container
    docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else if .State.Running}}running{{end}} {{.RestartCount}} {{.State.StartedAt}}' "$CONTAINER" 2>/dev/null || true
}

# Healthy within HEALTH_TIMEOUT, then unchanged (no restart, still healthy) for STABLE_SECONDS.
wait_healthy() {
    local deadline=$((SECONDS + HEALTH_TIMEOUT)) first
    while ((SECONDS < deadline)); do
        first=$(state)
        if [[ $first == healthy\ * || $first == running\ * ]]; then
            sleep "$STABLE_SECONDS"
            [[ $(state) == "$first" ]] && return 0
            echo "Became healthy but didn't stay that way: '$first' -> '$(state)'"
            return 1
        fi
        sleep 5
    done
    return 1
}

prev=$(docker inspect -f '{{.Image}}' "$CONTAINER" 2>/dev/null || true)
[[ -n $prev ]] && docker tag "$prev" "$IMAGE:previous"

docker compose build  # every service, so dashboard changes ship too

if docker compose up -d; then
    if wait_healthy; then
        # Compose file first, then the tag, so a failed copy can't pair a new image with old config.
        cp docker-compose.yml "$STATE/docker-compose.yml.tmp" && mv "$STATE/docker-compose.yml.tmp" "$STATE/docker-compose.yml"
        docker tag "$IMAGE:latest" "$IMAGE:last-good"
        docker image prune -f >/dev/null
        echo "Deploy healthy."
        exit 0
    fi
    echo "New version did not become (and stay) healthy. Last logs:"
    docker logs --tail 40 "$CONTAINER" 2>&1 || true
else
    echo "docker compose up failed."
    # Compose often fails before touching anything; if the old version is still serving, leave it.
    # (Only $CONTAINER is checked: if the bot came up on the new image but another service failed,
    # this falls through to a rollback. Conservative on purpose for a failed deploy.)
    if [[ -n $prev && $(docker inspect -f '{{.Image}}' "$CONTAINER" 2>/dev/null) == "$prev" ]] && wait_healthy; then
        echo "The previous version is still running and healthy; nothing to roll back. The new version was NOT deployed."
        exit 1
    fi
fi

# Prefer the last version that passed this check; fall back to whatever ran before this deploy.
if docker image inspect "$IMAGE:last-good" >/dev/null 2>&1; then
    target=last-good
elif [[ -n $prev ]]; then
    target=previous
else
    echo "No previous image to roll back to."
    exit 1
fi
compose=(docker compose)
if [[ -f $STATE/docker-compose.yml ]]; then
    compose=(docker compose --project-directory "$DIR" -f "$STATE/docker-compose.yml")
else
    echo "No saved compose file yet; rolling back the image with the current one."
fi

echo "Rolling back to $IMAGE:$target..."
docker tag "$IMAGE:$target" "$IMAGE:latest"
if "${compose[@]}" up -d --no-build --force-recreate "$SERVICE" && wait_healthy; then
    echo "Rolled back: the previous version is serving. The new version was NOT deployed."
else
    echo "Rollback is not healthy either. Check the server."
fi
exit 1
