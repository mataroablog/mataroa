#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

# Install Caddy web server
# This script adds the Caddy repository and installs Caddy via apt

echo "==> Installing Caddy web server..."

# Check if Caddy is already installed
if command -v caddy &> /dev/null; then
    echo "Caddy is already installed, skipping..."
    exit 0
fi

# Add Caddy GPG key
echo "Adding Caddy GPG key..."
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /etc/apt/trusted.gpg.d/caddy-stable.gpg

# Add Caddy repositories
echo "Adding Caddy repositories..."
echo "deb [signed-by=/etc/apt/trusted.gpg.d/caddy-stable.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" > /etc/apt/sources.list.d/caddy-stable.list
echo "deb-src [signed-by=/etc/apt/trusted.gpg.d/caddy-stable.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" >> /etc/apt/sources.list.d/caddy-stable.list

# Update package list and install Caddy
echo "Installing Caddy..."
apt-get update
apt-get install -y caddy

echo "==> Caddy installation completed successfully!"
