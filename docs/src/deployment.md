# Deployment

## 1. Choose domain

Let's assume `example.com` is your domain of choice, for the purposes of this
guide.

## 2. Setup DNS

Setup both `example.com` and `*.example.com` to point the mataroa server IP.

## 3. Provision server

(3a) First, set up config environment for provisioning:

```sh
cd deploy/

# make a copy of the example file
cp .envrc.example .envrc

# edit parameters as required
vim .envrc

# load variables into environment
source .envrc
```

(3b) Then, run the provisioning script:

```sh
./provision.sh
```

This script will:

* install essential packages (gcc, git, rclone, vim, postgresql)
* install and configure Caddy web server
* create the deploy user with proper permissions
* set up the postgresql database
* install uv and clone the mataroa repository
* configure all systemd services and timers
* run initial Django migrations and collect static files
* enable and start all systemd services

Note: Caddy will automatically obtain and manage SSL certificates for your
domain and all subdomains using Let's Encrypt. The first certificate request
happens when a domain is first accessed.

## 4. Future deployments

Running `./deploy.sh` will connect to the server and:

* pull the latest code from git
* update Python dependencies
* run database migrations
* collect static files
* reload the gunicorn service
