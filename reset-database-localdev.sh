#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

# Reset PostgreSQL database for mataroa local development
# This script removes and recreates the database and user

echo "==> Resetting PostgreSQL database for local development..."

# Defaults match the PostgreSQL service in docker-compose.yml.
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_ADMIN_USER="${POSTGRES_ADMIN_USER:-postgres}"
POSTGRES_ADMIN_DATABASE="${POSTGRES_ADMIN_DATABASE:-postgres}"
POSTGRES_ADMIN_PASSWORD="${POSTGRES_ADMIN_PASSWORD:-postgres}"
DB_NAME="${DB_NAME:-mataroa}"
DB_USER="${DB_USER:-mataroa}"
DB_PASSWORD="${DB_PASSWORD:-mataroa}"

if [[ ! "${DB_NAME}" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] ||
    [[ ! "${DB_USER}" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] ||
    [[ ! "${DB_PASSWORD}" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "ERROR: Database name, user, or password contains unsupported characters."
    exit 1
fi

export PGPASSWORD="${POSTGRES_ADMIN_PASSWORD}"
PSQL=(
    psql
    -v ON_ERROR_STOP=1
    -h "${POSTGRES_HOST}"
    -p "${POSTGRES_PORT}"
    -U "${POSTGRES_ADMIN_USER}"
    -d "${POSTGRES_ADMIN_DATABASE}"
)

echo "This will delete:"
echo "  Database: ${DB_NAME}"
echo "  User: ${DB_USER}"
echo "Both will be recreated with empty data."
echo ""

# Check if PostgreSQL is running
if ! "${PSQL[@]}" -c '\q' 2>/dev/null; then
    echo "ERROR: Cannot connect to PostgreSQL."
    echo "Make sure PostgreSQL is running and the administrator settings are correct."
    exit 1
fi

# Drop database if it exists
echo "Dropping database: ${DB_NAME}..."
if [[ "$("${PSQL[@]}" -Atc "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'")" == "1" ]]; then
    "${PSQL[@]}" -c "DROP DATABASE ${DB_NAME} WITH (FORCE);"
else
    echo "  Database ${DB_NAME} does not exist, skipping."
fi

# Drop user if it exists
echo "Dropping user: ${DB_USER}..."
if [[ "$("${PSQL[@]}" -Atc "SELECT 1 FROM pg_roles WHERE rolname = '${DB_USER}'")" == "1" ]]; then
    "${PSQL[@]}" -c "DROP USER ${DB_USER};"
else
    echo "  User ${DB_USER} does not exist, skipping."
fi

# Recreate the application user and database. CREATEDB is required by Django tests.
echo "Creating PostgreSQL user: ${DB_USER}..."
"${PSQL[@]}" -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}' CREATEDB;"

echo "Creating database: ${DB_NAME}..."
"${PSQL[@]}" -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"

echo ""
echo "==> Database reset completed successfully!"
echo ""
echo "Connection string for .envrc:"
echo "  export DATABASE_URL=postgres://${DB_USER}:${DB_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${DB_NAME}"
