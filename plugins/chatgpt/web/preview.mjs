import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = fileURLToPath(new URL('../', import.meta.url));
const harness = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Mataroa · Local fixture preview</title><style>html,body,iframe{margin:0;width:100%;height:100%;border:0}iframe{display:block}</style></head><body><iframe title="Mataroa library preview"></iframe><script type="module" src="/fixture-host.js"></script></body></html>`;
export async function createPreviewServer(port = 4173) {
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    try {
      if (url.pathname === '/') { res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(harness); }
      else if (url.pathname === '/fixture-host.js') { res.writeHead(200, { 'Content-Type': 'text/javascript; charset=utf-8' }); res.end(await readFile(path.join(root, 'web/fixture-host.js'))); }
      else if (url.pathname === '/library.html') {
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'; img-src data:" });
        res.end(await readFile(path.join(root, 'src/mataroa_chatgpt/static/library.html')));
      } else { res.writeHead(404); res.end('Not found'); }
    } catch { res.writeHead(500); res.end('Run npm run build before previewing.'); }
  });
  await new Promise(resolve => server.listen(port, '127.0.0.1', resolve));
  return server;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT || 4173);
  await createPreviewServer(port);
  console.log(`Local fixture preview: http://127.0.0.1:${port}`);
  console.log('Fictional sample posts only. This does not connect to a Mataroa account.');
}
