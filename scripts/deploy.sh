#!/usr/bin/env bash
# Deploy with automatic rollback. Run on the server by .github/workflows/deploy.yml.
#
# - Build fails  -> exit 1; the running bot is never touched.
# - New version never reports healthy (/health) within HEALTH_TIMEOUT
#                -> restore the previously running image, exit 1 (workflow goes red).
# - Healthy      -> exit 0. The prior image stays tagged as $IMAGE:previous for manual rollback.
#
# Database migrations are not rolled back; keep them backwards-compatible.
# The overrides below exist so the rollback path can be tested against a dummy service.
set -euo pipefail

DIR=${DEPLOY_DIR:-/home/ubuntu/serenity}
SERVICE=${DEPLOY_SERVICE:-serenity}
CONTAINER=${DEPLOY_CONTAINER:-serenity}
IMAGE=${DEPLOY_IMAGE:-serenity-serenity}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-180}

cd "$DIR"

wait_healthy() {
    local deadline=$((SECONDS + HEALTH_TIMEOUT))
    while ((SECONDS < deadline)); do
        [[ $(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null) == healthy ]] && return 0
        sleep 5
    done
    return 1
}

# Remember what's running now, so we can go back to it.
if prev=$(docker inspect -f '{{.Image}}' "$CONTAINER" 2>/dev/null); then
    docker tag "$prev" "$IMAGE:previous"
fi

docker compose build "$SERVICE"

if docker compose up -d && wait_healthy; then
    docker image prune -f >/dev/null
    echo "Deploy healthy."
    exit 0
fi

echo "New version did not become healthy within ${HEALTH_TIMEOUT}s. Last logs:"
docker logs --tail 40 "$CONTAINER" 2>&1 || true

if [[ -z ${prev:-} ]]; then
    echo "No previous image to roll back to."
    exit 1
fi

echo "Rolling back to the previous image ($prev)..."
docker tag "$IMAGE:previous" "$IMAGE:latest"
docker compose up -d --no-build --force-recreate "$SERVICE"
if wait_healthy; then
    echo "Rolled back: the previous version is serving. The new version was NOT deployed."
else
    echo "Rollback is not healthy either. Check the server."
fi
exit 1
