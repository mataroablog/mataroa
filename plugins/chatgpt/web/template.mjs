// Render the production Django template for local previews and tests.
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
export const repository = fileURLToPath(new URL('../../../', import.meta.url));
export const assetDirectory = new URL('../../../main/static/mcp/', import.meta.url);
export function libraryHTML() {
  return execFileSync(`${repository}.venv/bin/python`, ['-c', `
import django
from django.test import override_settings
django.setup()
from main.mcp.server import library_resource
with override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}, MATAROA_MCP_ISSUER_URL="http://127.0.0.1", STATIC_URL="/static/"):
    print(library_resource()[0])
`], { cwd: repository, env: { ...process.env, DJANGO_SETTINGS_MODULE: 'mataroa.settings_test' }, encoding: 'utf8' });
}
