#!/usr/bin/env python3
"""SeaDrop drop triage probe — dump semua jalur mint config untuk koleksi mana pun.

Usage:
  python3 seadrop_probe.py 0x<NFT_CONTRACT> [0x<SEADROP_MODULE>] [0x<WALLET>] [RPC_URL]

Defaults (Robinhood Chain):
  MODULE = 0x00005EA00Ac477B1030CE78506496e8C2dE24bf5
  WALLET = 0x1AfC8148CD5925732b8C5C6DF44e507D60CBa125 (opsional untuk signed/mintstats)
  RPC    = https://rpc.mainnet.chain.robinhood.com

Deps: eth-hash, eth-abi (pip install eth-hash eth-abi)
"""
import json, sys, time, urllib.request
from eth_hash.auto import keccak
from eth_abi import decode

RPC_DEFAULT = "https://rpc.mainnet.chain.robinhood.com"
MODULE_DEFAULT = "0x00005EA00Ac477B1030CE78506496e8C2dE24bf5"
WALLET_DEFAULT = "0x1AfC8148CD5925732b8C5C6DF44e507D60CBa125"

args = [a for a in sys.argv[1:] if a]
CONTRACT = args[0] if len(args) > 0 else None
MODULE = args[1] if len(args) > 1 else MODULE_DEFAULT
WALLET = args[2] if len(args) > 2 else WALLET_DEFAULT
RPC = args[3] if len(args) > 3 else RPC_DEFAULT

if not CONTRACT:
    print("Usage: seadrop_probe.py 0x<NFT_CONTRACT> [0x<MODULE>] [0x<WALLET>] [RPC]")
    sys.exit(1)

UA = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}

def rpc(method, params, _id=1):
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": _id}).encode()
    req = urllib.request.Request(RPC, data=body, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read())
    if "error" in resp:
        raise RuntimeError(f"RPC error: {resp['error']}")
    return resp["result"]

def eth_call(to, data, block="latest", _from=None):
    tx = {"to": to, "data": data}
    if _from:
        tx["from"] = _from
    try:
        return rpc("eth_call", [tx, block])
    except RuntimeError as e:
        return f"REVERT: {e}"

def sel(sig): return "0x" + keccak(sig.encode()).hex()[:8]
def addr(a): return a[2:].lower().rjust(64, "0")

def show(label, sig, to=None, args_hex="", decode_types=None, transform=None):
    to = to or MODULE
    res = eth_call(to, sel(sig) + args_hex)
    if res.startswith("REVERT"):
        print(f"  {label}: {res[:120]}")
        return None
    print(f"  {label}: {res[:120]}")
    if decode_types and not res.startswith("REVERT"):
        try:
            vals = decode(decode_types, bytes.fromhex(res[2:]))
            if transform:
                vals = transform(vals)
            print(f"    → {vals}")
            return vals
        except Exception as e:
            print(f"    decode err: {e}")
    return res

try:
    blk = rpc("eth_getBlockByNumber", ["latest", False])
    now = int(blk["timestamp"], 16)
    print(f"[*] now: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(now))} (block {int(blk['number'],16)})")
    print(f"[*] NFT contract: {CONTRACT}")
    print(f"[*] SeaDrop module: {MODULE}")
    print(f"[*] wallet: {WALLET}\n")

    print("[1] NFT contract state:")
    show("totalSupply", "totalSupply()", to=CONTRACT)
    show("maxSupply", "maxSupply()", to=CONTRACT)
    show("owner", "owner()", to=CONTRACT)
    show("name", "name()", to=CONTRACT)
    show("symbol", "symbol()", to=CONTRACT)

    print("\n[2] Public drop (jalur public mint):")
    pub = show("getPublicDrop", "getPublicDrop(address)", args_hex=addr(CONTRACT))
    if pub and not pub.startswith("REVERT"):
        try:
            mp, st, et, mx, fb, rf = decode(['uint80','uint48','uint48','uint16','uint16','bool'], bytes.fromhex(pub[2:]))
            print(f"    mintPrice: {mp} wei = {mp/1e18:.6f} ETH")
            print(f"    startTime: {st} = {time.strftime('%H:%M:%S UTC', time.gmtime(st))}")
            print(f"    endTime:   {et} = {time.strftime('%H:%M:%S UTC', time.gmtime(et))}")
            print(f"    maxTotalMintableByWallet: {mx}")
            print(f"    feeBps: {fb} ({fb/100}%)")
            print(f"    restrictFeeRecipients: {rf}")
        except Exception as e:
            print(f"    decode err: {e}")

    print("\n[3] Allowlist:")
    show("getAllowListMerkleRoot", "getAllowListMerkleRoot(address)", args_hex=addr(CONTRACT))

    print("\n[4] Token-gated:")
    tg = show("getTokenGatedAllowedTokens", "getTokenGatedAllowedTokens(address)", args_hex=addr(CONTRACT))
    if tg and not tg.startswith("REVERT"):
        try:
            toks = decode(['address[]'], bytes.fromhex(tg[2:]))[0]
            print(f"    allowedTokens ({len(toks)}): {[t.lower() for t in toks]}")
        except Exception as e:
            print(f"    decode err: {e}")

    print("\n[5] Signed mint:")
    show("getSigners", "getSigners(address)", args_hex=addr(CONTRACT))
    show("getSignedMintValidationParams", "getSignedMintValidationParams(address,address)",
         args_hex=addr(CONTRACT) + addr(WALLET))

    print("\n[6] Stages & fee:")
    show("getActiveStage", "getActiveStage(address)", args_hex=addr(CONTRACT))
    show("getAllowedFeeRecipients", "getAllowedFeeRecipients(address)", args_hex=addr(CONTRACT))

    print("\n[7] Mint stats for wallet:")
    show("getMintStats(address,address)", "getMintStats(address,address)",
         args_hex=addr(CONTRACT) + addr(WALLET))

    print("\n[*] Simulasi mintPublic (0 ETH value, cek revert reason):")
    # mintPublic(address nftContract, address feeRecipient, address minterIfNotPayer, uint256 quantity)
    calldata = sel("mintPublic(address,address,address,uint256)") + addr(CONTRACT) + addr(WALLET) + "0"*64 + "0"*63 + "1"
    res = eth_call(MODULE, calldata, _from=WALLET)
    print(f"  mintPublic: {res[:200]}")
except Exception as e:
    print(f"FATAL: {e}")
