// A tiny static server for a built UI (vite build --outDir DIR) that proxies /api, /u and /demo to the API.
//   node e2e/static_proxy.mjs <dist dir> <port> <api origin>
// Used by e2e/industry_flows.mjs; it never touches the dev servers' ports.
// CADDY_HEADERS=<deploy/Caddyfile.template>: the built app is served with the same security headers as production
// (its Content-Security-Policy is read from that file), to check the app still works under that policy.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";

const [dist, port, api] = process.argv.slice(2);
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
  ".json": "application/json", ".ico": "image/x-icon", ".woff2": "font/woff2", ".webmanifest": "application/manifest+json" };
const target = new URL(api);
const caddy = process.env.CADDY_HEADERS ? fs.readFileSync(process.env.CADDY_HEADERS, "utf8") : "";
const csp = (caddy.match(/header Content-Security-Policy "([^"]+)"/) || [])[1];
if (caddy && !csp) throw new Error("no Content-Security-Policy in " + process.env.CADDY_HEADERS);
const SECURITY = csp ? { "content-security-policy": csp, "x-frame-options": "DENY", "x-content-type-options": "nosniff",
  "referrer-policy": "strict-origin-when-cross-origin" } : {};

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
  res.writeHead(200, { "content-type": TYPES[path.extname(file)] || "application/octet-stream", ...SECURITY });
  fs.createReadStream(file).pipe(res);
}).listen(Number(port), "127.0.0.1", () => console.log(`static ${dist} on ${port} -> ${api}`));
