#!/usr/bin/env python3
"""Scan OpenSea for LIVE/FREE SeaDrop collections on Robinhood chain.
Uses GraphQL only (collectionsByQuery + DropEligibilityQuery) — no RPC needed.
Output: live stages (status: pending/live/not_started) with price=0 or paid."""
import json, sys, urllib.request, urllib.parse, time

GQL = "https://gql.opensea.io/graphql"
WALLET = "0x1AfC8148CD5925732b8C5C6DF44e507D60CBa125"
HASH = "e1b54354df0d26d39c6b81429bd5e5d37749eaa4bdc027f987128f8c1e7d2308"

SEARCH = """query Search($q: String!) {
  collectionsByQuery(query: $q, limit: 50) {
    __typename
    slug
    address
    chain { identifier networkId }
  }
}"""

ELIG = """query DropEligibilityQuery($collectionSlug: String!, $address: Address!) {
  dropBySlug(slug: $collectionSlug) {
    __typename
    ... on Erc721SeaDropV1 { minterQuantityMinted(minter: $address) }
    stages {
      __typename
      stageType
      stageIndex
      isEligible
      eligibleMinterAddress
      maxTotalMintableByWallet
      eligibleMaxTotalMintableByWallet
      eligiblePrice {
        usd
        token { unit symbol contractAddress chain { identifier } }
      }
      ... on Erc1155SeaDropV2Stage {
        fromTokenId
        toTokenId
        maxTotalMintableByWalletPerToken
        eligibleMaxTotalMintableByWalletPerToken
      }
    }
  }
}"""

def gql_apq(slug):
    variables = json.dumps({"address": WALLET.lower(), "collectionSlug": slug})
    extensions = json.dumps({"persistedQuery": {"sha256Hash": HASH, "version": 1}})
    params = urllib.parse.urlencode({
        "app_id": "os2-web",
        "operationName": "DropEligibilityQuery",
        "variables": variables,
        "extensions": extensions,
    })
    url = f"{GQL}?{params}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Origin": "https://opensea.io",
        "Referer": f"https://opensea.io/collection/{slug}",
        "Cookie": "connected-account-server-hint=" + WALLET.lower(),
        "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def search(query, limit=50):
    body = json.dumps({"query": SEARCH, "variables": {"q": query}}).encode()
    req = urllib.request.Request(GQL, data=body, headers={
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36",
        "X-Api-Key": "2f6f4193d7d9496ab6cdae379b4a4a5b",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode())
    out = []
    for c in d.get("data", {}).get("collectionsByQuery") or []:
        chain = c.get("chain") or {}
        if chain.get("identifier") == "robinhood":
            out.append((c.get("slug", ""), c.get("address", "")))
    return out

def main():
    terms = sys.argv[1:] or ["robinhood", "rh", "free", "mint", "drop", "airdrop"]
    seen = {}
    for t in terms:
        try:
            for slug, addr in search(t, 50):
                if addr and addr not in seen:
                    seen[addr] = slug
        except Exception as e:
            print(f"[search {t}] ERR: {e}", file=sys.stderr)
    print(f"[scan] {len(seen)} unique robinhood collections", file=sys.stderr)
    free, live, paid = [], [], []
    for i, (addr, slug) in enumerate(sorted(seen.items())):
        try:
            d = gql_apq(slug)
            drop = d.get("data", {}).get("dropBySlug")
            if not drop:
                continue
            stages = drop.get("stages") or []
            if not stages:
                continue
            minter_qty = drop.get("minterQuantityMinted")
            for s in stages:
                ep = (s.get("eligiblePrice") or {})
                tok = ep.get("token") or {}
                pu = tok.get("unit")
                # price 0 = free
                if pu is not None and pu != "" and int(pu) == 0:
                    free.append((addr, slug, minter_qty, s.get("stageType"), s.get("stageIndex"), s.get("isEligible"), s.get("maxTotalMintableByWallet")))
                else:
                    paid.append((addr, slug, minter_qty, s.get("stageType"), s.get("stageIndex"), s.get("isEligible"), pu))
        except Exception as e:
            print(f"[{slug}] ERR: {e}", file=sys.stderr)
        if i % 10 == 9:
            time.sleep(1.0)
    print(f"\n=== FREE stages: {len(free)} ===")
    for f in free:
        print(json.dumps(f))
    print(f"\n=== PAID stages: {len(paid)} ===")
    for p in paid[:10]:
        print(json.dumps(p))
    if len(paid) > 10:
        print(f"... {len(paid) - 10} more paid")

if __name__ == "__main__":
    main()
