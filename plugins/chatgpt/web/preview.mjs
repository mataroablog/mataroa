import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { libraryHTML, assetDirectory } from './template.mjs';
const harness = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Mataroa · Local fixture preview</title><style>html,body,iframe{margin:0;width:100%;height:100%;border:0}iframe{display:block}</style></head><body><iframe sandbox="allow-scripts" title="Mataroa library preview"></iframe><script type="module" src="/fixture-host.js"></script></body></html>`;
export async function createPreviewServer(port = 4173) {
  const html = libraryHTML();
  const assets = new Set(['model.js', 'library.js', 'bridge.js', 'main.js', 'library.css']);
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const origin = `http://127.0.0.1:${server.address().port}`;
    try {
      if (url.pathname === '/') { res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(harness); }
      else if (url.pathname === '/fixture-host.js') { res.writeHead(200, { 'Content-Type': 'text/javascript; charset=utf-8' }); res.end(await readFile(new URL('./fixture-host.js', import.meta.url))); }
      else if (url.pathname === '/library.html') {
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': `default-src 'none'; script-src ${origin}; style-src ${origin} 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'; img-src data:` });
        res.end(html.replaceAll('http://127.0.0.1/static/', `${origin}/static/`));
      } else if (url.pathname.startsWith('/static/mcp/') && assets.has(url.pathname.slice('/static/mcp/'.length))) {
        const name = url.pathname.slice('/static/mcp/'.length);
        res.writeHead(200, { 'Content-Type': name.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/javascript; charset=utf-8' });
        res.end(await readFile(new URL(name, assetDirectory)));
      } else { res.writeHead(404); res.end('Not found'); }
    } catch (error) { res.writeHead(500); res.end(error.message); }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
  return server;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT || 4173);
  await createPreviewServer(port);
  console.log(`Local fixture preview: http://127.0.0.1:${port}`);
  console.log('Fictional sample posts only. This does not connect to a Mataroa account.');
}
