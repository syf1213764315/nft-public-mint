import { keccak256 } from "ethers";
import {
  MintRequest,
  PrepareError,
  chainCatalog,
  prepareMint,
  serializePreview,
} from "./mint-prepare";
import { proxyJsonRpc } from "./rpc-proxy";
import { parseRpcEndpoints } from "./rpc-blast";

export interface ApiResult {
  status: number;
  body: unknown;
}

function rejectKeys(body: Record<string, unknown>): void {
  if (body.keys != null || body.privateKeys != null || body.walletKeys != null || body.key != null) {
    throw new PrepareError(
      "Private keys must never be sent to the server. The page derives addresses and signs in your browser."
    );
  }
}

export function asMintRequest(body: Record<string, unknown>): MintRequest {
  rejectKeys(body);
  const addresses = Array.isArray(body.addresses) ? body.addresses.map(String) : [];
  return {
    addresses,
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

export async function handleHealth(): Promise<ApiResult> {
  return {
    status: 200,
    body: {
      ok: true,
      public: true,
      signing: "browser",
      keysLeaveBrowser: false,
    },
  };
}

export async function handleChains(): Promise<ApiResult> {
  return { status: 200, body: { ok: true, chains: chainCatalog() } };
}

export async function handlePreview(body: Record<string, unknown>): Promise<ApiResult> {
  const prepared = await prepareMint(asMintRequest(body));
  return { status: 200, body: serializePreview(prepared) };
}

export async function handleRpc(body: Record<string, unknown>): Promise<ApiResult> {
  rejectKeys(body);
  const url = String(body.url || "");
  const payload =
    body.payload && typeof body.payload === "object"
      ? body.payload
      : {
          jsonrpc: "2.0",
          id: body.id ?? 1,
          method: String(body.method || ""),
          params: Array.isArray(body.params) ? body.params : [],
        };
  const method = String((payload as { method?: string }).method || "");
  if (!method || method.length > 64) {
    throw new PrepareError("JSON-RPC method is required");
  }
  const result = await proxyJsonRpc(url, payload);
  return { status: 200, body: { ok: true, result } };
}

export async function handleBlast(body: Record<string, unknown>): Promise<ApiResult> {
  rejectKeys(body);
  const rpcUrls = Array.isArray(body.rpcUrls) ? body.rpcUrls.map(String) : [];
  const rawTxs = Array.isArray(body.rawTxs) ? body.rawTxs.map(String) : [];
  if (rpcUrls.length === 0) throw new PrepareError("Need at least one RPC URL");
  if (rawTxs.length === 0) throw new PrepareError("Need at least one signed raw transaction");
  if (rawTxs.length > 25) throw new PrepareError("Too many transactions in one blast");
  if (rpcUrls.length > 12) throw new PrepareError("Too many RPC endpoints in one blast");

  const endpoints = parseRpcEndpoints(rpcUrls);
  const wallets = await Promise.all(
    rawTxs.map(async (raw, idx) => {
      const txHash = keccak256(raw.startsWith("0x") ? raw : `0x${raw}`);
      const results = await Promise.all(
        endpoints.map(async (ep) => {
          try {
            const json = (await proxyJsonRpc(ep.url, {
              jsonrpc: "2.0",
              method: "eth_sendRawTransaction",
              params: [raw],
              id: 1,
            })) as { result?: string; error?: { message?: string } };
            if (json.result) {
              return { label: ep.label, ok: true, message: json.result };
            }
            const errMsg = json.error?.message || "unknown RPC error";
            const already = /already known|already exists/i.test(errMsg);
            return { label: ep.label, ok: already, message: errMsg };
          } catch (err: unknown) {
            return {
              label: ep.label,
              ok: false,
              message: err instanceof Error ? err.message : String(err),
            };
          }
        })
      );
      const accepted = results.some((r) => r.ok);
      return { idx, txHash, accepted, results };
    })
  );

  return {
    status: 200,
    body: {
      ok: true,
      accepted: wallets.filter((w) => w.accepted).length,
      rejected: wallets.filter((w) => !w.accepted).length,
      wallets,
    },
  };
}

export async function dispatchApi(
  method: string,
  pathName: string,
  body: Record<string, unknown> | null
): Promise<ApiResult> {
  const path = pathName.split("?")[0];
  if (method === "GET" && (path === "/api/health" || path === "/health")) return handleHealth();
  if (method === "GET" && (path === "/api/chains" || path === "/chains")) return handleChains();
  if (method === "POST" && (path === "/api/preview" || path === "/preview")) {
    return handlePreview(body || {});
  }
  if (method === "POST" && (path === "/api/rpc" || path === "/rpc")) {
    return handleRpc(body || {});
  }
  if (method === "POST" && (path === "/api/blast" || path === "/blast")) {
    return handleBlast(body || {});
  }
  return { status: 404, body: { ok: false, error: "Not found" } };
}

export async function runApi(
  method: string,
  pathName: string,
  body: Record<string, unknown> | null
): Promise<ApiResult> {
  try {
    return await dispatchApi(method, pathName, body);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    const status = err instanceof PrepareError ? 400 : 500;
    return { status, body: { ok: false, error: message } };
  }
}

export function netlifyHandler(pathName: string) {
  return async (event: {
    httpMethod?: string;
    body?: string | null;
    isBase64Encoded?: boolean;
  }) => {
    const method = event.httpMethod || "GET";
    if (method === "OPTIONS") {
      return { statusCode: 204, body: "" };
    }
    let parsed: Record<string, unknown> | null = null;
    if (method === "POST") {
      const raw = event.body
        ? event.isBase64Encoded
          ? Buffer.from(event.body, "base64").toString("utf8")
          : event.body
        : "";
      if (!raw.trim()) {
        return jsonResponse(400, { ok: false, error: "Empty request body" });
      }
      try {
        parsed = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        return jsonResponse(400, { ok: false, error: "Request body is not valid JSON" });
      }
    }
    const result = await runApi(method, pathName, parsed);
    return jsonResponse(result.status, result.body);
  };
}

function jsonResponse(status: number, body: unknown) {
  return {
    statusCode: status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
    body: JSON.stringify(body),
  };
}
