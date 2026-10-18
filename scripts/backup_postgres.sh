#!/usr/bin/env bash
# Nightly Postgres backup (issue #17): pg_dump -> local copy (last 7 kept) -> OCI Object Storage.
#
# Runs from the ubuntu user's crontab on the server:
#   30 3 * * * bash /home/ubuntu/serenity/scripts/backup_postgres.sh >> /home/ubuntu/serenity-backups/backup.log 2>&1
#
# Needs BACKUP_PAR_URL in ~/serenity-backups/.env: a write-only pre-authenticated request
# for the bucket (ends in /o/). Bucket-side lifecycle rule deletes objects after 30 days.
# Restore steps: see "Backups" in README.md.
set -euo pipefail
umask 077  # dumps contain user data

BACKUP_DIR=/home/ubuntu/serenity-backups  # outside ~/serenity so dumps stay out of the docker build context
KEEP_LOCAL=7

source "$BACKUP_DIR/.env"

name="serenity-$(date -u +%Y%m%dT%H%M%SZ).dump"
tmp="$BACKUP_DIR/.$name.partial"
trap 'rm -f "$tmp"' EXIT

# pg_dump inside the container so its version always matches the server
docker exec serenity-postgres sh -c 'pg_dump --format=custom -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$tmp"
mv "$tmp" "$BACKUP_DIR/$name"

# Prune before uploading so local copies stay bounded even if uploads keep failing.
ls -1t "$BACKUP_DIR"/serenity-*.dump | tail -n +$((KEEP_LOCAL + 1)) | xargs -r rm --

curl --fail --silent --show-error --retry 3 --connect-timeout 10 --max-time 600 \
    --upload-file "$BACKUP_DIR/$name" "${BACKUP_PAR_URL}${name}"
echo "$(date -u +%FT%TZ) uploaded $name ($(stat -c %s "$BACKUP_DIR/$name") bytes)"
