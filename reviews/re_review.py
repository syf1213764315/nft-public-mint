#!/usr/bin/env python3
"""Re-review nft-public-mint skill by MiniMax M3 after all findings were fixed (2026-08-30)."""
import json, sqlite3, sys, time

SKILL_DIR = "/data/data/com.termux/files/home/.hermes/skills/nft-public-mint"
DB = "/data/data/com.termux/files/home/.9router/db/data.sqlite"

def get_key(name="audit-final2"):
    db = sqlite3.connect(DB)
    row = db.execute("SELECT key FROM apiKeys WHERE name=?", (name,)).fetchone()
    db.close()
    if not row:
        raise SystemExit(f"no api key named {name}")
    return row[0]

def read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()

sk = read(f"{SKILL_DIR}/SKILL.md")
code = read(f"{SKILL_DIR}/scripts/safe_mint.py")
ref = read(f"{SKILL_DIR}/references/security-first-mint-pipeline.md")

prompt = f"""You are the SAME security reviewer that earlier flagged NEEDS-FIX bugs in this skill package `nft-public-mint` (safe_mint.py, Robinhood Chain 4663 mint pipeline). Those findings have now been addressed. Your job: VERIFY the fixes and find any REMAINING or NEW problems.

PREVIOUS FINDINGS (stated as FIXED):
1. CRITICAL: hashlib.sha3_256 (NIST SHA3) used instead of EVM Keccak-256 in abi_encode_call selector, checksum_address (EIP-55), broadcast_same_raw dedup hash. -> FIXED: added `keccak()` helper via eth_hash.auto, all 3 sites now use keccak; NIST sha3 import removed.
2. CRITICAL: broadcast_same_raw treated "nonce too low" as already-broadcast success (fake tx hash). -> FIXED: "nonce too low" now raises a fatal error; only "already known"/"known transaction" returns deterministic hash.
3. CRITICAL: probe_public_drop_price mis-decoded PublicDrop struct (claimed packed 2-word). -> NOT CHANGED: maintainer verified SeaDrop returns standard ABI tuple (5 words, each member 32-byte padded); decode_uint256(result, 0..4) is correct. Confirm whether this reasoning is right.
4. HIGH: already_broadcast included FAILED state -> permanent lockout after a failed mint. -> FIXED: only BROADCAST/CONFIRMED block retry; FAILED now allows retry.
5. HIGH: RPCPool cached ranking never invalidated; pool.call() could hit different endpoints for nonce vs gas vs balance (inconsistent mempool view). -> FIXED: added pool.invalidate() + best_client(); fire-time reads now pin to ONE freshly-probed endpoint.
6. MEDIUM: 429 in pool path treated as non-transport (no failover), and hot loop could block 66s (6 retries x 11s). -> FIXED: is_transport now matches "RPC 429"; RPCPool.call passes max_retries through; watch hot poll uses max_retries=1.
7. MEDIUM: --function-call was not parsed (raw string passed as data). -> FIXED: parse_function_call() encodes sig+args with keccak selector (address/bool/uintN/bytes32); argparse nargs='+'.
8. MEDIUM: password on command line visible in ps. -> FIXED: SAFE_MINT_PASSWORD env fallback; --password optional.
9. MEDIUM: --max-price 0 clashed with --value (hard LimitViolation). -> FIXED: max_price_per_nft_wei=0 now means "no per-NFT limit" (skips check).
10. DOCS: drift between SKILL.md/reference and actual CLI; missing warnings (auto mode no confirm, os-check POST reserves slot). -> FIXED: docs updated.

=== FILE 1: SKILL.md (truncated) ===
{sk[:6000]}

=== FILE 2: scripts/safe_mint.py (FULL) ===
{code}

=== FILE 3: references/security-first-mint-pipeline.md (FULL) ===
{ref}

OUTPUT FORMAT (be concise, engineer-to-engineer):
- For each of the 10 findings above: VERIFIED-FIXED / STILL-BROKEN / PARTIAL — with one-line evidence (cite line).
- Any NEW critical/high/medium findings (real bugs only).
- Final verdict: PROPER / NEEDS-FIX / NOT-PROPER.
"""

import requests
KEY = get_key()
r = requests.post(
    "http://127.0.0.1:20128/v1/chat/completions",
    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    json={
        "model": "gmi/MiniMaxAI/MiniMax-M3",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 5000,
    },
    timeout=600,
)
print("HTTP", r.status_code)
if r.status_code != 200:
    print(r.text[:2000])
    sys.exit(1)
text = r.text
if text.rstrip().endswith('data: [DONE]'):
    text = text[: text.rfind('data: [DONE]')]
body = json.loads(text)
print(body["choices"][0]["message"]["content"])
