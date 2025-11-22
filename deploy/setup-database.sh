#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

# Setup PostgreSQL database for mataroa
# Requires POSTGRES_USERNAME and POSTGRES_PASSWORD environment variables

if [ -z "${POSTGRES_USERNAME:-}" ]; then
    echo "ERROR: POSTGRES_USERNAME environment variable is required"
    exit 1
fi

if [ -z "${POSTGRES_PASSWORD:-}" ]; then
    echo "ERROR: POSTGRES_PASSWORD environment variable is required"
    exit 1
fi

# Create PostgreSQL user
echo "Creating PostgreSQL user: ${POSTGRES_USERNAME}..."
sudo -u postgres psql -tc "SELECT 1 FROM pg_user WHERE usename = '${POSTGRES_USERNAME}'" | grep -q 1 || \
sudo -u postgres psql -c "CREATE USER ${POSTGRES_USERNAME} WITH PASSWORD '${POSTGRES_PASSWORD}';"

# Create database
echo "Creating database: mataroa..."
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = 'mataroa'" | grep -q 1 || \
sudo -u postgres psql -c "CREATE DATABASE mataroa OWNER ${POSTGRES_USERNAME};"

# Grant permissions
echo "Granting permissions..."
sudo -u postgres psql -d mataroa -c "GRANT ALL PRIVILEGES ON DATABASE mataroa TO ${POSTGRES_USERNAME};"
sudo -u postgres psql -d mataroa -c "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ${POSTGRES_USERNAME};"
sudo -u postgres psql -d mataroa -c "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ${POSTGRES_USERNAME};"
sudo -u postgres psql -d mataroa -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ${POSTGRES_USERNAME};"
sudo -u postgres psql -d mataroa -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ${POSTGRES_USERNAME};"

echo "==> PostgreSQL database setup completed successfully!"
