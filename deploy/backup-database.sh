#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

# Backup PostgreSQL database and upload to S3-compatible storage

# Check dependencies
if ! command -v rclone &> /dev/null; then
    echo "ERROR: rclone command not found."
    exit 1
fi

if ! command -v pg_dump &> /dev/null; then
    echo "ERROR: pg_dump command not found."
    exit 1
fi

# Check required variables
if [ -z "${BACKUP_BUCKET:-}" ]; then
    echo "ERROR: BACKUP_BUCKET environment variable is not set"
    exit 1
fi

if [ -z "${DATABASE_URL:-}" ] && [ -z "${PGPASSWORD:-}" ]; then
    echo "WARNING: Neither DATABASE_URL nor PGPASSWORD is set."
fi

echo "==> Starting database backup..."

# Generate timestamp
TIMESTAMP="$(date --utc +%Y%m%d-%H%M%S)"
DUMP_FILE="/var/tmp/mataroa.dump"

# Dump database
echo "  Dumping database..."
# Use DATABASE_URL if available, otherwise rely on PGPASSWORD/PGHOST/PGUSER
if [ -n "${DATABASE_URL:-}" ]; then
    pg_dump -Fc --no-acl "${DATABASE_URL}" -f "${DUMP_FILE}" -w
else
    pg_dump -Fc --no-acl mataroa -h localhost -U mataroa -f "${DUMP_FILE}" -w
fi

# Upload using rclone
echo "  Uploading to ${BACKUP_BUCKET}..."
rclone copy --stats 5m --stats-one-line "${DUMP_FILE}" "offsite-backup:${BACKUP_BUCKET}/mataroa-backups/postgres-mataroa-${TIMESTAMP}/"

# Delete old backups on remote
echo "  Deleting backups older than 20 days..."
rclone delete "offsite-backup:${BACKUP_BUCKET}/mataroa-backups" --min-age 20d --rmdirs --include "postgres-mataroa-*/mataroa.dump"

echo "==> Backup completed successfully!"
