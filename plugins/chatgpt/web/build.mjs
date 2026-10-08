import { build } from 'esbuild';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = fileURLToPath(new URL('../', import.meta.url));
const result = await build({
  absWorkingDir: root, entryPoints: ['web/main.ts'], outfile: 'library.js',
  bundle: true, write: false, minify: true, format: 'esm', target: 'es2022',
  legalComments: 'inline', sourcemap: false,
});
const script = result.outputFiles.find(file => file.path.endsWith('.js')).text;
const style = result.outputFiles.find(file => file.path.endsWith('.css')).text;
const template = await readFile(path.join(root, 'web/index.html'), 'utf8');
const html = template
  .replace('<!-- APP_STYLES -->', () => `<style>${style.replaceAll('</style', '<\\/style')}</style>`)
  .replace('<!-- APP_SCRIPT -->', () => `<script type="module">${script.replaceAll('</script', '<\\/script')}</script>`);
const destination = path.join(root, '../../main/mcp/library.html');
await mkdir(path.dirname(destination), { recursive: true });
// Normalize blank lines emitted by bundled dependency styles.
await writeFile(destination, html.replace(/^[ \t]+$/gm, ''));
console.log(`Built self-contained library.html (${Math.round(Buffer.byteLength(html) / 1024)} KiB)`);
