# Changelog

All notable changes to this project will be documented in this file.

## [1.5.0](https://github.com/mataroablog/mataroa/compare/v1.4...v1.5)

### Important changes

* Add /p/ as an alternative blog post path option

## [1.4.0](https://github.com/mataroablog/mataroa/compare/v1.3...v1.4)

### Important changes

* Add submit post by email functionality
* Add support for LaTeX-style mathematical markup
* Add API endpoints for managing comments
* Add API endpoints for managing pages (create, read, update, delete, list)
* Replace ansible deployment with bash scripts
* Move all docs pages into README
* Add deploy GitHub Actions workflow
* Upgrade to Django 6.0 Release Candidate 1
* Merge subscribe note with footer note
* Add database bash scripts for local development
* Make admin email configurable from the environment
* Make database backup setup part of server provisioning process
* Make old backups deletion as part of the backup process

### Bugfixes

* Fix broken image paths for epub exports
* Fix existing username error from human diagnostics endpoint
* Fix Docker image build workflow for ghcr.io

## [1.3.0](https://github.com/mataroablog/mataroa/compare/v1.2...v1.3) - 2025-10-26

### Important changes

* Rebuild content moderation dashboard with:
    * pagination
    * filters
    * sort by
    * day summary
    * images overview
    * global stats
    * daily admin summary email
* Rewrite Stripe integration:
    * Upgrade to latest Stripe SDK and API version
    * Improve webhook handling for more cases
    * Handle Stripe-side changes more robustly
* Add FAQ section on landing page
* Switch to pyproject and astral uv
* Add hard check for image uploading limit
* Change sign up text
* Remove robot checks from sign up form
* Upgrade to Django 5.2
* Add docker image auto-push to ghcr.io
* Add AGENTS.md to help onboarding of AI-enhanced contributors

### Bugfixes

* Improve dark mode colours for better readability
* Fix ansible not auto-enabling systemd timers

## [1.2.0](https://github.com/mataroablog/mataroa/compare/v1.1...v1.2) - 2025-02-06

### Important changes

* Change project license from MIT to AGPL-3.0-only
* Enable customisation of subscribe note on footer
* Introduce ansible configuration for deployment
* Switch jobs from cron to systemd timers
* Replace uWSGI with Gunicorn
* Replace black/flake8/isort with ruff
* Refactor newsletter processing into more robust and simpler workflow
* Setup docs using mdbook
* Improve docker local development setup
* Add guide for custom domains
* Simplify Zola and Hugo base CSS styles
* Add themed error pages
* Upgrade to Django 5.1
* Limit RSS to last 10 posts

### Bugfixes

* Fix Zola v0.19 RSS feed configuration

## [1.1.0](https://github.com/mataroablog/mataroa/compare/v1.0...v1.1) - 2023-12-05

### Important changes

* Rewrite moderation dashboard
* Rewrite Stripe integration with latest APIs
* Create new signup workflow
* Lower image size limit to 1MB
* Upgrade to Django 5.0

## [1.0.0](https://github.com/mataroablog/mataroa/compare/5ff277da71fb653631ea38407cd6154e831be540...v1.0) - 2023-09-06

This is an initial numbered release after 3+ years of development.

* Core blogging functionalities
* Export functionalities
* Email newsletter
* Custom domains
* Backend-based analytics
* API
