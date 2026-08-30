#!/usr/bin/env python3
"""
safe_mint.py — Security-first RH Chain mint helper
====================================================
Ported from gowthamaran/Robinhood-nft-sniper (MIT) design principles, adapted
for the nft-public-mint skill. Single-wallet, safety-over-speed.

Implements the strengths of the repo:
  1. Encrypted Web3 keystore (mode 600) — never plaintext pk
  2. Chain ID validation before every action
  3. Mandatory eth_call simulation before signing
  4. eth_estimateGas + 15% buffer before broadcast
  5. Hard spending limits (price/NFT, fee, total) + balance buffer
  6. Duplicate guard (SQLite state — no accidental re-mint)
  7. Watch-then-fire: warm poll until mint becomes mintable, then execute
  8. On-chain free-mint verification (getPublicDrop().price == 0) — closes the
     "header said token=0 but tx was PAID" trap
  9. OpenSea Drops API tx build + chain/value validation (optional os-mode)
 10. Broadcast same signed raw tx to up to 2 RPCs

Requires: eth-account, requests (both present in this environment).
No web3 dependency.

EXAMPLES
--------
# 1. Create encrypted keystore from an existing private key
python3 safe_mint.py keystore create --key 0x<PRIVATE_KEY> --password 'STRONG12chars' \
    --out ~/.nft-keystore/seadrop.json

# 2. Pre-flight check: chain, contract code, free-mint price, balance vs limits
python3 safe_mint.py check \
    --rpc https://rpc.mainnet.chain.robinhood.com \
    --contract 0x<COLLECTION> \
    --keystore ~/.nft-keystore/seadrop.json --password 'STRONG12chars'

# 3. Watch-then-fire (CONFIRM mode — asks before broadcast)
python3 safe_mint.py watch \
    --rpc https://rpc.mainnet.chain.robinhood.com \
    --backup https://<backup-rpc> \
    --contract 0x<COLLECTION> --quantity 1 \
    --keystore ~/.nft-keystore/seadrop.json --password 'STRONG12chars' \
    --max-price 0 --max-fee 0.0005 --max-total 0.001 --mode confirm

# 4. OpenSea drop free-check (no broadcast, just verify value == 0 + chain)
python3 safe_mint.py os-check --url https://opensea.io/drops/<slug> --wallet 0x<WALLET>

NOTE: mint price is operator-supplied via --value (like the repo: the bot never
infers price). For pure-free SeaDrop, leave --value 0 and it will still enforce
max-price 0. NEVER broadcast on mainnet without a successful dry-run first.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests

try:
    # eth_hash.auto picks the fastest keccak implementation (pycryptodome first).
    from eth_hash.auto import keccak as _keccak_bytes
except Exception:  # pragma: no cover - fallback for exotic environments
    from eth_utils import keccak as _keccak_bytes  # type: ignore


def keccak(data: bytes) -> bytes:
    """Ethereum Keccak-256 (NOT NIST SHA3-256 — hashlib.sha3_256 is WRONG for EVM).
    Returns 32 raw bytes. Critical for selectors, EIP-55 checksums and tx hashes."""
    return _keccak_bytes(data)

# --------------------------------------------------------------------------
# Constants (multi-chain EVM)
# --------------------------------------------------------------------------
MAINNET_CHAIN_ID = 4663
TESTNET_CHAIN_ID = 46630
# C3: allow only known EVM chains this tool actually supports (SeaDrop singleton
# present + RPC semantics validated). Anything else — Solana, non-EVM L1s,
# unknown testnets — is hard-rejected before any eth_call/signing work.
ALLOWED_CHAIN_IDS = {
    1,       # Ethereum
    8453,    # Base
    137,     # Polygon
    42161,   # Arbitrum One
    10,      # Optimism
    56,      # BNB Smart Chain
    43114,   # Avalanche C-Chain
    4663,    # Robinhood Chain (mainnet)
    46630,   # Robinhood Chain (testnet)
}

# OpenSea Drops API
OPENSEA_API_ROOT = "https://api.opensea.io/api/v2"

# Minimal SeaDrop getPublicDrop ABI fragment (view, returns tuple)
SEADROP_GET_PUBLIC_DROP_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "nftContract_", "type": "address"}],
        "name": "getPublicDrop",
        "outputs": [
            {
                "components": [
                    {"internalType": "uint80", "name": "mintPrice", "type": "uint80"},
                    {"internalType": "uint48", "name": "startTime", "type": "uint48"},
                    {"internalType": "uint48", "name": "endTime", "type": "uint48"},
                    {"internalType": "uint16", "name": "maxTotalMintableByWallet", "type": "uint16"},
                    {"internalType": "uint16", "name": "feeBps", "type": "uint16"},
                    {"internalType": "bool", "name": "restrictFeeRecipients", "type": "bool"},
                ],
                "internalType": "struct PublicDrop",
                "name": "publicDrop_",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    }
]
SEADROP_PUBLIC_DROP_SELECTOR = "0x6d5d595a"  # keccak("getPublicDrop(address)")[0:4]
TOTAL_SUPPLY_SELECTOR = "0x18160ddd"  # totalSupply()
MAX_SUPPLY_SELECTOR = "0xd5abeb01"  # maxSupply()

RPC_TIMEOUT = 8.0
MAX_RESPONSE_BYTES = 2_000_000
RATE_LIMIT_RETRIES = 6  # handles NodeFlare-style 1-req/10s limits
RATE_LIMIT_BACKOFF = 11.0  # seconds between retries on 429

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class SafeMintError(RuntimeError):
    pass


class LimitViolation(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Redaction (repo strength #10)
# --------------------------------------------------------------------------
_URL_PATTERNS = re.compile(r"(https?://)[^/@\s]+(:[^/@\s]+)?(@)([^/\s]+)", re.I)


def redact(text: str) -> str:
    if not text:
        return text
    text = _URL_PATTERNS.sub(r"\1[REDACTED]\3\4", text)
    text = re.sub(r"(sk-[A-Za-z0-9]{4})[A-Za-z0-9_-]+", r"\1...", text)
    text = re.sub(r"(0x[a-fA-F0-9]{4})[a-fA-F0-9]{36}(?![a-fA-F0-9])", r"\1...", text)
    return text


# --------------------------------------------------------------------------
# RPC client (keep-alive session)
# --------------------------------------------------------------------------
class RPCClient:
    def __init__(self, url: str, timeout: float = RPC_TIMEOUT) -> None:
        self.url = url
        self.session = requests.Session()
        self.timeout = timeout
        self._id = 0

    def call(self, method: str, params: list[Any] | None = None, max_retries: int | None = None) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or []}
        retries = RATE_LIMIT_RETRIES if max_retries is None else max_retries
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                resp = self.session.post(
                    self.url, json=payload, timeout=self.timeout, headers={"Content-Type": "application/json"}
                )
                if resp.status_code == 429:
                    # Rate-limited (e.g. NodeFlare 1 req/10s). Back off and retry.
                    last_exc = SafeMintError(
                        f"RPC 429 rate-limited persistently on {self.url} (tried {attempt+1}x)"
                    )
                    time.sleep(RATE_LIMIT_BACKOFF)
                    continue
                if resp.status_code >= 400:
                    # Deterministic reject (e.g. DRPC: method not supported) — do NOT retry.
                    raise SafeMintError(
                        f"RPC HTTP {resp.status_code} on {self.url}: {resp.text[:160]}"
                    )
                resp.raise_for_status()
                body = resp.json()
                last_exc = None  # success — do not resend
                break
            except SafeMintError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(min(2 * (attempt + 1), 8))
                    continue
                break
        if last_exc is not None:
            if isinstance(last_exc, SafeMintError):
                raise last_exc
            detail = str(last_exc)[:200]
            if isinstance(last_exc, requests.HTTPError) and last_exc.response is not None:
                detail = f"HTTP {last_exc.response.status_code} {last_exc.response.reason}"
            raise SafeMintError(f"RPC transport error ({type(last_exc).__name__}): {self.url} [{detail}]") from last_exc
        if "error" in body:
            err = body["error"]
            raise SafeMintError(f"RPC error {err.get('code')}: {redact(str(err.get('message', '')))}")
        if "result" not in body:
            raise SafeMintError("Malformed JSON-RPC response")
        return body["result"]

    def close(self) -> None:
        self.session.close()


def validate_chain(client: RPCClient, expected: int) -> int:
    if expected not in ALLOWED_CHAIN_IDS:
        raise SafeMintError(f"Unsupported chain ID: {expected}")
    chain_hex = client.call("eth_chainId")
    actual = int(chain_hex, 16)
    if actual != expected:
        raise SafeMintError(f"Wrong chain: RPC returned {actual}, expected {expected}. Refusing to proceed.")
    return actual


def get_code(client: RPCClient, address: str) -> str:
    return str(client.call("eth_getCode", [address, "latest"]))


# --------------------------------------------------------------------------
# RPCPool — multi-endpoint health scoring + failover (port dari repo, strength #1)
# --------------------------------------------------------------------------
DEFAULT_RH_RPCS = [
    "https://rpc.nodeflare.app/robinhood/public",   # full methods, 1 req/10s
    "https://robinhood.drpc.org",                    # limited methods, no eth_blockNumber
]


@dataclass
class EndpointHealth:
    url: str
    latency_ms: float = float("inf")
    block_number: int = 0
    successes: int = 0
    failures: int = 0
    last_error: str | None = None
    validated_chain: bool = False
    updated_at: float = 0.0
    unsupported_methods: set[str] = field(default_factory=set)

    def supports(self, method: str) -> bool:
        return method not in self.unsupported_methods

    def mark_unsupported(self, method: str) -> None:
        self.unsupported_methods.add(method)

    @property
    def reliability(self) -> float:
        total = self.successes + self.failures
        return self.successes / total if total else 0.0

    def score(self, freshest_block: int) -> float:
        """Lower = better. latency + block-lag penalty + reliability penalty."""
        if self.successes == 0:
            return float("inf")
        lag_penalty = max(0, freshest_block - self.block_number) * 1_000
        failure_penalty = (1 - self.reliability) * 2_000
        return self.latency_ms + lag_penalty + failure_penalty

    def as_table(self) -> dict[str, object]:
        return {
            "url": self.url,
            "latency_ms": round(self.latency_ms, 1),
            "block": self.block_number,
            "ok": self.successes,
            "fail": self.failures,
            "reliability": round(self.reliability, 2),
            "chain_ok": self.validated_chain,
            "error": self.last_error,
        }


class RPCPool:
    """Health-ranked pool. call() tries the best endpoint first, fails over on error.
    Ranking is probed once (lazily) then cached; a failing endpoint is demoted live
    so the hot loop (watch polling) never re-probes every endpoint."""

    def __init__(self, urls: list[str], expected_chain_id: int, timeout: float = RPC_TIMEOUT) -> None:
        unique = list(dict.fromkeys(url.strip() for url in urls if url.strip()))
        if not unique:
            raise SafeMintError("At least one RPC URL is required")
        self.urls = unique
        self.expected_chain_id = expected_chain_id
        self.timeout = timeout
        self.clients = {url: RPCClient(url, timeout) for url in unique}
        self.health = {url: EndpointHealth(url) for url in unique}
        self._ranked_cache: list[EndpointHealth] | None = None

    def probe(self, url: str, validate: bool = True) -> EndpointHealth:
        """Measure latency + block height + chain id. eth_blockNumber is best-effort (DRPC-limited)."""
        health = self.health[url]
        client = self.clients[url]
        started = time.perf_counter()
        chain_ok = False
        try:
            chain_hex = client.call("eth_chainId")
            chain_id = int(chain_hex, 16)
            if validate:
                if chain_id != self.expected_chain_id:
                    raise SafeMintError(f"chain {chain_id}, expected {self.expected_chain_id}")
                health.validated_chain = True
                chain_ok = True
            # blockNumber is best-effort — DRPC rejects it (HTTP 400)
            try:
                block_hex = client.call("eth_blockNumber")
                health.block_number = int(block_hex, 16)
            except Exception:
                health.block_number = 0  # limited endpoint, no freshness metric
            elapsed = (time.perf_counter() - started) * 1_000
            health.latency_ms = elapsed if health.successes == 0 else health.latency_ms * 0.7 + elapsed * 0.3
            health.successes += 1
            health.last_error = None
        except Exception as exc:  # noqa: BLE001
            health.failures += 1
            health.last_error = redact(str(exc))
            if not chain_ok:
                health.validated_chain = False
        health.updated_at = time.time()
        return health

    def validate(self) -> list[EndpointHealth]:
        """Probe all endpoints once (lazy), return healthy ones sorted best-first."""
        if self._ranked_cache is None:
            for url in self.urls:
                self.probe(url, validate=True)
            freshest = max((h.block_number for h in self.health.values()), default=0)
            self._ranked_cache = sorted(
                [
                    h for h in self.health.values()
                    if h.successes > 0 and h.last_error is None and h.validated_chain
                ],
                key=lambda h: h.score(freshest),
            )
        return self._ranked_cache

    def ranked_urls(self) -> list[str]:
        """Ordered list of validated endpoints, best first."""
        return [h.url for h in self.validate()]

    def invalidate(self) -> None:
        """Drop the cached ranking so the next validate() re-probes every endpoint.
        Use before fire-time reads (nonce/gas/balance) so they come from a freshly
        probed, single endpoint instead of a possibly-stale cache."""
        self._ranked_cache = None

    def best_client(self) -> RPCClient:
        """Client of the top-ranked healthy endpoint. Used to PIN fire-time reads
        (nonce/gas/balance/chainId) to ONE endpoint so they share the same mempool
        view — avoids cross-endpoint nonce/gas inconsistency."""
        ranked = self.validate()
        if not ranked:
            raise SafeMintError("No healthy RPC endpoint available (all probes failed)")
        return self.clients[ranked[0].url]

    def call(self, method: str, params: list[Any] | None = None, allow_fallback: bool = True, max_retries: int | None = None) -> Any:
        """Try best endpoint first; fail over through the ranked list on error.
        Failover triggers only on TRANSPORT errors (connectivity, HTTP 5xx, 429)
        and method-not-found (-32601). Contract-level errors (revert, invalid params)
        propagate immediately — they are valid blockchain responses, not RPC failures.

        Uses the cached ranking — a dead endpoint is demoted live, and a method that
        an endpoint rejects as unsupported (-32601) is negative-cached so later calls
        skip it. Hot-loop friendly (never re-probes on every call).

        max_retries: pass through to the client so hot loops can use max_retries=1
        (fail over fast on 429 instead of blocking RATE_LIMIT_RETRIES*BACKOFF = 66s)."""
        ranked = [h for h in self.validate() if h.supports(method)]
        if not ranked:
            healthy = self.validate()
            if not healthy:
                raise SafeMintError("No healthy RPC endpoint available (all probes failed)")
            raise SafeMintError(f"No RPC endpoint supports {method}")
        errors: list[str] = []
        # Snapshot iteration: each endpoint is tried at most once. Demotion happens
        # on the cached ranking (for future calls), never mutates the list we
        # are iterating — the old enumerate+del bug re-tried the demoted endpoint.
        for health in list(ranked):
            try:
                return self.clients[health.url].call(method, params, max_retries=max_retries)
            except SafeMintError as exc:
                msg = str(exc)
                # 429 rate-limit is a TRANSPORT condition (endpoint temporarily
                # saturated) — fail over, never treat it as a contract-level reply.
                is_transport = msg.startswith("RPC transport error") or msg.startswith("RPC 429")
                is_method_not_found = "-32601" in msg or "does not exist" in msg
                if is_method_not_found:
                    self.health[health.url].mark_unsupported(method)
                if not is_transport and not is_method_not_found:
                    # Contract-level error (revert, invalid params, etc.) —
                    # propagate immediately, don't failover.
                    raise
                self.health[health.url].failures += 1
                self.health[health.url].last_error = msg
                errors.append(f"{health.url}: {msg}")
                if not allow_fallback:
                    raise
                # live demotion on the CACHED ranking only — working endpoints
                # get preferred on the next call without re-probing.
                if self._ranked_cache is not None and health in self._ranked_cache:
                    self._ranked_cache.remove(health)
                    self._ranked_cache.append(health)
        raise SafeMintError(f"All RPC endpoints failed for {method}: {'; '.join(errors)}")

    def broadcast_same_raw(self, raw_hex: str) -> str:
        """Propagate one signed tx to up to 2 endpoints that support sendRawTransaction
        (repo strength #10). Limited endpoints (e.g. DRPC) still work for broadcast."""
        ranked = [h for h in self.validate() if h.supports("eth_sendRawTransaction")][:2]
        if not ranked:
            raise SafeMintError("No healthy RPC endpoint to broadcast through")
        errors: list[str] = []
        for health in ranked:
            try:
                h = self.clients[health.url].call(
                    "eth_sendRawTransaction", [raw_hex], max_retries=1
                )
                return str(h)
            except SafeMintError as exc:
                msg = str(exc).lower()
                errors.append(str(exc))
                if "nonce too low" in msg:
                    # FATAL: the signed raw tx has a stale nonce (already consumed by
                    # another tx). The deterministic hash we could compute is NOT the
                    # tx that actually hit the chain — treat as a real sign error and
                    # never report a fake success.
                    raise SafeMintError(
                        f"Nonce too low on {health.url}: signed tx nonce is stale. "
                        "Re-sign with a fresh nonce before broadcasting."
                    ) from exc
                if "already known" in msg or "known transaction" in msg:
                    # Tx already in mempool (possibly via the other endpoint) —
                    # the hash is deterministic from the signed raw bytes.
                    return "0x" + keccak(bytes.fromhex(raw_hex.removeprefix("0x"))).hex()
        raise SafeMintError(f"Broadcast failed on all endpoints: {'; '.join(errors)}")

    def close(self) -> None:
        for client in self.clients.values():
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    def report(self) -> list[dict[str, object]]:
        return [h.as_table() for h in sorted(self.health.values(), key=lambda h: h.url)]



# --------------------------------------------------------------------------
# ABI encoding (minimal, no web3)
# --------------------------------------------------------------------------
def encode_uint(selector: str, *args: int) -> str:
    """encode(selector, uint args...) -> calldata with selector + 32-byte words."""
    words = []
    for arg in args:
        words.append(f"{arg:064x}")
    return selector + "".join(words)


def encode_address(selector: str, address: str) -> str:
    addr = address.lower().removeprefix("0x")
    return selector + "0" * 24 + addr


def decode_uint256(data: str, offset_words: int = 0) -> int:
    """Decode one 32-byte word from hex result at word offset."""
    hex_body = data.removeprefix("0x")
    if len(hex_body) < (offset_words + 1) * 64:
        raise SafeMintError("RPC result too short for ABI decode")
    word = hex_body[offset_words * 64 : (offset_words + 1) * 64]
    return int(word, 16)


def checksum_address(address: str) -> str:
    """Minimal EIP-55 checksum. eth_account not required for this path."""
    addr = address.lower().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{40}", addr):
        raise SafeMintError(f"Invalid address: {address}")
    digest = keccak(addr.encode("ascii")).hex()
    return "0x" + "".join(
        c.upper() if int(digest[i], 16) >= 8 else c for i, c in enumerate(addr)
    )


def abi_encode_call(sig: str, args: list[Any]) -> str:
    """Very small ABI encoder for address/uint/bool/string/bytes32 args."""
    selector = keccak(sig.encode("ascii")).hex()[:8]
    head = [selector]
    for arg in args:
        if isinstance(arg, bool):
            head.append(f"{1 if arg else 0:064x}")
        elif isinstance(arg, int):
            head.append(f"{arg & ((1 << 256) - 1):064x}")
        elif isinstance(arg, str) and arg.startswith("0x") and len(arg) == 66:
            head.append(arg[2:])  # bytes32 literal
        elif isinstance(arg, str) and len(arg) == 40:  # plain 40-hex address
            head.append("0" * 24 + arg.lower())
        elif isinstance(arg, str) and arg.startswith("0x"):
            raw = arg[2:]
            if len(raw) != 64:
                raise SafeMintError("Only 32-byte hex args supported in minimal encoder")
            head.append(raw)
        else:
            raise SafeMintError(f"Unsupported argument type for minimal encoder: {arg!r}")
    return "0x" + "".join(head)


def encode_function_call(selector_hex: str, *args: int | str) -> str:
    """Generic: selector + 32-byte words (int) or address-padded (str 0x..40)."""
    parts = [selector_hex]
    for arg in args:
        if isinstance(arg, int):
            parts.append(f"{arg & ((1 << 256) - 1):064x}")
        elif isinstance(arg, str) and arg.startswith("0x") and len(arg) == 42:
            parts.append("0" * 24 + arg[2:].lower())
        else:
            raise SafeMintError(f"Unsupported arg {arg!r}")
    return "0x" + "".join(parts)


def parse_function_call(parts: list[str]) -> str:
    """Parse CLI --function-call 'name(uint256,address)' '1' '0x..' into calldata.

    Supports address / bool / uintN / bytes32 static args (covers mintPublic
    variants). Selector is keccak(signature) — the fixed, EVM-correct path
    (was previously passed through as raw hex, which never worked).
    """
    if not parts:
        raise SafeMintError("--function-call requires a signature + args")
    sig = parts[0]
    args = parts[1:]
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(([^)]*)\)$", sig)
    if not m:
        raise SafeMintError(
            f"Invalid signature {sig!r} — expected 'name(uint256,address)'"
        )
    types = [t.strip() for t in m.group(2).split(",") if t.strip()]
    if len(types) != len(args):
        raise SafeMintError(
            f"Signature {sig!r} has {len(types)} params but {len(args)} args given"
        )
    selector = keccak(sig.encode("ascii")).hex()[:8]
    words: list[str] = []
    for t, a in zip(types, args):
        if t == "address":
            addr = a.lower().removeprefix("0x")
            if not re.fullmatch(r"[0-9a-f]{40}", addr):
                raise SafeMintError(f"Invalid address arg {a!r}")
            words.append("0" * 24 + addr)
        elif t == "bool":
            words.append(f"{1 if a.strip().lower() in ('true', '1', 'yes') else 0:064x}")
        elif t.startswith("uint"):
            if a.strip().startswith("-"):
                raise SafeMintError(f"Negative uint arg {a!r} for type {t!r} in {sig!r}")
            val = int(a, 0)
            if val < 0 or val >= (1 << 256):
                raise SafeMintError(f"uint arg {a!r} out of range for {t!r}")
            bits = int(t[4:]) if t[4:].isdigit() else 256
            if bits < 256 and val >= (1 << bits):
                raise SafeMintError(
                    f"uint arg {a!r} exceeds {bits}-bit width for type {t!r}"
                )
            words.append(f"{val:064x}")
        elif t == "bytes32":
            b = a.removeprefix("0x").lower()
            if len(b) != 64:
                raise SafeMintError(f"bytes32 arg must be 64 hex chars: {a!r}")
            words.append(b)
        else:
            raise SafeMintError(f"Unsupported type {t!r} in signature {sig!r}")
    return "0x" + selector + "".join(words)


