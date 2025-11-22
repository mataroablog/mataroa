#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

# Main provisioning script for mataroa, tested with Debian 13
# This script sets up a fresh server with all required dependencies and configurations

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load configuration from .envrc if it exists
if [ -f "${SCRIPT_DIR}/.envrc" ]; then
    echo "==> Loading configuration from .envrc..."
    # shellcheck disable=SC1091
    source "${SCRIPT_DIR}/.envrc"
else
    echo "ERROR: .envrc file not found. Copy .envrc.example to .envrc and configure it."
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

echo "==> Provisioning mataroa on ${SERVER_IP} as ${SERVER_USER}..."
echo ""

# Generate systemd files
echo "==> Generating systemd configuration files..."
"${SCRIPT_DIR}/generate-systemd-files.sh"
SYSTEMD_FILES="${SCRIPT_DIR}/generated"

# Function to run commands on remote server
run_remote() {
    ssh -o StrictHostKeyChecking=no "${SERVER_USER}@${SERVER_IP}" "$@"
}

# 1. Install essential packages
echo "==> Installing essential packages..."
run_remote "apt-get update && apt-get install -y gcc git rclone vim postgresql postgresql-contrib curl gettext-base"

# 2. Install Caddy
echo "==> Installing Caddy..."
run_remote "bash -s" < "${SCRIPT_DIR}/install-caddy.sh"

# 3. Create deploy user
echo "==> Creating deploy user..."
run_remote "
    if ! id -u deploy &>/dev/null; then
        useradd -m -s /bin/bash -G sudo,www-data deploy
        mkdir -p /home/deploy/.ssh
        ssh-keygen -t ed25519 -f /home/deploy/.ssh/id_ed25519 -N ''
        chown -R deploy:deploy /home/deploy/.ssh
        echo 'deploy user created successfully'
    else
        echo 'deploy user already exists'
    fi
"

# 4. Create /var/www directory
echo "==> Creating /var/www directory..."
run_remote "mkdir -p /var/www && chown deploy:www-data /var/www && chmod 755 /var/www"

# 5. Setup PostgreSQL database
echo "==> Setting up PostgreSQL database..."
run_remote "
    export POSTGRES_USERNAME='${POSTGRES_USERNAME}'
    export POSTGRES_PASSWORD='${POSTGRES_PASSWORD}'
    bash -s
" < "${SCRIPT_DIR}/setup-database.sh"

# 6. Install uv as deploy user
echo "==> Installing uv for deploy user..."
run_remote "sudo -u deploy bash -c 'curl -LsSf https://astral.sh/uv/0.9.11/install.sh | sh'"

# 7. Clone repository
echo "==> Cloning mataroa repository..."
run_remote "
    if [ -d /var/www/mataroa ]; then
        echo 'Repository already exists, pulling latest changes...'
        sudo -u deploy bash -c 'cd /var/www/mataroa && git pull'
    else
        sudo -u deploy git clone https://github.com/mataroablog/mataroa /var/www/mataroa
    fi
"

# 8. Install Python dependencies
echo "==> Installing Python dependencies..."
run_remote "sudo -u deploy bash -c 'cd /var/www/mataroa && /home/deploy/.local/bin/uv sync --all-groups'"

# 9. Deploy systemd files
echo "==> Deploying systemd configuration files..."
cat "${SYSTEMD_FILES}/mataroa.service" | run_remote "cat > /etc/systemd/system/mataroa.service"
cat "${SYSTEMD_FILES}/mataroa.env" | run_remote "cat > /etc/systemd/system/mataroa.env && chmod 640 /etc/systemd/system/mataroa.env"
cat "${SYSTEMD_FILES}/Caddyfile" | run_remote "cat > /etc/caddy/Caddyfile"
cat "${SYSTEMD_FILES}/caddy.service" | run_remote "cat > /etc/systemd/system/caddy.service"
cat "${SYSTEMD_FILES}/mataroa-notifications.timer" | run_remote "cat > /etc/systemd/system/mataroa-notifications.timer"
cat "${SYSTEMD_FILES}/mataroa-notifications.service" | run_remote "cat > /etc/systemd/system/mataroa-notifications.service"
cat "${SYSTEMD_FILES}/mataroa-exports.timer" | run_remote "cat > /etc/systemd/system/mataroa-exports.timer"
cat "${SYSTEMD_FILES}/mataroa-exports.service" | run_remote "cat > /etc/systemd/system/mataroa-exports.service"
cat "${SYSTEMD_FILES}/mataroa-backup.timer" | run_remote "cat > /etc/systemd/system/mataroa-backup.timer"
cat "${SYSTEMD_FILES}/mataroa-backup.service" | run_remote "cat > /etc/systemd/system/mataroa-backup.service"
cat "${SYSTEMD_FILES}/mataroa-dailysummary.timer" | run_remote "cat > /etc/systemd/system/mataroa-dailysummary.timer"
cat "${SYSTEMD_FILES}/mataroa-dailysummary.service" | run_remote "cat > /etc/systemd/system/mataroa-dailysummary.service"

# 10. Setup rclone
echo "==> Setting up rclone..."
echo "==> Setting up rclone..."
run_remote "
    export RCLONE_ACCESS_KEY_ID='${RCLONE_ACCESS_KEY_ID:-}'
    export RCLONE_SECRET_ACCESS_KEY='${RCLONE_SECRET_ACCESS_KEY:-}'
    export RCLONE_REGION='${RCLONE_REGION:-}'
    export RCLONE_ENDPOINT='${RCLONE_ENDPOINT:-}'
    bash -s
" < "${SCRIPT_DIR}/setup-rclone.sh"

# 11. Run Django migrations and collectstatic
echo "==> Running Django migrations..."
run_remote "sudo -u deploy bash -c 'cd /var/www/mataroa && export DATABASE_URL=\"${DATABASE_URL}\" && /home/deploy/.local/bin/uv run python manage.py migrate --no-input'"

echo "==> Collecting static files..."
run_remote "sudo -u deploy bash -c 'cd /var/www/mataroa && /home/deploy/.local/bin/uv run python manage.py collectstatic --no-input'"

# 12. Reload systemd and enable services
echo "==> Enabling and starting systemd services..."
run_remote "
    systemctl daemon-reload
    systemctl enable mataroa
    systemctl enable caddy
    systemctl enable mataroa-notifications.timer
    systemctl enable mataroa-exports.timer
    systemctl enable mataroa-backup.timer
    systemctl enable mataroa-dailysummary.timer
    systemctl start mataroa-notifications.timer
    systemctl start mataroa-exports.timer
    systemctl start mataroa-backup.timer
    systemctl start mataroa-dailysummary.timer
    systemctl start mataroa
    systemctl start caddy
"

echo ""
echo "==> ✓ Provisioning completed successfully!"
