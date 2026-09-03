#!/usr/bin/env node

import http from "http";
import fs from "fs";
import path from "path";
import dotenv from "dotenv";
import { chainCatalog, parseWalletKeys, prepareMint, serializePreview, PrepareError, MintRequest } from "./mint-prepare";
import { localPublicSnipe } from "./local-mint";
import { MintEvent } from "./mint-events";

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

function isLocalHost(hostHeader: string | undefined): boolean {
  const host = (hostHeader || "").split(":")[0].toLowerCase();
  return host === "127.0.0.1" || host === "localhost" || host === "[::1]";
}

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
        reject(new Error("Request body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

async function parseJsonBody<T>(req: http.IncomingMessage): Promise<T> {
  const raw = await readBody(req);
  if (!raw.trim()) throw new PrepareError("Empty request body");
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new PrepareError("Request body is not valid JSON");
  }
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

function asMintRequest(body: Partial<MintRequest>): MintRequest {
  return {
    keys: Array.isArray(body.keys) ? body.keys.map(String) : [],
    chainKey: String(body.chainKey || "base"),
    quantity: Number(body.quantity || 1),
    nftLink: String(body.nftLink || ""),
    rpc: body.rpc == null ? "" : String(body.rpc),
    maxFeeGwei: body.maxFeeGwei == null ? undefined : Number(body.maxFeeGwei),
    priorityGwei: body.priorityGwei == null ? undefined : Number(body.priorityGwei),
    gasLimit: body.gasLimit == null ? undefined : Number(body.gasLimit),
    timing: body.timing === "now" || body.timing === "custom" ? body.timing : "wait",
    customTime: body.customTime == null ? undefined : String(body.customTime),
    continueUnverifiedRpc: Boolean(body.continueUnverifiedRpc),
  };
}

const server = http.createServer(async (req, res) => {
  if (!isLocalHost(req.headers.host)) {
    sendJson(res, 403, { ok: false, error: "This console only accepts connections from localhost." });
    return;
  }

  const url = req.url || "/";
  const method = req.method || "GET";

  try {
    if (method === "GET" && (url === "/api/health" || url.startsWith("/api/health?"))) {
      sendJson(res, 200, { ok: true, bind: `${HOST}:${PORT}`, localOnly: true });
      return;
    }

    if (method === "GET" && (url === "/api/chains" || url.startsWith("/api/chains?"))) {
      sendJson(res, 200, { ok: true, chains: chainCatalog() });
      return;
    }

    if (method === "POST" && url === "/api/wallets") {
      const body = await parseJsonBody<{ keys?: string[] }>(req);
      const parsed = parseWalletKeys(body.keys || []);
      sendJson(res, 200, {
        ok: true,
        wallets: parsed.addresses.map((address, index) => ({ index, address })),
      });
      return;
    }

    if (method === "POST" && url === "/api/preview") {
      const body = await parseJsonBody<Partial<MintRequest>>(req);
      const prepared = await prepareMint(asMintRequest(body));
      sendJson(res, 200, serializePreview(prepared));
      return;
    }

    if (method === "POST" && url === "/api/mint") {
      req.setTimeout(0);
      res.setTimeout(0);
      const body = await parseJsonBody<Partial<MintRequest>>(req);
      const prepared = await prepareMint(asMintRequest(body));
      if (!prepared.canFire) {
        sendJson(res, 400, { ok: false, error: prepared.blockReason || "Mint is blocked." });
        return;
      }

      res.writeHead(200, {
        "Content-Type": "application/x-ndjson; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
      });

      const abort = new AbortController();
      req.on("close", () => {
        if (!res.writableEnded) abort.abort();
      });

      const sendEvent = (event: MintEvent) => {
        if (res.writableEnded) return;
        res.write(`${JSON.stringify(event)}\n`);
      };

      try {
        await localPublicSnipe({
          nftContract: prepared.nftContract,
          quantity: prepared.quantity,
          walletKeys: prepared.walletKeys,
          rpcUrls: prepared.rpcUrls,
          maxFeePerGas: prepared.maxFeePerGas,
          maxPriorityFee: prepared.maxPriorityFee,
          gasLimit: prepared.gasLimit,
          targetStart: prepared.targetStart,
          plan: prepared.mintPlan,
          onEvent: sendEvent,
          signal: abort.signal,
        });
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : String(err);
        sendEvent({ type: "error", message });
      }
      if (!res.writableEnded) res.end();
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
    if (!res.headersSent) {
      sendJson(res, status, { ok: false, error: message });
    } else {
      res.end(`${JSON.stringify({ type: "error", message })}\n`);
    }
  }
});

server.listen(PORT, HOST, () => {
  console.log(`NFT public mint console → http://${HOST}:${PORT}`);
  console.log("Bound to localhost only. Private keys stay in RAM for this process and are never written to disk.");
});
