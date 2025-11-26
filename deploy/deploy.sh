#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

# Deploy script
# This script connects to the mataroa server via SSH, pulls latest commits, reloads service.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load configuration from .envrc if it exists
if [ -f "${SCRIPT_DIR}/.envrc" ]; then
    echo "==> Loading configuration from .envrc..."
    # shellcheck disable=SC1091
    source "${SCRIPT_DIR}/.envrc"
else
    echo "ERROR: .envrc file not found. Please copy .envrc.example to .envrc and configure it."
    exit 1
fi

# Check required variables
if [ -z "${SERVER_IP:-}" ]; then
    echo "ERROR: SERVER_IP is not set in .envrc"
    exit 1
fi

if [ -z "${SERVER_USER:-}" ]; then
    echo "ERROR: SERVER_USER is not set in .envrc"
    exit 1
fi

echo "==> Deploying mataroa updates to ${SERVER_IP}..."
echo ""

# Function to run commands on remote server
run_remote() {
    ssh -o StrictHostKeyChecking=no "${SERVER_USER}@${SERVER_IP}" "$@"
}

# 1. Pull latest code
echo "==> Pulling latest code from git..."
run_remote "sudo -u deploy bash -c 'cd /var/www/mataroa && git pull'"

# 2. Update dependencies
echo "==> Updating Python dependencies..."
run_remote "sudo -u deploy bash -c 'cd /var/www/mataroa && /home/deploy/.local/bin/uv sync --all-groups'"

# 3. Run migrations
echo "==> Running database migrations..."
run_remote "sudo -u deploy bash -c 'cd /var/www/mataroa && export DATABASE_URL=\"${DATABASE_URL}\" && /home/deploy/.local/bin/uv run python manage.py migrate --no-input'"

# 4. Collect static files
echo "==> Collecting static files..."
run_remote "sudo -u deploy bash -c 'cd /var/www/mataroa && /home/deploy/.local/bin/uv run python manage.py collectstatic --no-input'"

# 5. Reload gunicorn
echo "==> Reloading mataroa service..."
run_remote "systemctl reload mataroa"

echo ""
echo "==> ✓ Deployment completed successfully!"
