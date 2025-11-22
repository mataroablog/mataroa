# mataroa

Naked blogging platform.

## Table of Contents

- [Community](#community)
- [Contributing](#contributing)
- [Development](#development)
    - [Structure](#structure)
    - [Subdomains](#subdomains)
    - [Docker](#docker)
    - [Dependencies](#dependencies)
    - [Environment variables](#environment-variables)
    - [Database](#database)
    - [Serve](#serve)
    - [Testing](#testing)
    - [Code linting & formatting](#code-linting--formatting)
- [Architecture](#architecture)
    - [File structure walkthrough](#file-structure-walkthrough)
    - [Coding Conventions](#coding-conventions)
    - [Git Commit Message Guidelines](#git-commit-message-guidelines)
    - [Dependency Policy](#dependency-policy)
- [Operations](#operations)
    - [Deployment](#deployment)
    - [Billing](#billing)
    - [Cronjobs](#cronjobs)
    - [Database Backup](#database-backup)
    - [Feature Release Playbook](#feature-release-playbook)
    - [Server Migration](#server-migration)
    - [On Server Outage](#on-server-outage)
- [License](#license)

## Community

We have a mailing list at
[~sirodoht/mataroa-community@lists.sr.ht](mailto:~sirodoht/mataroa-community@lists.sr.ht)
for the mataroa community to introduce themselves, their blogs, and discuss
anything that’s on their mind!

Archives at
[lists.sr.ht/~sirodoht/mataroa-community](https://lists.sr.ht/~sirodoht/mataroa-community)

### Tools

* [mataroa-cli](https://github.com/mataroablog/mataroa-cli)
* [Mataroa Telegram Bot](https://github.com/jorphex/Mataroa-Telegram-Bot)

## Contributing

**Main repository on GitHub**
[github.com/mataroablog/mataroa](https://github.com/mataroablog/mataroa)

**Mirror repository on sr.ht**
[git.sr.ht/~sirodoht/mataroa](https://git.sr.ht/~sirodoht/mataroa)

**Report bugs on GitHub**
[github.com/mataroablog/mataroa/issues](https://github.com/mataroablog/mataroa/issues)

**Contribute on GitHub with Pull Requests**
[github.com/mataroablog/mataroa/pulls](https://github.com/mataroablog/mataroa/pulls)

Open a PR on [GitHub](https://github.com/mataroablog/mataroa).

Send an email patch to
[~sirodoht/public-inbox@lists.sr.ht](mailto:~sirodoht/public-inbox@lists.sr.ht).
See how to contribute using email patches here:
[git-send-email.io](https://git-send-email.io/).

## Local Development

This is a [Django](https://www.djangoproject.com/) codebase. Check out the
[Django docs](https://docs.djangoproject.com/) for general technical
documentation.

The Django project is [`mataroa`](mataroa). There is one Django app,
[`main`](main), with all business logic. Application CLI commands are generally
divided into two categories, those under `python manage.py` and those under
`make`.

### Subdomains

Mataroa works primarily with subdomain, thus one cannot access the basic web app
using the standard `http://127.0.0.1:8000` or `http://localhost:8000` URLs. What we do
for local development is add a few custom entries on our `/etc/hosts` system file.

Important note: there needs to be an entry of each user account created in the local
development environment, so that the web server can respond to it.

The first line is the main needed: `mataroalocal.blog`. The rest are included as
examples of other users one can create in their local environment. The
easiest way to create them is to go through the sign up page
(`http://mataroalocal.blog:8000/accounts/create/` using default values).

```
# /etc/hosts

127.0.0.1 mataroalocal.blog

127.0.0.1 paul.mataroalocal.blog
127.0.0.1 random.mataroalocal.blog
127.0.0.1 anyusername.mataroalocal.blog
```

This will enable us to access mataroa locally (once we start the web server) at
[http://mataroalocal.blog:8000/](http://mataroalocal.blog:8000/)
and if we make a user account with username `paul`, then we will be able to access it at
[http://paul.mataroalocal.blog:8000/](http://paul.mataroalocal.blog:8000/)

### Docker

> [!NOTE]  
> This is the last step for initial Docker setup. See the "Environment variables"
> section below, for further configuration details.

To set up a development environment with Docker and Docker Compose, run the following
to start the web server and database:

```sh
docker compose up
```

If you have also configured hosts as described above in the "Subdomains"
section, mataroa should now be locally accessible at
[http://mataroalocal.blog:8000/](http://mataroalocal.blog:8000/)

Note: The database data are saved in the git-ignored `docker-postgres-data` docker
volume, located in the root of the project.

### Dependencies

We use `uv` for dependency management and virtual environments.

```sh
uv sync --all-groups
```

See [Dependency Policy](#dependency-policy) for more details on adding/upgrading dependencies.

### Environment variables

A file named `.envrc` is used to define the environment variables required for
this project to function. One can either export it directly or use
[direnv](https://github.com/direnv/direnv). There is an example environment
file one can copy as base:

```sh
cp .envrc.example .envrc
```

When on Docker, to change or populate environment variables, edit the `environment`
key of the `web` service either directly on `docker-compose.yml` or by overriding it
using the standard named git-ignored `docker-compose.override.yml`.

```yaml
# docker-compose.override.yml

services:
  web:
    environment:
      EMAIL_HOST_USER: smtp-user
      EMAIL_HOST_PASSWORD: smtp-password
```

Finally, stop and start `docker compose up` again. It should pick up the override file
as it has the default name `docker-compose.override.yml`.

### Database

This project is using one PostreSQL database for persistence.

One can use the provided script to set up a local Postgres database (user `mataroa`,
passwordless):

```sh
./setup-database-localdev.sh
```

And also easily reset it (drop database and user):

```sh
./reset-database-localdev.sh
```

After setting the `DATABASE_URL` ([see above](#environment-variables)), create
the database schema with:

```sh
uv python manage.py migrate
```

Initialising the database with some sample development data is possible with:

```sh
uv python manage.py loaddata dev-data
```

* `dev-data` is defined in [`main/fixtures/dev-data.json`](main/fixtures/dev-data.json)
* Credentials of the fixtured user are `admin` / `admin`.

### Serve

To run the Django development server:

```sh
uv python manage.py runserver
```

If you have also configured hosts as described above in the "Subdomains"
section, mataroa should now be locally accessible at
[http://mataroalocal.blog:8000/](http://mataroalocal.blog:8000/)

### Testing

Using the Django test runner:

```sh
uv run python manage.py test
```

For coverage, run:

```sh
uv run coverage run --source='.' --omit '.venv/*' manage.py test
uv run coverage report -m
```

### Code linting & formatting

We use [ruff](https://github.com/astral-sh/ruff) for Python code formatting and linting.

To format:

```sh
uv run ruff format
```

To lint:

```sh
uv run ruff check
uv run ruff check --fix
```

## Architecture

### File structure walkthrough

Here, an overview of the project's code sources is presented. The purpose is
for the reader to understand what kind of functionality is located where in
the sources.

All business logic of the application is in one Django app: [`main`](main).

Condensed and commented sources file tree:

```
.
├── .build.yml # SourceHut CI build config
├── .envrc.example # example direnv file
├── .github/ # GitHub Actions config files
├── Caddyfile # configuration for Caddy webserver
├── Dockerfile
├── docker-compose.yml
├── export_base_epub/ # base sources for epub export functionality
├── export_base_hugo/ # base sources for hugo export functionality
├── export_base_zola/ # base sources for zola export functionality
├── main/
│   ├── admin.py
│   ├── apps.py
│   ├── denylist.py # list of various keywords allowed and denied
│   ├── feeds.py # django rss functionality
│   ├── fixtures/
│   │   └── dev-data.json # sample development data
│   ├── forms.py
│   ├── management/ # commands under `python manage.py`
│   │   └── commands/
│   │       ├── checkstripe.py
│   │       ├── mailexports.py
│   │       ├── mailsummary.py
│   │       ├── processnotifications.py
│   │       └── testbulkmail.py
│   ├── middleware.py # mostly subdomain routing
│   ├── migrations/
│   ├── models.py
│   ├── sitemaps.py
│   ├── static/
│   ├── templates
│   │   ├── main/ # HTML templates for most pages
│   │   ├── assets/
│   │   │   ├── drag-and-drop-upload.js
│   │   │   └── style.css
│   │   ├── partials/
│   │   │   ├── footer.html
│   │   │   ├── footer_blog.html
│   │   │   └── webring.html
│   │   └── registration/
│   ├── tests/
│   │   ├── test_api.py
│   │   ├── test_billing.py
│   │   ├── test_blog.py
│   │   ├── test_comments.py
│   │   ├── test_images.py
│   │   ├── test_management.py
│   │   ├── test_pages.py
│   │   ├── test_posts.py
│   │   ├── test_users.py
│   │   └── testdata/
│   ├── text_processing.py # markdown and text utilities
│   ├── urls.py
│   ├── validators.py # custom form and field validators
│   └── views/
│       ├── api.py
│       ├── billing.py
│       ├── export.py
│       ├── general.py
│       └── moderation.py
├── manage.py
└── mataroa
    ├── asgi.py
    ├── settings.py # django configuration file
    ├── urls.py
    └── wsgi.py
```

#### [`main/urls.py`](main/urls.py)

All urls are in this module. They are visually divided into several sections:

* general, includes index, dashboard, static pages
* user system, includes signup, settings, logout
* blog posts, the CRUD operations of
* blog extras, includes rss and newsletter features
* comments, related to the blog post comments
* billing, subscription and card related
* blog import, export, webring
* images CRUD
* analytics list and details
* pages CRUD

#### [`main/views/`](main/views/)

The majority of business logic is organized in the `views/` directory, split across
several modules for better organization.

Generally,
[Django class-based generic views](https://docs.djangoproject.com/en/3.2/topics/class-based-views/generic-display/)
are used most of the time as they provide useful functionality abstracted away.

The Django source code [for generic views](https://github.com/django/django/tree/main/django/views/generic)
is also extremely readable:

* [base.py](https://github.com/django/django/blob/main/django/views/generic/base.py): base `View` and `TemplateView`
* [list.py](https://github.com/django/django/blob/main/django/views/generic/list.py): `ListView`
* [edit.py](https://github.com/django/django/blob/main/django/views/generic/edit.py): `UpdateView`, `DeleteView`, `FormView`
* [detail.py](https://github.com/django/django/blob/main/django/views/generic/detail.py): `DetailView`

[Function-based views](https://docs.djangoproject.com/en/3.2/intro/tutorial01/#write-your-first-view)
are used in cases where the CRUD/RESTful design pattern is not clear such as
`notification_unsubscribe_key` where we unsubscribe an email via a GET operation.

##### [`main/views/general.py`](main/views/general.py)

The main view module containing most of the application's business logic:

* indexes, dashboard, static pages
* user CRUD and login/logout
* posts CRUD
* comments CRUD
* images CRUD
* pages CRUD
* webring
* analytics
* notifications subscribe/unsubscribe

##### [`main/views/api.py`](main/views/api.py)

This module contains all API related views. These views have their own
api key based authentication.

##### [`main/views/export.py`](main/views/export.py)

This module contains all views related to the export capabilities of mataroa.

The way the exports work is by reading the base files from the repository root:
[export_base_hugo](export_base_hugo/), [export_base_zola](export_base_zola/),
[export_base_epub](export_base_epub/) for Hugo, Zola, and epub respectively.
After reading, we replace some strings on the configurations, generate posts
as markdown strings, and zip-archive everything in-memory. Finally, we respond
using the appropriate content type (`application/zip` or `application/epub`) and
[Content-Disposition](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Disposition)
`attachment`.

##### [`main/views/billing.py`](main/views/billing.py)

This module contains all billing and subscription related views. It's designed to
support one payment processor, Stripe.

##### [`main/views/moderation.py`](main/views/moderation.py)

This module contains all views related to moderating the content of the platform.

#### [`main/tests/`](main/tests/)

All tests are under this directory. They are divided into several modules,
based on the functionality and the views they test.

Everything uses the built-in Python `unittest` module along with standard
Django testing facilities.

#### [`main/models.py`](main/models.py) and [`main/migrations/`](main/migrations/)

`main/models.py` is where the database schema is defined, translated into
Django ORM-speak. This always displays the latest schema.

`main/migrations/` includes all incremental migrations required to reach
the schema defined in `main/models.py` starting from an empty database.

We use the built-in Django commands to generate and execute migrations, namely
`makemigrations` and `migrate`. For example, the steps to make a schema change
would be something like:

1. Make the change in `main/models.py`. See
[Django Model field reference](https://docs.djangoproject.com/en/3.2/ref/models/fields/).
1. Run `python manage.py makemigrations` to auto-generate the migrations.
1. Potentially refactor the auto-generated migration file (located at `main/migrations/XXXX_auto_XXXXXXXX.py`)
1. Run `python manage.py migrate` to execute migrations.
1. Also `make format` before committing.

#### [`main/forms.py`](main/forms.py)

Here a collection of Django-based forms resides, mostly in regards to user creation,
upload functionalities (for post import or image upload), and card details
submission.

See [Django Form fields reference](https://docs.djangoproject.com/en/3.2/ref/forms/fields/).

#### [`main/text_processing.py`](main/text_processing.py)

This module contains utilities for processing text content, including markdown
rendering, syntax highlighting, and text transformations used throughout the
application.

#### [`main/templates/assets/style.css`](main/templates/assets/style.css)

On Mataroa, a user can enable an option, Theme Zia Lucia, and get a higher font
size by default. Because we need to change the body font-size value, we render
the CSS. It is not static. This is why it lives inside the templates directory.

### Coding Conventions

1. All files should end with a new line character.
1. Python code should be formatted with [ruff](https://github.com/astral-sh/ruff).

### Git Commit Message Guidelines

We follow some simple non-austere git commit message guidelines.

* Start with a verb
    * `add`
    * `change`
    * `delete`
    * `fix`
    * `refactor`
    * `tweak`
    * et al.
* Start with a lowercase letter
    * eg. `change analytic page path to the same of page slug`
* Do not end with a fullstop

### Dependency Policy

The mataroa project has an unusually strict yet usually unclear dependency policy.

Vague rules include:

* No third-party Django apps.
* All Python / PyPI packages should be individually vetted.
    * Packages should be published from community-trusted organisations or developers.
    * Packages should be actively maintained (though not necessarily actively developed).
    * Packages should hold a high quality of coding practices.
* No JavaScript libraries / dependencies.

Current list of top-level PyPI dependencies (source at [`pyproject.toml`](pyproject.toml)):

* [Django](https://pypi.org/project/Django/)
* [psycopg](https://pypi.org/project/psycopg/)
* [gunicorn](https://pypi.org/project/gunicorn/)
* [Markdown](https://pypi.org/project/Markdown/)
* [Pygments](https://pypi.org/project/Pygments/)
* [bleach](https://pypi.org/project/bleach/)
* [stripe](https://pypi.org/project/stripe/)

#### Adding a new dependency

After approving a dependency, add it using `uv`:

1. Ensure `uv` is installed and a virtualenv exists (managed by `uv`).
1. Add the dependency to `pyproject.toml` and lockfile with:
   - Runtime: `uv add PACKAGE`
   - Dev-only: `uv add --dev PACKAGE`
1. Install/sync dependencies: `uv sync`

#### Upgrading dependencies

When a new Django version is out it’s a good idea to upgrade everything.

Steps:

1. Update the lockfile: `uv lock --upgrade`
1. Review changes: `git diff uv.lock` and spot non-patch level version bumps.
1. Examine release notes of each one.
1. Install updated deps: `uv sync`
1. Unless something comes up, make sure tests and smoke tests pass.
1. Deploy new dependency versions.

## Operations

### Deployment

See the [Deployment](./docs/src/deployment.md) document on how to deploy a
mataroa instance. Briefly, the steps are:

#### 1. Choose domain

Let's assume `example.com` is your domain of choice, for the purposes of this
guide.

#### 2. Setup DNS

Setup both `example.com` and `*.example.com` to point the mataroa server IP.

#### 3. Provision server

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

#### 4. Future deployments

Running `./deploy.sh` will connect to the server and:

* pull the latest code from git
* update Python dependencies
* run database migrations
* collect static files
* reload the gunicorn service

#### Useful Commands

To reload the gunicorn process:

```sh
sudo systemctl reload mataroa
```

To reload Caddy:

```sh
systemctl restart caddy  # root only
```

gunicorn logs:

```sh
journalctl -fb -u mataroa
```

Caddy logs:

```sh
journalctl -fb -u caddy
```

Get an overview with systemd status:

```sh
systemctl status caddy
systemctl status mataroa
```

### Billing

One can deploy mataroa without setting up billing functionalities. This is
the default case. To handle payments and subscriptions this project uses
[Stripe](https://stripe.com/). To enable Stripe and payments, one needs to have
a Stripe account with a single
[Product](https://stripe.com/docs/billing/prices-guide) (eg. "Mataroa Premium
Plan").

To configure, add the following variables from your Stripe account to your
`.envrc`:

```sh
export STRIPE_API_KEY="sk_test_XXX"
export STRIPE_PUBLIC_KEY="pk_test_XXX"
export STRIPE_PRICE_ID="price_XXX"
```

### Cronjobs

We don't use cron but systemd timers for jobs that need to run recurringly.

#### Process email notifications

```sh
python manage.py processnotifications
```

Sends notification emails for new blog posts.

Triggers daily at 10AM server time.

#### Email blog exports

```sh
python manage.py mailexports
```

Emails users their blog exports.

Triggers monthly, first day of the month, 6AM server time.

#### Daily summary

```sh
python manage.py mailsummary
```

Sends mataroa daily moderation summary.

Triggers daily at 00:15 server time.

### Database Backup

#### Shell Script

We use the script [`backup-database.sh`](./deploy/backup-database.sh) to dump the
database and upload it into an S3-compatible object storage cloud using
[rclone](https://rclone.org/).

#### Commands

To create a database dump run:

```sh
pg_dump -Fc --no-acl mataroa -h localhost -U mataroa -f /home/deploy/mataroa.dump -W
```

To restore a database dump run:

```sh
pg_restore --disable-triggers -j 4 -v -h localhost -cO --if-exists -d mataroa -U mataroa -W mataroa.dump
```

### Feature Release Playbook

This document provides a comprehensive checklist for releasing a new version of Mataroa.

1. Finish with all commits, push to main
1. Verify `CHANGELOG.md` is up-to-date
1. Verify CI is green
1. Bump in in `pyproject.toml` with `git commit -m "release v1.x"`
1. Run `uv lock`
1. Create Git tag: `git tag -a v1.x -m "v1.x"`
1. Push tag: `git push origin --tags` and `git push srht --tags`

### Server Migration

Sadly or not, nothing lasts forever. One day you might do a server migration.
Among many, mataroa is doing something naughty. We store everything, images
including, in the Postgres database. Naughty indeed, yet makes it much easier to
backup but also migrate.

To start with, one a migrator has setup their new server (see
[Deployment](#deployment)) we recommend testing everything in another
domain, other than the main (existing) one.

Once everything works:

1. Verify all production variables and canonical server names exist in settings et al.
1. Disconnect production server from public IP. This is not a zero-downtime migration — to be clear.
1. Run backup-database.sh one last time.
1. Assign elastic/floating IP to new server.
1. Run TLS certificate (naked and wildcard) generations.
1. `scp` database dump into new server.
1. Restore database dump in new server.
1. Start mataroa and caddy systemd services

Later:

1. Setup cronjobs / systemd timers
1. Setup healthcheks for recurring jobs.
1. Verify DEBUG is 0.

The above assume the migrator has a floating IP that they can move around. If
not, there are two problems. The migrator needs to coordinate DNS but much
more problematically all custom domains stop working :/ For this reason we
should implement CNAME custom domains. However, CNAME custom domains do not
support root domains, so what's the point anyway you ask. Good question. I don't
know. I only hope I never decide to switch away from Hetzner.

### On Server Outage

So, mataroa is down. What do we do?

First, panic. Run around in circles with your hands up in despair. It's important to
do this, don't think of it as a joke. Once that's done:

#### 1. Check Caddy

Caddy is the first point of contact inside the server from the outside world.

First ssh into server:

```sh
ssh root@mataroa.blog
```

Caddy runs as a systemd service. Check status with:

```sh
systemctl status caddy
```

Exit with `q`. If the service is not running and is errored restart with:

```sh
systemctl restart caddy
```

If restart does not work, check logs:

```sh
journalctl -u caddy -r
```

`-r` is for reverse. Use `-f` to follow logs real time:

```sh
journalctl -u caddy -f
```

To search within all logs do slash and then the keyword itself, eg: `/keyword-here`,
then hit enter.

The config for Caddy is:

```sh
cat /etc/caddy/Caddyfile
```

One entry is to serve anything with *.mataroa.blog host, and the second is for anything
not in that domain, which is exclusively all the blogs custom domains.

The systemd config for Caddy is:

```sh
cat /etc/systemd/system/multi-user.target.wants/caddy.service
```

#### 2. Check gunicorn

After caddy receives the request, it forwards it to gunicorn. Gunicorn is what runs the
mataroa Django instances, so it's named `mataroa`. It also runs as a systemd service.

To see status:

```sh
systemctl status mataroa
```

To restart:

```sh
systemctl restart mataroa
```

To see logs:

```sh
journalctl -u mataroa -r
```

and to follow them:

```sh
journalctl -u mataroa -f
```

The systemd config for mataroa/gunicorn is:

```sh
cat /etc/systemd/system/multi-user.target.wants/mataroa.service
```

Note that the env variables for production live inside the systemd service file.

#### 3. How to hotfix code

Here's where the code lives and how to access it:

```sh
sudo -i -u deploy
cd /var/www/mataroa/
source .envrc  # load env variables for manual runs
source .venv/bin/activate  # activate venv
python manage.py
```

If you make a change in the source code files (inside `/var/www/mataroa`) you need to
restart the service for the changes to take effect:

```sh
systemctl restart mataroa
```

## License

Copyright Mataroa Contributors

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, version 3.
