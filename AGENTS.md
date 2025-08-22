# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

See https://agents.md/

## Essential Commands

### Development Server

```sh
# Start the Django development server
uv run python manage.py runserver

# Use Docker for development (includes database)
docker compose up
```

### Database Management

```sh
# Run migrations
uv run python manage.py migrate

# Load development data fixtures
uv run python manage.py loaddata dev-data

# Create superuser for admin access
uv run python manage.py createsuperuser
```

### Testing

```sh
# Run all tests
uv run python manage.py test

# Run tests with coverage
uv run coverage run --source='.' --omit '.venv/*' manage.py test
uv run coverage report -m

# Run specific test module
uv run python manage.py test main.tests.test_posts
```

### Code Quality

```sh
# Format code with ruff
uv run ruff format

# Lint code
uv run ruff check
uv run ruff check --fix
```

### Management Commands

```sh
# Process notification emails for new blog posts
uv run python manage.py processnotifications

# Email blog exports to users
uv run python manage.py mailexports

# Email daily summary
uv run python manage.py mailsummary

# Check Stripe integration
uv run python manage.py checkstripe
```

### Dependencies

```sh
# Install all dependencies including dev group
uv sync --all-groups

# Add new dependency
uv add <package>

# Update dependencies
uv lock -U
uv sync --all-groups
```

## Architecture

### Project Structure

Mataroa is a Django-based blogging platform with a single app architecture:

- **`mataroa/`**: Django project configuration (settings, URLs, WSGI/ASGI)
- **`main/`**: Single Django app containing all business logic
- **`docs/`**: Project documentation built with mdBook
- **`static/`**: Collected static files for production

### Core Application (`main/`)

All business logic resides in the `main` Django app:

- **`models.py`**: Database schema (User, Post, Page, Comment, etc.)
- **`views/`**: Business logic split across modules:
  - `general.py`: Index, dashboard, static pages
  - `api.py`: API endpoints with key-based authentication
  - `billing.py`: Stripe integration for subscriptions
  - `export.py`: Export functionality (Hugo, Zola, EPUB)
  - `moderation.py`: Admin moderation features
- **`templates/`**: HTML templates, including dynamic CSS rendering
- **`management/commands/`**: Custom Django management commands
- **`tests/`**: Test suite using Python's unittest and Django testing framework

### Key Features

- **Subdomain-based blogs**: Each user gets `username.mataroa.blog`
- **Custom domains**: Users can configure CNAMEs
- **Export capabilities**: Hugo, Zola, EPUB formats
- **Email notifications**: RSS-style email subscriptions
- **Image uploads**: Direct image hosting for blog posts
- **Comment system**: Moderated comments on posts
- **Analytics**: Simple page view tracking
- **Billing**: Optional Stripe integration for premium features

### Database Models

- **User**: Extended Django user with blog configuration
- **Post**: Blog posts with markdown content
- **Page**: Static pages (About, etc.)
- **Comment**: Post comments with moderation
- **Image**: Uploaded images with slugs
- **AnalyticPost/AnalyticPage**: Simple analytics tracking
- **NotificationRecord**: Email subscription management
- **ExportRecord**: Export history tracking

### Subdomain Routing

The `main.middleware.host_middleware` handles subdomain routing to serve user blogs on their respective subdomains while maintaining the main site on the root domain.

### Local Development Setup

1. Add entries to `/etc/hosts` for subdomain testing:
   ```
   127.0.0.1 mataroalocal.blog
   127.0.0.1 testuser.mataroalocal.blog
   ```
2. Use `.envrc.example` as template for environment variables
3. Access at `http://mataroalocal.blog:8000/`

### Testing Patterns

- Tests use Django's TestCase and built-in test client
- Fixtures available in `main/fixtures/dev-data.json`
- Test data includes user "admin" with password "admin"
- Tests cover views, models, forms, and management commands

### Code Style

- Use ruff for formatting and linting
- All files should end with newline
- Follow Django conventions for models, views, and templates
- API views use custom key-based authentication
