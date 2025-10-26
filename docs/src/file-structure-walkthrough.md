# File Structure Walkthrough

Here, an overview of the project's code sources is presented. The purpose is
for the reader to understand what kind of functionality is located where in
the sources.

All business logic of the application is in one Django app: [`main`](/main).

Condensed and commented sources file tree:

```
.
├── .build.yml # SourceHut CI build config
├── .envrc.example # example direnv file
├── .github/ # GitHub Actions config files
├── Caddyfile # configuration for Caddy webserver
├── Dockerfile
├── LICENSE
├── Makefile # make-defined tasks
├── README.md
├── backup-database.sh
├── docker-compose.yml
├── docs/
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

## [`main/urls.py`](/main/urls.py)

All urls are in this module. They are visually divided into several sections:

* general, includes index, dashboard, static pages
* user system, includes signup, settings, logout
* blog posts, the CRUD opertions of
* blog extras, includes rss and newsletter features
* comments, related to the blog post comments
* billing, subscription and card related
* blog import, export, webring
* images CRUD
* analytics list and details
* pages CRUD

## [`main/views/`](/main/views/)

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

### [`main/views/general.py`](/main/views/general.py)

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

### [`main/views/api.py`](/main/views/api.py)

This module contains all API related views. These views have their own
api key based authentication.

### [`main/views/export.py`](/main/views/export.py)

This module contains all views related to the export capabilities of mataroa.

The way the exports work is by reading the base files from the repository root:
[export_base_hugo](export_base_hugo/), [export_base_zola](export_base_zola/),
[export_base_epub](export_base_epub/) for Hugo, Zola, and epub respectively.
After reading, we replace some strings on the configurations, generate posts
as markdown strings, and zip-archive everything in-memory. Finally, we respond
using the appropriate content type (`application/zip` or `application/epub`) and
[Content-Disposition](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Disposition)
`attachment`.

### [`main/views/billing.py`](/main/views/billing.py)

This module contains all billing and subscription related views. It's designed to
support one payment processor, Stripe.

### [`main/views/moderation.py`](/main/views/moderation.py)

This module contains all views related to moderating the content of the platform.

## [`main/tests/`](/main/tests/)

All tests are under this directory. They are divided into several modules,
based on the functionality and the views they test.

Everything uses the built-in Python `unittest` module along with standard
Django testing facilities.

## [`main/models.py`](/main/models.py) and [`main/migrations/`](/main/migrations/)

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

## [`main/forms.py`](/main/forms.py)

Here a collection of Django-based forms resides, mostly in regards to user creation,
upload functionalities (for post import or image upload), and card details
submission.

See [Django Form fields reference](https://docs.djangoproject.com/en/3.2/ref/forms/fields/).

## [`main/text_processing.py`](/main/text_processing.py)

This module contains utilities for processing text content, including markdown
rendering, syntax highlighting, and text transformations used throughout the
application.

## [`main/templates/assets/style.css`](main/templates/assets/style.css)

On Mataroa, a user can enable an option, Theme Zia Lucia, and get a higher font
size by default. Because we need to change the body font-size value, we render
the CSS. It is not static. This is why it lives inside the templates directory.
