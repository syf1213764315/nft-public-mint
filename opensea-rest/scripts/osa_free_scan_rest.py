#!/usr/bin/env python3
"""
osa_free_scan_rest.py — Free-mint drop discovery via the OFFICIAL OpenSea REST API.

Differs from the GraphQL-based osa_free_scan.py: this one uses the keyed
/api/v2/drops endpoints (instant free key, read rate limit 600/h), so it is
stable and needs no evolving GraphQL APQ hashes.

Usage:
  osa_free_scan_rest.py [--type featured|upcoming|recently_minted|all]
                        [--chains ethereum,base,robinhood]
                        [--limit 50] [--details]

  --type      which drop bucket(s) to scan. Default: all
  --chains    comma-separated chain filter (default: no filter)
  --limit     max drops to consider per bucket (default 50)
  --details   also fetch /api/v2/drops/{slug} for each candidate (full stages
              + total supply). Slower but authoritative.

Key resolution (first match wins):
  1. $OPENSEA_API_KEY
  2. $HOME/.opensea/api_key   (cached instant key)
  3. POST /api/v2/auth/keys   (fetch + cache fresh instant key)

Output: tab-separated lines, one per free candidate:
  STAGE_TYPE  CHAIN  COLLECTION_SLUG  CONTRACT  PRICE_ETH  LABEL  START  END  FREE?

FREE classification:
  price == "0"  -> genuinely FREE. If stage_type == public_sale it mints with
                   no allowlist (best). signed_presale / merkle_presale at price
                   0 still need an allowlist signature / merkle proof to mint.
                   active = mintable NOW; next = scheduled later.
"""

import argparse
import json
import os
import sys
import time
import urllib.request

BASE = "https://api.opensea.io"
KEY_FILE = os.path.expanduser("~/.opensea/api_key")
UA = "opensea-skill/1.0"


def resolve_key():
    env = os.environ.get("OPENSEA_API_KEY")
    if env:
        return env
    if os.path.isfile(KEY_FILE):
        with open(KEY_FILE) as f:
            k = f.read().strip()
        if k:
            return k
    # fetch fresh instant key
    req = urllib.request.Request(
        f"{BASE}/api/v2/auth/keys",
        data=b"{}",
        headers={"User-Agent": UA, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    key = d.get("api_key", "")
    if key:
        try:
            os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
            with open(KEY_FILE, "w") as f:
                f.write(key + "\n")
            os.chmod(KEY_FILE, 0o600)
        except OSError:
            pass
    return key


def api_get(path, key):
    req = urllib.request.Request(
        f"{BASE}{path}",
        headers={"x-api-key": key, "User-Agent": UA},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(2 * (2 ** attempt))
                continue
            print(f"# API error {e.code} on {path}", file=sys.stderr)
            return None
    return None


def wei_to_eth(w):
    try:
        return int(w) / 1e18
    except (TypeError, ValueError):
        return None


def stage_info(stage):
    if not stage:
        return None
    return {
        "stage_type": stage.get("stage_type", "?"),
        "price_eth": wei_to_eth(stage.get("price")),
        "price_raw": stage.get("price"),
        "label": stage.get("label", ""),
        "start": stage.get("start_time", ""),
        "end": stage.get("end_time", ""),
        "max_per_wallet": stage.get("max_per_wallet"),
        "allowlist_count": stage.get("allowlist_wallet_count"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", default="all",
                    choices=["all", "featured", "upcoming", "recently_minted"])
    ap.add_argument("--chains", default=None)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--details", action="store_true")
    args = ap.parse_args()

    key = resolve_key()
    if not key:
        print("# FATAL: could not resolve an OpenSea API key", file=sys.stderr)
        sys.exit(1)

    types = (["featured", "upcoming", "recently_minted"]
             if args.type == "all" else [args.type])

    candidates = []  # (drop, stage_key, stage)
    for t in types:
        cursor = None
        seen = 0
        while seen < args.limit:
            q = f"type={t}&limit=20"
            if args.chains:
                q += f"&chains={args.chains}"
            if cursor:
                q += f"&cursor={cursor}"
            data = api_get(f"/api/v2/drops?{q}", key)
            if not data or "drops" not in data:
                break
            for dr in data["drops"]:
                seen += 1
                for stage_key in ("active_stage", "next_stage"):
                    st = stage_info(dr.get(stage_key))
                    if st is None:
                        continue
                    candidates.append((dr, stage_key, st))
            cursor = data.get("next")
            if not cursor:
                break

    print("# SCAN source=opensea-rest-api type=%s chains=%s"
          % (args.type, args.chains or "all"))
    print("# candidates_considered=%d" % len(candidates))
    print("# STAGE_TYPE\tCHAIN\tSLUG\tCONTRACT\tPRICE_ETH\tLABEL\tSTART\tEND\tFREE")

    free_count = 0
    for dr, stage_key, st in candidates:
        price = st["price_eth"]
        is_free = st["price_raw"] in ("0", None)
        if not is_free:
            continue
        free_count += 1
        slug = dr.get("collection_slug")
        print("\t".join([
            st["stage_type"],
            dr.get("chain", "?"),
            slug,
            dr.get("contract_address", ""),
            "0" if price is None else str(price),
            st["label"].replace("\t", " "),
            st["start"],
            st["end"],
            "PUBLIC-FREE" if st["stage_type"] == "public_sale" else "NEEDS-ALLOWLIST",
        ]))
        if args.details and slug:
            det = api_get(f"/api/v2/drops/{slug}", key)
            if det and "stages" in det:
                print(f"# details {slug}: total_supply={det.get('total_supply')} "
                      f"stages={len(det['stages'])}"
                      f" minting_chain={det.get('chain')}")

    print(f"# free_candidates={free_count}")


if __name__ == "__main__":
    main()
