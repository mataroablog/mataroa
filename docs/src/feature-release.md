# Feature Release Playbook

This document provides a comprehensive checklist for releasing a new version of Mataroa.

1. Version bump in in `pyproject.toml`
1. Update `CHANGELOG.md`
1. Push all latest changes and verify CI is green
1. Create Git tag: `git tag -a v1.x -m "v1.x"`
1. Push tag: `git push origin --tags` and `git push srht --tags`