# --------------------------------------------------------------------------
# Keystore (repo strength #1)
# --------------------------------------------------------------------------
def create_keystore(private_key: str, password: str, path: Path) -> str:
    from eth_account import Account

    if len(password) < 12:
        raise SafeMintError("Keystore password must be at least 12 characters")
    try:
        account = Account.from_key(private_key)
        encrypted = Account.encrypt(account.key, password)
    except Exception as exc:  # noqa: BLE001
        raise SafeMintError(f"Invalid private key: {exc}") from exc
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(encrypted), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)
    return str(account.address)


def unlock_account(keystore_path: Path, password: str):
    from eth_account import Account

    try:
        payload = json.loads(keystore_path.read_text(encoding="utf-8"))
        key = Account.decrypt(payload, password)
        return Account.from_key(key)
    except Exception as exc:  # noqa: BLE001
        raise SafeMintError(f"Could not unlock keystore (wrong password?): {exc}") from exc


# --------------------------------------------------------------------------
# Hard limits (repo strength #5)
# --------------------------------------------------------------------------
WEI_PER_ETH = 10**18


def eth_to_wei(value: Decimal | str | int) -> int:
    dv = value if isinstance(value, Decimal) else Decimal(str(value))
    if dv < 0:
        raise ValueError("ETH amount cannot be negative")
    wei = (dv * WEI_PER_ETH).to_integral_value(rounding=ROUND_DOWN)
    if dv > 0 and wei == 0:
        raise ValueError(
            f"ETH amount {dv} is below 1 wei (minimum transferable unit) — refusing to send value=0"
        )
    return int(wei)


