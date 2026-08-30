#!/usr/bin/env python3
"""Full-skill, all-chain review of nft-public-mint by MiniMax M3 (2026-08-30).

Covers ALL four components of the skill package:
  1. SKILL.md routing + references (multichain-rpc-reference, robinhood-chain-pitfalls, security-first-mint-pipeline)
  2. osnm-z (Rust CLI) — multi-wallet, EIP-7702 sponsored, chain definitions
  3. src/ (TypeScript sniper) — chains.ts, seadrop-public.ts, rpc-blast.ts
  4. scripts/safe_mint.py (Python CLI) — reference only (already reviewed deeply)
Focus: cross-chain correctness (chainId, RPC selection, gas, EIP-1559, SeaDrop singleton per chain).
"""
import json, sqlite3, sys

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

def cap(p, n):
    t = read(p)
    return t[:n] + (f"\n...[TRUNCATED at {n} chars]" if len(t) > n else "")

sk      = read(f"{SKILL_DIR}/SKILL.md")
rpc_ref = read(f"{SKILL_DIR}/references/multichain-rpc-reference.md")
rh_pit  = cap(f"{SKILL_DIR}/references/robinhood-chain-pitfalls.md", 8000)
pipeline= read(f"{SKILL_DIR}/references/security-first-mint-pipeline.md")
chain_rs= read(f"{SKILL_DIR}/osnm-z/src/chain.rs")
multi_rs= read(f"{SKILL_DIR}/osnm-z/src/multi_wallet.rs")
spons_rs= cap(f"{SKILL_DIR}/osnm-z/src/sponsored.rs", 20000)
tx_rs   = cap(f"{SKILL_DIR}/osnm-z/src/transaction.rs", 12000)
chain_ts= read(f"{SKILL_DIR}/src/chains.ts")
seadrop_ts = read(f"{SKILL_DIR}/src/seadrop-public.ts")
rpcblast_ts= read(f"{SKILL_DIR}/src/rpc-blast.ts")

prompt = f"""You are a smart-contract / cross-chain security reviewer. Review the ENTIRE `nft-public-mint` skill package for multi-chain minting correctness and security. This package mints free NFTs across EVM chains (ethereum 1, base 8453, polygon 137, arbitrum 42161, optimism 10, bsc 56, avalanche 43114, robinhood 4663) via SeaDrop (singleton 0x00005EA00Ac477B1030CE78506496e8C2dE24bf5).

The package has 4 components:
- osnm-z: Rust CLI (multi-wallet self-funded <=10, sponsored EIP-7702 <=25, wallet gen/fund/withdraw)
- src/: TypeScript sniper (public SeaDrop, on-chain calldata, no OpenSea API, pre-sign + multi-RPC blast)
- opensea-rest/: curl+jq REST scripts
- scripts/safe_mint.py: Python single-wallet CLI (already reviewed deeply elsewhere — focus on cross-chain interaction)

=== FILE 1: SKILL.md (truncated) ===
{sk[:6000]}

=== FILE 2: references/multichain-rpc-reference.md ===
{rpc_ref}

=== FILE 3: references/robinhood-chain-pitfalls.md ===
{rh_pit}

=== FILE 4: references/security-first-mint-pipeline.md ===
{pipeline}

=== FILE 5: osnm-z/src/chain.rs (FULL — core chain definitions) ===
{chain_rs}

=== FILE 6: osnm-z/src/multi_wallet.rs ===
{multi_rs}

=== FILE 7: osnm-z/src/sponsored.rs (EIP-7702 sponsored flow) ===
{spons_rs}

=== FILE 8: osnm-z/src/transaction.rs ===
{tx_rs}

=== FILE 9: src/chains.ts (TS sniper chain table) ===
{chain_ts}

=== FILE 10: src/seadrop-public.ts ===
{seadrop_ts}

=== FILE 11: src/rpc-blast.ts ===
{rpcblast_ts}

REVIEW FOCUS (engineer-to-engineer, concise):
1. Cross-chain correctness: chainId handling, RPC routing, EIP-1559 fee fields per chain, SeaDrop module address per chain, tx type compatibility.
2. Security: wallet key handling, EIP-7702 sponsored executor safety (is the executor audited? delegation risk?), nonce management under multi-wallet blast, replay protection across chains (same nonce/same raw on different chainId?).
3. Funds safety: gas estimation, refund handling, fee policy, accidental paid mint.
4. Any chain-specific footgun (BSC 56 vs others: EIP-1559 support? Avalanche: different gas semantics? Optimism/Arbitrum L2: fee model?).
5. Real bugs only — flag with file+line, severity, and a concrete exploit scenario if any.

OUTPUT FORMAT:
- CRITICAL / HIGH / MEDIUM / LOW findings with file:line evidence.
- For each: what, why it matters, suggested fix.
- Final verdict: PROPER / NEEDS-FIX / NOT-PROPER for cross-chain production use.
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
        "max_tokens": 6000,
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
