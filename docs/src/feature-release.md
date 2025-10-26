# Feature Release Playbook

This document provides a comprehensive checklist for releasing a new version of Mataroa.

1. Finish with all commits, push to main
1. Verify `CHANGELOG.md` is up-to-date
1. Verify CI is green
1. Bump in in `pyproject.toml` with `git commit -m "release v1.x"`
1. Run `uv sync`
1. Create Git tag: `git tag -a v1.x -m "v1.x"`
1. Push tag: `git push origin --tags` and `git push srht --tags`
