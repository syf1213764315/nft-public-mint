#!/usr/bin/env node

import http from "http";
import fs from "fs";
import path from "path";
import dotenv from "dotenv";
import { PrepareError } from "./mint-prepare";
import { runApi } from "./http-api";

dotenv.config({ path: path.resolve(process.cwd(), ".env") });

const HOST = process.env.WEB_HOST || "127.0.0.1";
const PORT = Number(process.env.WEB_PORT || 3847);
const WEB_ROOT = path.resolve(__dirname, "..", "web");
const MAX_BODY = 256 * 1024;

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".png": "image/png",
  ".woff2": "font/woff2",
};

function sendJson(res: http.ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Content-Length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_BODY) {
        reject(new PrepareError("Request body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function serveStatic(urlPath: string, res: http.ServerResponse): void {
  let rel = decodeURIComponent(urlPath.split("?")[0]);
  if (rel === "/") rel = "/index.html";
  const safe = path.normalize(rel).replace(/^(\.\.[/\\])+/, "");
  const file = path.join(WEB_ROOT, safe);
  if (!file.startsWith(WEB_ROOT)) {
    res.writeHead(403).end("Forbidden");
    return;
  }
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" }).end("Not found");
    return;
  }
  const ext = path.extname(file).toLowerCase();
  res.writeHead(200, {
    "Content-Type": MIME[ext] || "application/octet-stream",
    "Cache-Control": ext === ".html" ? "no-store" : "public, max-age=300",
  });
  fs.createReadStream(file).pipe(res);
}

const server = http.createServer(async (req, res) => {
  const url = req.url || "/";
  const method = req.method || "GET";

  try {
    if (url.startsWith("/api/")) {
      let parsed: Record<string, unknown> | null = null;
      if (method === "POST") {
        const raw = await readBody(req);
        if (!raw.trim()) throw new PrepareError("Empty request body");
        try {
          parsed = JSON.parse(raw) as Record<string, unknown>;
        } catch {
          throw new PrepareError("Request body is not valid JSON");
        }
      }
      const result = await runApi(method, url, parsed);
      sendJson(res, result.status, result.body);
      return;
    }

    if (method === "GET") {
      serveStatic(url, res);
      return;
    }

    sendJson(res, 404, { ok: false, error: "Not found" });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    const status = err instanceof PrepareError ? 400 : 500;
    if (!res.headersSent) sendJson(res, status, { ok: false, error: message });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`NFT public mint console → http://${HOST}:${PORT}`);
  console.log("Private keys are parsed and signed in the browser. They are never posted to this server.");
});
