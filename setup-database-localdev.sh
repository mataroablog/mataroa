#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

# Setup PostgreSQL database for mataroa local development
# This script creates a database on your local PostgreSQL installation

echo "==> Setting up PostgreSQL database for local development..."

# Default values for local development
DB_NAME="mataroa"
DB_USER="mataroa"

echo "Database configuration:"
echo "  Database: ${DB_NAME}"
echo "  User: ${DB_USER} (no password for local dev)"
echo ""

# Check if PostgreSQL is running
if ! psql -U postgres -c '\q' 2>/dev/null; then
    echo "ERROR: Cannot connect to PostgreSQL."
    echo "Make sure PostgreSQL is installed and running."
    exit 1
fi

# Create PostgreSQL user if it doesn't exist (no password for local dev)
echo "Creating PostgreSQL user: ${DB_USER}..."
psql -U postgres -tc "SELECT 1 FROM pg_user WHERE usename = '${DB_USER}'" | grep -q 1 || \
psql -U postgres -c "CREATE USER ${DB_USER};"

# Grant CREATEDB privilege (needed for running tests)
psql -U postgres -c "ALTER USER ${DB_USER} WITH CREATEDB;"

# Create database if it doesn't exist
echo "Creating database: ${DB_NAME}..."
psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'" | grep -q 1 || \
psql -U postgres -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"

echo "Database owner set to ${DB_USER}"

echo ""
echo "==> PostgreSQL database setup completed successfully!"
echo ""
echo "Connection string for .envrc:"
echo "  export DATABASE_URL=postgres://${DB_USER}@localhost:5432/${DB_NAME}"
