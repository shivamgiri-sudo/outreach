// A tiny static server for a built UI (vite build --outDir DIR) that proxies /api, /u and /demo to the API.
//   node e2e/static_proxy.mjs <dist dir> <port> <api origin>
// Used by e2e/industry_flows.mjs; it never touches the dev servers' ports.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";

const [dist, port, api] = process.argv.slice(2);
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
  ".json": "application/json", ".ico": "image/x-icon", ".woff2": "font/woff2", ".webmanifest": "application/manifest+json" };
const target = new URL(api);

http.createServer((req, res) => {
  if (/^\/(api|u|demo)(\/|$)/.test(req.url)) {
    const up = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method,
      headers: { ...req.headers, host: target.host } }, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
    up.on("error", (e) => { res.writeHead(502); res.end(String(e)); });
    req.pipe(up);
    return;
  }
  const clean = path.normalize(decodeURIComponent(req.url.split("?")[0])).replace(/^(\.\.[/\\])+/, "");
  let file = path.join(dist, clean);
  if (!file.startsWith(path.resolve(dist)) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(dist, "index.html");
  res.writeHead(200, { "content-type": TYPES[path.extname(file)] || "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
}).listen(Number(port), "127.0.0.1", () => console.log(`static ${dist} on ${port} -> ${api}`));
