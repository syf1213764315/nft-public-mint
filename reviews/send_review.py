#!/usr/bin/env python3
"""Send nft-public-mint skill files to MiniMax M3 for a proper code+docs review."""
import json, sqlite3, sys, textwrap, time

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

# --- assemble materials ---
sk = read(f"{SKILL_DIR}/SKILL.md")
code = read(f"{SKILL_DIR}/scripts/safe_mint.py")
ref = read(f"{SKILL_DIR}/references/security-first-mint-pipeline.md")

prompt = f"""You are an expert smart-contract / security engineer and code reviewer. Review this Hermes skill package `nft-public-mint` — specifically the `safe_mint.py` security-first mint pipeline for the Robinhood Chain (EVM chain 4663).

TASK: Determine whether this skill is PROPER — correct, safe, consistent, and production-ready for minting NFTs. Be critical and specific. Find REAL bugs, not style nits.

Review dimensions:
1. CODE CORRECTNESS — real logic bugs, ABI encoding errors, decoding offsets, edge cases (nonce, gas, sold-out, revert propagation), RPCPool logic (ranking, failover, negative cache, broadcast dedup).
2. SECURITY — private key handling, keystore perms, redaction, tx safety, chain-lock, duplicate guard soundness, anything that could lose money or leak keys.
3. DOCS-VS-CODE DRIFT — does SKILL.md / reference doc describe commands/flags/flows that no longer match the actual argparse CLI in safe_mint.py? List exact mismatches (command names, flag names, defaults, mode names).
4. MISSING / WEAK — claims in the doc not actually enforced in code, gaps vs the "10 strengths" table, anything that would let a PAID mint slip through as free, or a broadcast fire with wrong params.
5. VERDICT — is it PROPER (ship-ready), NEEDS-FIX (list must-fix items), or NOT-PROPER (redesign)?

Be concrete: cite line numbers and quote the exact code. If something is correct, say so briefly. Prioritize findings that could cause real financial loss on mainnet.

=== FILE 1: SKILL.md (truncated to essentials) ===
{sk[:6000]}

=== FILE 2: scripts/safe_mint.py (FULL) ===
{code}

=== FILE 3: references/security-first-mint-pipeline.md (FULL) ===
{ref}
"""

import requests
KEY = get_key()
r = requests.post(
    "http://127.0.0.1:20128/v1/chat/completions",
    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    json={
        "model": "gmi/MiniMaxAI/MiniMax-M3",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 6000,
    },
    timeout=600,
)
print("HTTP", r.status_code)
if r.status_code != 200:
    print(r.text[:2000])
    sys.exit(1)
# strip trailing SSE "data: [DONE]" (9router appends it even for non-stream)
text = r.text
if text.rstrip().endswith('data: [DONE]'):
    text = text[: text.rfind('data: [DONE]')]
body = json.loads(text)
content = body["choices"][0]["message"]["content"]
print(content)
