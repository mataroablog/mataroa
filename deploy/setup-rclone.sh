#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

# Setup rclone configuration for database backups
# Creates ~/.config/rclone/rclone.conf

if [ -z "${RCLONE_ACCESS_KEY_ID:-}" ] || [ -z "${RCLONE_SECRET_ACCESS_KEY:-}" ]; then
    echo "WARNING: RCLONE_ACCESS_KEY_ID or RCLONE_SECRET_ACCESS_KEY not set, skipping rclone setup"
    exit 0
fi

echo "==> Setting up rclone configuration..."

mkdir -p /home/deploy/.config/rclone

cat > /home/deploy/.config/rclone/rclone.conf <<EOF
[scaleway]
type = s3
provider = ${RCLONE_PROVIDER}
access_key_id = ${RCLONE_ACCESS_KEY_ID}
secret_access_key = ${RCLONE_SECRET_ACCESS_KEY}
region = ${RCLONE_REGION}
endpoint = ${RCLONE_ENDPOINT}
acl = private
storage_class = STANDARD
EOF

chown -R deploy:deploy /home/deploy/.config
chmod 600 /home/deploy/.config/rclone/rclone.conf

echo "==> rclone configuration created successfully"
