import { PrepareError } from "./mint-prepare";

function isBlockedHost(host: string): boolean {
  const h = host.toLowerCase().replace(/^\[|\]$/g, "");
  if (h === "localhost" || h === "::1" || h === "0.0.0.0") return true;
  if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(h)) return false;
  const [a, b] = h.split(".").map(Number);
  if (a === 0 || a === 10 || a === 127) return true;
  if (a === 192 && b === 168) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 169 && b === 254) return true;
  return false;
}

export function assertSafeRpcUrl(raw: string): URL {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new PrepareError("RPC URL is not valid");
  }
  if (url.protocol !== "https:") {
    throw new PrepareError("RPC URL must use https");
  }
  if (isBlockedHost(url.hostname)) {
    throw new PrepareError("RPC host is not allowed");
  }
  return url;
}

export async function proxyJsonRpc(
  rpcUrl: string,
  payload: unknown,
  timeoutMs = 12_000
): Promise<unknown> {
  assertSafeRpcUrl(rpcUrl);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(rpcUrl, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const text = await res.text();
    try {
      return JSON.parse(text);
    } catch {
      throw new PrepareError(`RPC returned non-JSON (HTTP ${res.status})`);
    }
  } catch (err: unknown) {
    if (err instanceof PrepareError) throw err;
    const message = err instanceof Error ? err.message : String(err);
    throw new PrepareError(`RPC proxy failed: ${message}`);
  } finally {
    clearTimeout(timer);
  }
}