@dataclass(frozen=True, slots=True)
class SpendCheck:
    mint_wei: int
    network_fee_wei: int
    total_wei: int
    balance_wei: int

    def as_table(self) -> dict[str, str]:
        return {
            "mint_eth": str(Decimal(self.mint_wei) / WEI_PER_ETH),
            "fee_eth": str(Decimal(self.network_fee_wei) / WEI_PER_ETH),
            "total_eth": str(Decimal(self.total_wei) / WEI_PER_ETH),
            "balance_eth": str(Decimal(self.balance_wei) / WEI_PER_ETH),
        }


def enforce_limits(
    *,
    quantity: int,
    mint_wei: int,
    gas_limit: int,
    gas_price_wei: int,
    balance_wei: int,
    max_price_per_nft_wei: int,
    max_network_fee_wei: int,
    max_total_spend_wei: int,
    buffer_wei: int = 0,
) -> SpendCheck:
    if quantity < 1:
        raise LimitViolation("Quantity must be positive")
    if max_price_per_nft_wei > 0 and mint_wei > max_price_per_nft_wei * quantity:
        raise LimitViolation(
            f"Current mint value {Decimal(mint_wei)/WEI_PER_ETH} ETH exceeds max NFT price "
            f"{Decimal(max_price_per_nft_wei)/WEI_PER_ETH} ETH"
        )
    network_fee = gas_limit * gas_price_wei
    if network_fee > max_network_fee_wei:
        raise LimitViolation(
            f"Worst-case fee {Decimal(network_fee)/WEI_PER_ETH} ETH exceeds max fee "
            f"{Decimal(max_network_fee_wei)/WEI_PER_ETH} ETH"
        )
    total = mint_wei + network_fee
    if total > max_total_spend_wei:
        raise LimitViolation(
            f"Worst-case total {Decimal(total)/WEI_PER_ETH} ETH exceeds max total "
            f"{Decimal(max_total_spend_wei)/WEI_PER_ETH} ETH"
        )
    if total + buffer_wei > balance_wei:
        raise LimitViolation(
            f"Wallet balance {Decimal(balance_wei)/WEI_PER_ETH} ETH insufficient for cost "
            f"{Decimal(total)/WEI_PER_ETH} ETH + buffer {Decimal(buffer_wei)/WEI_PER_ETH} ETH"
        )
    return SpendCheck(mint_wei, network_fee, total, balance_wei)


