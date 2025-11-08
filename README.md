# mataroa

Naked blogging platform.

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

Open a PR on [GitHub](https://github.com/mataroablog/mataroa).

Send an email patch to
[~sirodoht/public-inbox@lists.sr.ht](mailto:~sirodoht/public-inbox@lists.sr.ht).
See how to contribute using email patches here:
[git-send-email.io](https://git-send-email.io/).

Read our docs at [docs.mataroa.blog](https://docs.mataroa.blog/)

## Development

This is a [Django](https://www.djangoproject.com/) codebase. Check out the
[Django docs](https://docs.djangoproject.com/) for general technical
documentation.

### Structure

The Django project is [`mataroa`](mataroa). There is one Django app,
[`main`](main), with all business logic. Application CLI commands are generally
divided into two categories, those under `python manage.py` and those under
`make`.

### Set up subdomains

Because mataroa works primarily with subdomain, one cannot access the basic web app
using the standard `http://127.0.0.1:8000` or `http://localhost:8000` URLs. What we do
for local development is adding a few custom entries on our `/etc/hosts` system file.

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

```
docker compose up
```

If you have also configured hosts as described above in the "Set up subdomains"
section, mataroa should now be locally accessible at
[http://mataroalocal.blog:8000/](http://mataroalocal.blog:8000/)

Note: The database data are saved in the git-ignored `docker-postgres-data` docker
volume, located in the root of the project.

### Dependencies

We use `uv` for dependency management and virtual environments.

```
uv sync --all-groups
```

### Environment variables

A file named `.envrc` is used to define the environment variables required for
this project to function. One can either export it directly or use
[direnv](https://github.com/direnv/direnv). There is an example environment
file one can copy as base:

```sh
cp .envrc.example .envrc
```

`.envrc` should contain the following variables:

```sh
# .envrc

export DEBUG=1
export SECRET_KEY=some-secret-key
export DATABASE_URL=postgres://mataroa:db-password@db:5432/mataroa
export EMAIL_HOST_USER=smtp-user
export EMAIL_HOST_PASSWORD=smtp-password
```

When on production, also include/update the following variables (see
[Deployment](#Deployment) and [Backup](#Backup)):

```sh
# .envrc

export DEBUG=0
export PGPASSWORD=db-password
```

When on Docker, to change or populate environment variables, edit the `environment`
key of the `web` service either directly on `docker-compose.yml` or by overriding it
using the standard named git-ignored `docker-compose.override.yml`.

```sh
# docker-compose.override.yml

version: "3.8"

services:
  web:
    environment:
      EMAIL_HOST_USER=smtp-user
      EMAIL_HOST_PASSWORD=smtp-password
```

Finally, stop and start `docker compose up` again. It should pick up the override file
as it has the default name `docker-compose.override.yml`.

### Database

This project is using one PostreSQL database for persistence.

One can use the `make pginit` command to initialise a database in the
`postgres-data/` directory.

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

If you have also configured hosts as described above in the "Set up subdomains"
section, mataroa should now be locally accessible at
[http://mataroalocal.blog:8000/](http://mataroalocal.blog:8000/)

## Testing

Using the Django test runner:

```sh
uv run python manage.py test
```

For coverage, run:

```sh
uv run coverage run --source='.' --omit '.venv/*' manage.py test
uv run coverage report -m
```

## Code linting & formatting

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

## Python dependencies

We use `uv` to manage dependencies declared in `pyproject.toml` (see `[project]` and `[dependency-groups]`).

Common commands:

```sh
# Add or remove dependencies
uv add <package>
uv remove <package>

# Update locked versions and install
uv lock -U
uv sync --all-groups
```

## Deployment

See the [Deployment](./docs/src/deployment.md) document on how to deploy a
mataroa instance. Briefly, the steps are:

```sh
cd deploy/
cp .envrc.example .envrc

# edit .envrc with your settings
vim .envrc

source .envrc
./provision.sh
```

After initial provisioning, deploy updates with `./deploy.sh`.

### Useful Commands

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

## Backup

See [Database Backup](docs/database-backup.md) for details. In summary:

To create a database dump:

```sh
pg_dump -Fc --no-acl mataroa -h localhost -U mataroa -f /home/deploy/mataroa.dump -w
```

To restore a database dump:

```sh
pg_restore -v -h localhost -cO --if-exists -d mataroa -U mataroa -W mataroa.dump
```

## Management

In addition to the standard Django management commands, there are also:

* `processnotifications`: sends notification emails for new blog posts of existing records.
* `mailexports`: emails users of their blog exports.

They are triggered using the standard `manage.py` Django way; eg:

```sh
python manage.py processnotifications
```

## Billing

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

## License

Copyright Mataroa Contributors

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, version 3.
