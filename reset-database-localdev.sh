#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

# Reset PostgreSQL database for mataroa local development
# This script removes the database and user

echo "==> Resetting PostgreSQL database for local development..."

# Default values (must match setup script)
DB_NAME="mataroa"
DB_USER="mataroa"

echo "This will delete:"
echo "  Database: ${DB_NAME}"
echo "  User: ${DB_USER}"
echo ""

# Check if PostgreSQL is running
if ! psql -U postgres -c '\q' 2>/dev/null; then
    echo "ERROR: Cannot connect to PostgreSQL."
    echo "Make sure PostgreSQL is installed and running."
    exit 1
fi

# Drop database if it exists
echo "Dropping database: ${DB_NAME}..."
psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'" | grep -q 1 && \
psql -U postgres -c "DROP DATABASE ${DB_NAME};" || \
echo "  Database ${DB_NAME} does not exist, skipping."

# Drop user if it exists
echo "Dropping user: ${DB_USER}..."
psql -U postgres -tc "SELECT 1 FROM pg_user WHERE usename = '${DB_USER}'" | grep -q 1 && \
psql -U postgres -c "DROP USER ${DB_USER};" || \
echo "  User ${DB_USER} does not exist, skipping."

echo ""
echo "==> Database reset completed successfully!"
