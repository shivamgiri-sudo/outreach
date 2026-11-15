// Rig only: serves web/dist and proxies /api/* to the local Outreach (what Caddy does in production).
// Env: UI_PORT, API_PORT, DIST
const http = require("http"), fs = require("fs"), path = require("path");
const { UI_PORT, API_PORT, DIST } = process.env;
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml", ".json": "application/json", ".woff2": "font/woff2" };
http.createServer((req, res) => {
  if (req.url.startsWith("/api/") || req.url.startsWith("/u/")) {
    const up = http.request({ host: "127.0.0.1", port: API_PORT, path: req.url, method: req.method, headers: req.headers }, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
    up.on("error", () => { res.writeHead(502); res.end("API down"); });
    return req.pipe(up);
  }
  let file = path.join(DIST, decodeURIComponent(req.url.split("?")[0]));
  if (!file.startsWith(DIST) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(DIST, "index.html");
  res.writeHead(200, { "Content-Type": types[path.extname(file)] || "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
}).listen(UI_PORT, "127.0.0.1");