# --------------------------------------------------------------------------
# Duplicate guard (repo strength #6) — SQLite state
# --------------------------------------------------------------------------
def state_db(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or Path.home() / ".nft-safe-mint" / "state.sqlite3"
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(db_path.parent, 0o700)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet TEXT NOT NULL,
            contract TEXT NOT NULL,
            state TEXT NOT NULL,
            tx_hash TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def already_broadcast(conn: sqlite3.Connection, wallet: str, contract: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM runs WHERE wallet=? AND contract=? AND state IN ('BROADCAST','CONFIRMED')",
        (wallet.lower(), contract.lower()),
    ).fetchone()
    return row is not None


def record_run(conn: sqlite3.Connection, wallet: str, contract: str, state: str, tx_hash: str | None) -> None:
    conn.execute(
        "INSERT INTO runs (wallet, contract, state, tx_hash, created_at) VALUES (?,?,?,?,?)",
        (
            wallet.lower(),
            contract.lower(),
            state,
            tx_hash,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


# --------------------------------------------------------------------------
# OpenSea (repo strength #9)
# --------------------------------------------------------------------------
def parse_opensea_mint_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"opensea.io", "www.opensea.io"}:
        raise SafeMintError("Use a complete https://opensea.io mint or collection link")
    parts = [unquote(p).strip() for p in parsed.path.split("/") if p.strip()]
    for marker in ("collection", "drops", "drop"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                slug = parts[idx + 1]
                if slug.replace("-", "").replace("_", "").isalnum():
                    return slug
    raise SafeMintError("Link must contain /collection/<slug> or /drops/<slug>; item/listing links are not mints")


class OpenSeaClient:
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if api_key:
            self.session.headers["x-api-key"] = api_key

    def _request(self, method: str, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self.session.request(
            method, f"{OPENSEA_API_ROOT}{path}", json=json_body, timeout=12, allow_redirects=False
        )
        if resp.status_code not in {200, 201}:
            msgs = {404: "drop not found", 409: "drop not active yet", 422: "wallet not eligible", 429: "rate limited"}
            raise SafeMintError(f"OpenSea {path}: {msgs.get(resp.status_code, resp.status_code)}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise SafeMintError("OpenSea returned invalid JSON") from exc
        return payload

    def get_drop(self, slug: str) -> dict[str, Any]:
        return self._request("GET", f"/drops/{slug}")

    def build_mint(self, slug: str, minter: str, quantity: int) -> dict[str, Any]:
        return self._request("POST", f"/drops/{slug}/mint", json_body={"minter": minter, "quantity": quantity})


# --------------------------------------------------------------------------
# Free-mint on-chain verification (repo strength #8)
# --------------------------------------------------------------------------
def probe_public_drop_price(client: RPCClient, contract: str) -> dict[str, Any]:
    """Call getPublicDrop(contract) on the contract. Returns decoded fields or error."""
    calldata = encode_address(SEADROP_PUBLIC_DROP_SELECTOR, contract)
    try:
        result = client.call("eth_call", [{"to": contract, "data": calldata}, "latest"])
        mint_price = decode_uint256(result, 0)
        start_time = decode_uint256(result, 1)
        end_time = decode_uint256(result, 2)
        max_wallet = decode_uint256(result, 3)
        fee_bps = decode_uint256(result, 4)
        restrict_fee = decode_uint256(result, 5)
        # Sanity: standard PublicDrop = 6×32B words (uint80,uint48,uint48,uint16,uint16,bool).
        # Flag nonsense values (bad ABI decode / wrong contract / reorg) instead of trusting them.
        flags: list[str] = []
        if mint_price >= (1 << 80):
            flags.append("mint_price>2^80")
        if start_time >= (1 << 48) or end_time >= (1 << 48):
            flags.append("time>2^48")
        if max_wallet >= (1 << 16) or fee_bps >= (1 << 16):
            flags.append("uint16_overflow")
        if fee_bps > 10000:
            flags.append("fee_bps>10000")
        if end_time and start_time and end_time <= start_time:
            flags.append("end<=start")
        return {
            "ok": True,
            "mint_price_wei": mint_price,
            "mint_price_eth": str(Decimal(mint_price) / WEI_PER_ETH),
            "free": mint_price == 0,
            "start_time": start_time,
            "end_time": end_time,
            "max_per_wallet": max_wallet,
            "fee_bps": fee_bps,
            "restrict_fee_recipients": bool(restrict_fee),
            "sanity_flags": flags,
            "sane": not flags,
        }
    except SafeMintError as exc:
        return {"ok": False, "error": redact(str(exc))}


def probe_supply(client: RPCClient, contract: str) -> dict[str, Any]:
    try:
        total = decode_uint256(client.call("eth_call", [{"to": contract, "data": TOTAL_SUPPLY_SELECTOR}, "latest"]))
    except SafeMintError:
        total = None
    try:
        mx = decode_uint256(client.call("eth_call", [{"to": contract, "data": MAX_SUPPLY_SELECTOR}, "latest"]))
    except SafeMintError:
        mx = None
    return {"total_supply": total, "max_supply": mx, "sold_out": total is not None and mx is not None and total >= mx}


# --------------------------------------------------------------------------
# Watch-then-fire engine (repo strengths #3/#4/#7)
# --------------------------------------------------------------------------
def build_transaction(
    chain_id: int,
    to: str,
    nonce: int,
    gas_price: int,
    gas_limit: int,
    data: str,
    value_wei: int,
) -> dict[str, Any]:
    return {
        "chainId": chain_id,
        "to": to,
        "nonce": nonce,
        "data": data,
        "value": value_wei,
        "gas": gas_limit,
        "gasPrice": gas_price,
    }


def run_watch(
    *,
    rpc_urls: list[str],
    chain_id: int,
    contract: str,
    keystore_path: Path,
    password: str,
    quantity: int,
    data: str,
    value_wei: int,
    max_price_eth: str,
    max_fee_eth: str,
    max_total_eth: str,
    balance_buffer_eth: str,
    mode: str,  # watch | confirm | auto | dry-run
    poll_interval_ms: int,
    stop_after: float | None,
    backup_urls: list[str] | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Monitor until eth_call on the mint calldata succeeds, then execute with all checks."""
    from eth_account import Account

    all_urls = rpc_urls + (backup_urls or [])
    if not all_urls:
        raise SafeMintError("At least one RPC required")
    pool = RPCPool(all_urls, chain_id)
    ranked = pool.validate()
    if not ranked:
        raise SafeMintError("No RPC endpoint passed chain + freshness validation — check --rpc/--backup")
    print(f"[watch] RPC pool ({len(ranked)} healthy): " + ", ".join(h.url for h in ranked))
    primary = pool  # pool.call() fails over automatically

    account = unlock_account(keystore_path, password)
    checksum_contract = checksum_address(contract)
    contract_lower = checksum_contract.lower()

    code = get_code(pool, checksum_contract)
    if code in {"0x", "0x0"}:
        raise SafeMintError("Contract has no code on this chain — wrong address or wrong network")

    conn = state_db(state_path)
    if already_broadcast(conn, account.address, contract_lower):
        raise SafeMintError(
            "Duplicate guard: this wallet already broadcast for this target. "
            "Use a fresh keystore/wallet or clear the state DB deliberately."
        )

    print(f"[watch] chain_id={chain_id} contract={checksum_contract} wallet={account.address}")
    print(f"[watch] value={Decimal(value_wei)/WEI_PER_ETH} ETH quantity={quantity} mode={mode}")

    # Supply / free-mint probe (blocking in auto mode, informational otherwise)
    supply = probe_supply(primary, checksum_contract)
    if supply.get("sold_out"):
        print(f"[!] Contract reports SOLD OUT (totalSupply={supply['total_supply']} == maxSupply={supply['max_supply']})")
    drop = probe_public_drop_price(primary, checksum_contract)
    if drop.get("ok"):
        free = "FREE" if drop["free"] else f"PAID {drop['mint_price_eth']} ETH"
        print(f"[!] getPublicDrop: {free}, start={drop['start_time']}, end={drop['end_time']}, "
              f"per-wallet={drop['max_per_wallet']}, fee_bps={drop['fee_bps']}")
        if not drop["free"] and Decimal(drop["mint_price_eth"]) > 0 and value_wei == 0:
            if mode == "auto":
                raise SafeMintError(
                    f"BLOCKED: contract says PAID ({drop['mint_price_eth']} ETH) but --value=0. "
                    "Auto mode refuses paid mints. Pass --value or use 'watch' mode to confirm manually."
                )
            print("[!] WARNING: contract says PAID but --value is 0. Simulation below will reveal truth.")
    else:
        print(f"[!] getPublicDrop unavailable (non-SeaDrop or proxy): {drop.get('error','?')}")

    call_obj = {"from": account.address, "to": checksum_contract, "data": data, "value": hex(value_wei)}
    interval = poll_interval_ms / 1000.0
    started = time.monotonic()

    # Validate chain BEFORE watch loop so RPC can't lie about chainId during eth_call simulation
    validate_chain(primary, chain_id)

    print(f"[watch] waiting for successful simulation (poll {interval}s)...")
    while True:
        try:
            primary.call("eth_call", [call_obj, "pending"], max_retries=1)
            break  # mint is now live
        except SafeMintError as exc:
            if stop_after is not None and time.monotonic() - started > stop_after:
                raise SafeMintError(f"Watch timeout after {stop_after}s — mint never simulated") from exc
            time.sleep(interval)

    print("[watch] TRIGGER: eth_call succeeded — refreshing nonce/gas/balance...")
    # Refresh probe + pin to a SINGLE endpoint for fire-time reads so nonce/gas/balance
    # share the same mempool view (avoids cross-endpoint inconsistency flagged by review).
    pool.invalidate()
    pool.validate()
    fire = pool.best_client()
    nonce_hex = fire.call("eth_getTransactionCount", [account.address, "pending"])
    gas_price_hex = fire.call("eth_gasPrice")
    balance_hex = fire.call("eth_getBalance", [account.address, "pending"])
    # chain_id is already validated — re-check on fire endpoint for consistency
    fire_chain_hex = fire.call("eth_chainId")
    if int(fire_chain_hex, 16) != chain_id:
        raise SafeMintError(f"Chain mismatch: primary={chain_id} fire={int(fire_chain_hex, 16)}")
    nonce = int(nonce_hex, 16)
    gas_price = int(gas_price_hex, 16)
    balance = int(balance_hex, 16)

    # Mandatory final simulation + gas estimate (fast-fail on 429 — max_retries=1)
    fire.call("eth_call", [call_obj, "pending"], max_retries=1)
    gas_hex = fire.call(
        "eth_estimateGas",
        [
            {
                **call_obj,
                "nonce": hex(nonce),
                "gasPrice": hex(gas_price),
            }
        ],
        max_retries=1,
    )
    gas_limit = max(21_000, int(int(gas_hex, 16) * 1.15))

    # Hard limits
    spend = enforce_limits(
        quantity=quantity,
        mint_wei=value_wei,
        gas_limit=gas_limit,
        gas_price_wei=gas_price,
        balance_wei=balance,
        max_price_per_nft_wei=eth_to_wei(max_price_eth),
        max_network_fee_wei=eth_to_wei(max_fee_eth),
        max_total_spend_wei=eth_to_wei(max_total_eth),
        buffer_wei=eth_to_wei(balance_buffer_eth),
    )
    print(f"[limits] {json.dumps(spend.as_table())}")
    print(f"[limits] gas={gas_limit} gasPrice={gas_price} wei")

    if mode in {"watch", "dry-run"}:
        record_run(conn, account.address, contract_lower, "SKIPPED", None)
        return {"state": "SKIPPED", "spend": spend.as_table(), "message": "Dry run: safe to submit (not broadcast)"}

    if mode == "confirm":
        answer = input("Simulation + limits pass. Broadcast now? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            record_run(conn, account.address, contract_lower, "SKIPPED", None)
            return {"state": "SKIPPED", "spend": spend.as_table(), "message": "User declined broadcast"}

    tx = build_transaction(
        chain_id=chain_id,
        to=checksum_contract,
        nonce=nonce,
        gas_price=gas_price,
        gas_limit=gas_limit,
        data=data,
        value_wei=value_wei,
    )
    signed = account.sign_transaction(tx)
    raw_hex = "0x" + signed.raw_transaction.hex()
    print("[sign] signed locally, propagating to up to 2 healthy RPCs...")

    # Broadcast same raw bytes to up to 2 healthy endpoints (repo strength #10)
    tx_hash = pool.broadcast_same_raw(raw_hex)
    print(f"[broadcast] tx_hash={tx_hash}")

    record_run(conn, account.address, contract_lower, "BROADCAST", tx_hash)
    print(f"[done] tx_hash={tx_hash}")
    print("[done] waiting for receipt (up to 120s)...")
    deadline = time.monotonic() + 120
    receipt = None
    while time.monotonic() < deadline:
        try:
            receipt = fire.call("eth_getTransactionReceipt", [tx_hash], max_retries=1)
            if receipt is not None:
                break
        except SafeMintError:
            pass
        time.sleep(0.5)
    if receipt is None:
        print("[done] receipt timeout — tx may still be pending. Check explorer.")
        state = "PENDING"
    else:
        state = "CONFIRMED" if int(receipt.get("status", "0x0"), 16) == 1 else "FAILED"
        print(f"[done] state={state} block={receipt.get('blockNumber')}")
    record_run(conn, account.address, contract_lower, state, tx_hash)
    return {"state": state, "tx_hash": tx_hash, "spend": spend.as_table()}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def resolve_password(cli_value: str) -> str:
    """Prefer --password; fall back to SAFE_MINT_PASSWORD env var.
    Lets users avoid passing the keystore password on the command line
    (visible in `ps aux` / shell history)."""
    if cli_value:
        print("WARNING: --password visible in process list — prefer SAFE_MINT_PASSWORD env", file=sys.stderr)
        return cli_value
    env_val = os.environ.get("SAFE_MINT_PASSWORD", "")
    if env_val:
        return env_val
    raise SafeMintError(
        "--password required — pass it or set SAFE_MINT_PASSWORD (safer: keeps it out of `ps`/history)"
    )


def cmd_keystore_create(args: argparse.Namespace) -> int:
    path = Path(args.out)
    address = create_keystore(args.key, resolve_password(args.password), path)
    print(f"keystore written: {path}")
    print(f"address: {address}")
    return 0


def cmd_keystore_addr(args: argparse.Namespace) -> int:
    acct = unlock_account(Path(args.keystore), resolve_password(args.password))
    print(acct.address)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    urls = [args.rpc] + ([u for u in (args.backup or "").split(",") if u.strip()] if args.backup else [])
    pool = RPCPool(urls, args.chain_id)
    ranked = pool.validate()
    if not ranked:
        raise SafeMintError("No RPC endpoint passed chain + freshness validation")
    client = pool.clients[ranked[0].url]
    validate_chain(client, args.chain_id)
    contract = checksum_address(args.contract)
    code = get_code(pool, contract)
    print(f"chain: {args.chain_id} (valid)")
    print(f"rpc: {ranked[0].url} (latency {ranked[0].latency_ms:.0f}ms, block {ranked[0].block_number})")
    print(f"contract code: {'present' if code not in {'0x','0x0'} else 'MISSING'}")
    if args.keystore:
        acct = unlock_account(Path(args.keystore), resolve_password(args.password))
        bal = int(pool.call("eth_getBalance", [acct.address, "latest"]), 16)
        print(f"wallet: {acct.address} balance: {Decimal(bal)/WEI_PER_ETH} ETH")
        if args.max_total:
            need = eth_to_wei(args.max_total) + eth_to_wei(args.balance_buffer)
            print(f"limits: max_total={args.max_total} buffer={args.balance_buffer} -> need {Decimal(need)/WEI_PER_ETH} ETH")
            print(f"status: {'OK' if bal >= need else 'INSUFFICIENT BALANCE'}")
    supply = probe_supply(pool, contract)
    if supply.get("sold_out"):
        print(f"[!] SOLD OUT totalSupply={supply['total_supply']} maxSupply={supply['max_supply']}")
    drop = probe_public_drop_price(pool, contract)
    if drop.get("ok"):
        print(f"getPublicDrop: {'FREE' if drop['free'] else 'PAID ' + drop['mint_price_eth'] + ' ETH'}"
              f" start={drop['start_time']} end={drop['end_time']} perWallet={drop['max_per_wallet']} feeBps={drop['fee_bps']}")
    else:
        print(f"getPublicDrop: unavailable ({drop.get('error','?')})")
    pool.close()
    return 0


def cmd_rpc_test(args: argparse.Namespace) -> int:
    """Probe + rank RPC endpoints. Read-only — no broadcast ever."""
    urls = [args.rpc] + ([u for u in (args.backup or "").split(",") if u.strip()] if args.backup else [])
    pool = RPCPool(urls, args.chain_id)
    print(f"[rpc-test] probing {len(urls)} endpoint(s) for chain {args.chain_id} (read-only)...")
    for h in pool.validate():
        print(f"  OK   {h.url}  latency={h.latency_ms:7.1f}ms  block={h.block_number}  ok={h.successes} fail={h.failures}")
    for url, h in pool.health.items():
        if h.successes == 0 or h.last_error is not None:
            print(f"  FAIL {url}  -> {h.last_error or 'no successful probe'}")
    ranked = pool.ranked_urls()
    if not ranked:
        print("[rpc-test] NO healthy endpoint. Mint pipeline cannot proceed.")
        pool.close()
        return 1
    print(f"[rpc-test] ranking: " + " > ".join(ranked))
    pool.close()
    return 0


def _load_os_api_key() -> str:
    """Load OpenSea API key from ~/.opensea/api_key (instant key cache)."""
    p = Path.home() / ".opensea" / "api_key"
    if p.exists():
        return p.read_text().strip()
    return ""

def cmd_os_check(args: argparse.Namespace) -> int:
    slug = parse_opensea_mint_url(args.url)
    api_key = args.api_key or _load_os_api_key()
    client = OpenSeaClient(api_key)
    drop = client.get_drop(slug)
    chain = str(drop.get("chain", "")).lower()
    print(f"slug: {slug}")
    print(f"chain: {chain} (must be robinhood)")
    contract = drop.get("contract_address") or (drop.get("contract") or {}).get("address")
    print(f"contract: {contract}")
    active = isinstance(drop.get("active_stage"), dict)
    print(f"active: {active}")
    if not args.wallet:
        # NOTE: this read-only path (GET drop only) does NOT reserve anything.
        # Only the POST /drops/{slug}/mint below can reserve a drop slot.
        return 0
    mint = client.build_mint(slug, args.wallet, args.quantity)
    mchain = str(mint.get("chain", "")).lower()
    value = int(str(mint.get("value", "0")), 0)
    data = mint.get("data", "")
    print(f"mint chain: {mchain} to: {mint.get('to')}")
    print(f"mint value: {Decimal(value)/WEI_PER_ETH} ETH -> {'FREE' if value == 0 else 'PAID'}")
    print(f"calldata len: {len(data)//2 - 1} bytes (selector {data[:10]})")
    print(f"VERDICT: {'OK FREE MINT' if mchain=='robinhood' and value==0 else 'NOT free — do not auto-mint'}")
    # os-check --wallet POSTs /drops/{slug}/mint to OpenSea. Per the OpenSea
    # drops API this may RESERVE a drop slot server-side — do not run this
    # repeatedly or before you are ready to actually mint the drop.
    print("[!] note: POST /drops/{slug}/mint can reserve a drop slot on OpenSea's side")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    if args.data:
        data = args.data
        if not data.startswith("0x"):
            raise SafeMintError("--data must be hex calldata starting with 0x")
    elif args.function_call:
        data = parse_function_call(args.function_call)
    else:
        raise SafeMintError("Provide --data (hex calldata) or --function-call 'name(uint256,address)' '1' '0x..'")
    run_watch(
        rpc_urls=[args.rpc],
        backup_urls=args.backup.split(",") if args.backup else [],
        chain_id=args.chain_id,
        contract=args.contract,
        keystore_path=Path(args.keystore),
        password=resolve_password(args.password),
        quantity=args.quantity,
        data=data,
        value_wei=eth_to_wei(args.value),
        max_price_eth=args.max_price,
        max_fee_eth=args.max_fee,
        max_total_eth=args.max_total,
        balance_buffer_eth=args.balance_buffer,
        mode=args.mode,
        poll_interval_ms=args.poll_interval,
        stop_after=args.stop_after,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="safe_mint.py", description="Security-first RH Chain NFT mint helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # keystore create
    ks_create = sub.add_parser("keystore", help="keystore subcommands")
    ks_sub = ks_create.add_subparsers(dest="ks_cmd", required=True)
    kc = ks_sub.add_parser("create", help="encrypt a private key into a Web3 keystore")
    kc.add_argument("--key", required=True, help="private key (0x...) — hidden by shell history best practices")
    kc.add_argument("--password", default="", help="keystore password (12+ chars) or SAFE_MINT_PASSWORD env")
    kc.add_argument("--out", required=True, help="output path")
    kc.set_defaults(func=cmd_keystore_create)
    ka = ks_sub.add_parser("addr", help="show address of an encrypted keystore")
    ka.add_argument("--keystore", required=True)
    ka.add_argument("--password", default="", help="or SAFE_MINT_PASSWORD env")
    ka.set_defaults(func=cmd_keystore_addr)

    # check
    ck = sub.add_parser("check", help="pre-flight chain/contract/balance/free-mint check")
    ck.add_argument("--rpc", default=DEFAULT_RH_RPCS[0])
    ck.add_argument("--backup", default=",".join(DEFAULT_RH_RPCS[1:]),
                    help="comma-separated backup RPCs (pool failover)")
    ck.add_argument("--chain-id", type=int, default=MAINNET_CHAIN_ID)
    ck.add_argument("--contract", required=True)
    ck.add_argument("--keystore")
    ck.add_argument("--password", default="")
    ck.add_argument("--max-total")
    ck.add_argument("--balance-buffer", default="0.002")
    ck.set_defaults(func=cmd_check)

    # rpc-test
    rt = sub.add_parser("rpc-test", help="probe + rank RPC endpoints (read-only, no broadcast)")
    rt.add_argument("--rpc", default=DEFAULT_RH_RPCS[0])
    rt.add_argument("--backup", default=",".join(DEFAULT_RH_RPCS[1:]),
                    help="comma-separated backup RPCs to probe")
    rt.add_argument("--chain-id", type=int, default=MAINNET_CHAIN_ID)
    rt.set_defaults(func=cmd_rpc_test)

    # os-check
    oc = sub.add_parser("os-check", help="OpenSea drop: verify chain + free value before mint")
    oc.add_argument("--url", required=True, help="https://opensea.io/drops/<slug>")
    oc.add_argument("--wallet", help="minter address to build tx for")
    oc.add_argument("--quantity", type=int, default=1)
    oc.add_argument("--api-key", default="")
    oc.set_defaults(func=cmd_os_check)

    # watch
    wc = sub.add_parser("watch", help="watch-then-fire with full safety pipeline")
    wc.add_argument("--rpc", default=DEFAULT_RH_RPCS[0])
    wc.add_argument("--backup", default=",".join(DEFAULT_RH_RPCS[1:]),
                    help="comma-separated backup RPCs (pool failover)")
    wc.add_argument("--chain-id", type=int, default=MAINNET_CHAIN_ID)
    wc.add_argument("--contract", required=True)
    wc.add_argument("--keystore", required=True)
    wc.add_argument("--password", default="", help="keystore password or SAFE_MINT_PASSWORD env (safer than CLI)")
    wc.add_argument("--quantity", type=int, default=1)
    wc.add_argument("--value", default="0", help="mint value in ETH")
    wc.add_argument("--data", default="", help="hex calldata (function selector + args)")
    wc.add_argument("--function-call", nargs="+", default=None, metavar=("SIG", "ARG"),
                    help="e.g. --function-call 'mintPublic(uint256,address)' '1' '0x..'")
    wc.add_argument("--max-price", default="0", help="max price per NFT in ETH")
    wc.add_argument("--max-fee", default="0.0005")
    wc.add_argument("--max-total", default="0.001")
    wc.add_argument("--balance-buffer", default="0.002")
    wc.add_argument("--mode", choices=["watch", "confirm", "auto", "dry-run"], default="confirm")
    wc.add_argument("--poll-interval", type=int, default=175, help="ms between eth_call polls")
    wc.add_argument("--stop-after", type=float, default=None, help="max seconds to watch")
    wc.set_defaults(func=cmd_watch)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (SafeMintError, LimitViolation) as exc:
        print(f"ERROR: {redact(str(exc))}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
