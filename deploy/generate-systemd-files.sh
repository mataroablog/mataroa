#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

# Generate systemd service and timer files from templates
# Requires environment variables to be set for template substitution

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATES_DIR="${SCRIPT_DIR}/templates"
OUTPUT_DIR="${SCRIPT_DIR}/generated"

# Check if envsubst is available
if ! command -v envsubst &> /dev/null; then
    echo "ERROR: envsubst command not found. Please install gettext-base package."
    exit 1
fi

# Required environment variables
REQUIRED_VARS=(
    "DOMAIN"
    "ADMIN_EMAIL"
    "DEBUG"
    "LOCALDEV"
    "SECRET_KEY"
    "DATABASE_URL"
    "POSTGRES_USERNAME"
    "POSTGRES_PASSWORD"
    "BACKUP_BUCKET"
    "EMAIL_HOST_USER"
    "EMAIL_HOST_PASSWORD"
    "CUSTOM_DOMAIN_IP"
    "STRIPE_API_KEY"
    "STRIPE_PUBLIC_KEY"
    "STRIPE_PRICE_ID"
    "STRIPE_WEBHOOK_SECRET"
)

SUBST_VARS="$(printf '$%s ' "${REQUIRED_VARS[@]}")"

# Check required variables
echo "==> Checking required environment variables..."
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: Required environment variable ${var} is not set"
        exit 1
    fi
done

# Create output directory
mkdir -p "${OUTPUT_DIR}"

echo "==> Generating systemd files from templates..."

# List of template files
TEMPLATE_FILES=(
    "mataroa.service"
    "mataroa.env"
    "Caddyfile"
    "caddy.service"
    "mataroa-notifications.timer"
    "mataroa-notifications.service"
    "mataroa-exports.timer"
    "mataroa-exports.service"
    "mataroa-backup.timer"
    "mataroa-backup.service"
    "mataroa-dailysummary.timer"
    "mataroa-dailysummary.service"
)

# Process each template file
for template in "${TEMPLATE_FILES[@]}"; do
    input_file="${TEMPLATES_DIR}/${template}"
    output_file="${OUTPUT_DIR}/${template}"

    if [ ! -f "${input_file}" ]; then
        echo "WARNING: Template file not found: ${input_file}"
        continue
    fi

    echo "  Processing: ${template}"
    envsubst "${SUBST_VARS}" < "${input_file}" > "${output_file}"
done

echo "==> Generated files written to: ${OUTPUT_DIR}"
