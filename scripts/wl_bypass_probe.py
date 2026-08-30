#!/usr/bin/env python3
"""WL Bypass Triage v2 — zero-dependency SeaDrop + custom-contract probe (Robinhood Chain default).

Usage:
  python3 wl_bypass_probe.py 0x<NFT_CONTRACT> [RPC_URL] [0x<WALLET>]

v2 changes:
  - RPC auto-verify at boot (eth_chainId == 4663); drop dead endpoints.
  - SeaDrop confidence via NFT bytecode scan (NFT-side SeaDrop selectors).
  - Custom-contract scan: detect mint/whitelist selectors in bytecode + sim each.
  - isWhitelisted()/whitelisted() view probes for WL status on custom contracts.
  - Per-RPC cooldown (NodeFlare 10.5s, Blockscout 429 backoff).
  - End-to-end mint plan output (osnm-z command).

Zero deps: pure-python keccak256 + manual ABI decode. Self-tests keccak on boot.
"""
import json, sys, time, urllib.request

# force IPv4 (tablet IPv6 blackhole — known issue, same as Hermes gateway)
import socket
_orig_gai = socket.getaddrinfo
def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_gai(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4_only

RPC_DEFAULT = "https://rpc.mainnet.chain.robinhood.com"
# Multichain registry (merged from febfrmn/nft-s evm_async.PUBLIC_RPCS + osnm-z chains.ts)
# Order = query-capable primary first, send-only sequencers LAST (blast-only).
CHAINS = {
    "ethereum":  {"id": 1,     "rpcs": ["https://ethereum-rpc.publicnode.com", "https://eth.merkle.io",
                                        "https://cloudflare-eth.com", "https://eth.llamarpc.com"]},
    "base":      {"id": 8453,  "rpcs": ["https://mainnet.base.org", "https://base-rpc.publicnode.com",
                                        "https://mainnet-sequencer.base.org"]},  # last = send-only blast
    "polygon":   {"id": 137,   "rpcs": ["https://polygon-rpc.com", "https://polygon-bor-rpc.publicnode.com"]},
    "arbitrum":  {"id": 42161, "rpcs": ["https://arb1.arbitrum.io/rpc"]},
    "optimism":  {"id": 10,    "rpcs": ["https://mainnet.optimism.io"]},
    "bsc":       {"id": 56,    "rpcs": ["https://bsc-dataseed.binance.org"]},
    "avalanche": {"id": 43114, "rpcs": ["https://api.avax.network/ext/bc/C/rpc"]},
    "robinhood": {"id": 4663,  "rpcs": ["https://rpc.mainnet.chain.robinhood.com",  # official — VPS OK, tablet TLS-blocked
                                        "https://rpc.nodeflare.app/robinhood/public",  # tablet OK, 1 req/10s
                                        "https://robinhood.drpc.org",  # limited methods
                                        "https://robinhoodchain.blockscout.com/api/eth-rpc"]},  # 429-prone
}
RPC_LIST = list(CHAINS["robinhood"]["rpcs"])
MODULE_DEFAULT = "0x00005EA00Ac477B1030CE78506496e8C2dE24bf5"  # SeaDrop singleton — same on all EVM chains
WALLET_DEFAULT = "0x1AfC8148CD5925732b8C5C6DF44e507D60CBa125"
CHAIN_ID = 4663

# ---------------- pure keccak256 ----------------
RC = [0x0000000000000001,0x0000000000008082,0x800000000000808A,0x8000000080008000,
      0x000000000000808B,0x0000000080000001,0x8000000080008081,0x8000000000008009,
      0x000000000000008A,0x0000000000000088,0x0000000080008009,0x000000008000000A,
      0x000000008000808B,0x800000000000008B,0x8000000000008089,0x8000000000008003,
      0x8000000000008002,0x8000000000000080,0x000000000000800A,0x800000008000000A,
      0x8000000080008081,0x8000000000008080,0x0000000080000001,0x8000000080008008]
ROT = [[0,36,3,41,18],[1,44,10,45,2],[62,6,43,15,61],[28,55,25,21,56],[27,20,39,8,14]]

def _rol(v, n): return ((v << n) & 0xFFFFFFFFFFFFFFFF) | (v >> (64 - n)) if n else v

def _keccak_f(st):
    for rc in RC:
        C = [st[x] ^ st[x+5] ^ st[x+10] ^ st[x+15] ^ st[x+20] for x in range(5)]
        D = [C[(x-1) % 5] ^ _rol(C[(x+1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                st[x + 5*y] ^= D[x]
        B = [0] * 25
        for x in range(5):
            for y in range(5):
                B[y + 5 * ((2*x + 3*y) % 5)] = _rol(st[x + 5*y], ROT[x][y])
        for x in range(5):
            for y in range(5):
                st[x + 5*y] = B[x + 5*y] ^ ((~B[(x+1) % 5 + 5*y]) & B[(x+2) % 5 + 5*y])
        st[0] ^= rc

def keccak256(data):
    rate = 136
    msg = bytearray(data); msg.append(0x01)
    while len(msg) % rate != rate - 1: msg.append(0x00)
    msg.append(0x80)
    st = [0] * 25
    for off in range(0, len(msg), rate):
        blk = msg[off:off+rate]
        for i in range(rate // 8):
            st[i] ^= int.from_bytes(blk[i*8:(i+1)*8], 'little')
        _keccak_f(st)
    return b''.join(x.to_bytes(8, 'little') for x in st[:4])

assert keccak256(b'').hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470", "keccak empty FAIL"
assert keccak256(b"transfer(address,uint256)").hex()[:8] == "a9059cbb", "keccak selector FAIL"

def sel(sig): return "0x" + keccak256(sig.encode()).hex()[:8]
def selb(sig): return keccak256(sig.encode()).hex()[:8]  # 8 hex chars, no 0x
def addr(a): return a[2:].lower().rjust(64, "0")

# ---------------- manual ABI decode ----------------
def u(data, off, bits): return int.from_bytes(data[off:off + bits//8], 'big')
def b_off(data, off): return data[off + 31] != 0
def a_addr(data, off): return "0x" + data[off+12:off+32].hex()

def dec_static(data, types):
    out = []; off = 0
    for t in types:
        if t == 'bool': out.append(b_off(data, off)); off += 32
        elif t == 'address': out.append(a_addr(data, off)); off += 32
        elif t.startswith('uint'):
            out.append(u(data, off, int(t[4:]))); off += 32
        else: raise ValueError(f"unsupported static type {t}")
    return out

def dec_addr_array(data):
    off = u(data, 0, 256)
    ln = u(data, off, 256)
    return [a_addr(data, off + 32 + i*32) for i in range(ln)]

# ---------------- RPC with auto-verify + cooldown ----------------
UA = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
COOLDOWN = {   # seconds to sleep before next call when this RPC is active
    "nodeflare": 10.5,
    "blockscout": 5.0,
}
_cur = 0
_work = []   # verified working URLs (chainId==4663)

def _post(url, body, timeout=20):
    req = urllib.request.Request(url, data=body, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def _tag(url):
    if "nodeflare" in url: return "nodeflare"
    if "blockscout" in url: return "blockscout"
    if "drpc" in url: return "drpc"
    return "other"

def rpc(method, params, _id=1, allow_verify_fail=False):
    """Call across verified RPCs. If no verified list yet, verify boot candidates."""
    global _cur
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": _id}).encode()
    urls = _work or RPC_LIST
    last = None
    for _round in range(2):
        for url in urls:
            tag = _tag(url)
            if tag == "nodeflare":
                time.sleep(COOLDOWN["nodeflare"])
            if tag == "blockscout" and _round > 0:
                time.sleep(COOLDOWN["blockscout"])
            try:
                resp = _post(url, body)
                if isinstance(resp, dict) and "error" in resp:
                    err = resp["error"]
                    msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    last = f"rate_limited({url})" if "rate" in msg.lower() else f"rpc_err({url}:{msg})"
                    if "rate" in msg.lower():
                        time.sleep(2)
                        continue
                    continue
                return resp["result"]
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(1)
    raise RuntimeError(f"All RPCs failed: {last}")

def verify_rpcs():
    """Probe eth_chainId on each candidate; keep those matching CHAIN_ID."""
    global _work
    ok = []
    for url in RPC_LIST:
        try:
            body = json.dumps({"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1}).encode()
            res = _post(url, body, timeout=10)
            cid = int(res.get("result", "0x0"), 16)
            if cid == CHAIN_ID:
                ok.append(url)
                print(f"  ✓ RPC {_tag(url)} chainId={cid}")
            else:
                print(f"  ✗ RPC {_tag(url)} wrong chain {cid}")
        except Exception as e:
            print(f"  ✗ RPC {_tag(url)} unreachable: {type(e).__name__}")
    _work = ok
    if not _work:
        print("!! No working RPC — aborting")
        sys.exit(1)

def eth_call(to, data, block="latest", _from=None):
    tx = {"to": to, "data": data}
    if _from: tx["from"] = _from
    try:
        return rpc("eth_call", [tx, block])
    except RuntimeError as e:
        return f"REVERT: {e}"

def eth_get_code(to):
    try:
        return rpc("eth_getCode", [to, "latest"])
    except RuntimeError as e:
        return f"REVERT: {e}"

# ---------------- main ----------------
args = [a for a in sys.argv[1:] if a]
chain = "robinhood"
rest = []
_i = 0
while _i < len(args):
    if args[_i] == "--chain" and _i + 1 < len(args):
        chain = args[_i + 1].lower()
        _i += 2
    else:
        rest.append(args[_i]); _i += 1

if chain not in CHAINS:
    print(f"!! Unknown chain '{chain}' — supported: {', '.join(sorted(CHAINS))}")
    sys.exit(1)
CHAIN_ID = CHAINS[chain]["id"]
if not (rest and len(rest) > 1):
    RPC_LIST[:] = list(CHAINS[chain]["rpcs"])  # registry list unless explicit RPC override

CONTRACT = rest[0] if len(rest) > 0 else None
WALLET = rest[2] if len(rest) > 2 else WALLET_DEFAULT
if rest and len(rest) > 1:
    RPC_LIST.insert(0, rest[1])  # explicit RPC override first
MODULE = MODULE_DEFAULT

if not CONTRACT:
    print("Usage: wl_bypass_probe.py 0x<NFT_CONTRACT> [--chain ethereum|base|polygon|arbitrum|optimism|bsc|avalanche|robinhood] [RPC_URL] [0x<WALLET>]")
    sys.exit(1)

print(f"[*] Verifying RPC endpoints (chain {CHAIN_ID})...")
verify_rpcs()

def probe(sig, args_hex="", to=None, static_types=None, arr_types=None, _from=None):
    to = to or MODULE
    res = eth_call(to, sel(sig) + args_hex, _from=_from)
    if res.startswith("REVERT"): return ("REVERT", res)
    raw = bytes.fromhex(res[2:])
    if arr_types: return ("OK", dec_addr_array(raw))
    if static_types: return ("OK", dec_static(raw, static_types))
    return ("OK", res)

blk = rpc("eth_getBlockByNumber", ["latest", False])
now = int(blk["timestamp"], 16)
print(f"[*] Contract: {CONTRACT}")
print(f"[*] Wallet:   {WALLET}")
print(f"[*] now: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(now))}")

# --- [0] bytecode + SeaDrop confidence ---
code = eth_get_code(CONTRACT)
code_is_revert = isinstance(code, str) and code.startswith("REVERT")
nft_seadrop_sels = ["setSeaDrop(address)", "updatePublicDrop(address,(uint80,uint48,uint48,uint16,uint16,bool))",
                    "updateAllowListMerkleRoot(address,bytes32)", "mintSeaDrop(address,uint256)", "seaDrop()"]
found_sd = [s for s in nft_seadrop_sels if not code_is_revert and selb(s) in code.lower()]
if code_is_revert:
    print(f"[BYTECODE] eth_getCode revert — {code[:80]}")
else:
    print(f"[BYTECODE] len={len(code)//2} bytes; NFT-side SeaDrop selectors found: {len(found_sd)}")
    for s in found_sd: print(f"    + {s}")

SEADROP_NFT = bool(found_sd)
if not SEADROP_NFT:
    print("  → KONTRAK CUSTOM (bukan SeaDrop) — module reads below = low confidence; custom scan aktif")

# --- [1] public drop (module) ---
st_pub, pub = probe("getPublicDrop(address)", args_hex=addr(CONTRACT),
                    static_types=['uint80','uint48','uint48','uint16','uint16','bool'])
if st_pub == "OK":
    mp, st, et, mx, fb, rf = pub
    print(f"[PUBLIC DROP] price={mp} wei ({mp/1e18:.8f} ETH) start={st} end={et} max/wallet={mx} feeBps={fb}")
else:
    print(f"[PUBLIC DROP] {pub}"); mp = st = et = mx = None

# --- [2] allowlist (module) ---
st_al, al = probe("getAllowListMerkleRoot(address)", args_hex=addr(CONTRACT), static_types=['uint256'])
root = al[0].to_bytes(32, 'big').hex() if st_al == "OK" else None
print(f"[ALLOWLIST] merkleRoot=0x{root if root else '?'}" + ("" if st_al == "OK" else f" ({al})"))

# --- [3] token gated (module) ---
st_tg, tg = probe("getTokenGatedAllowedTokens(address)", args_hex=addr(CONTRACT), arr_types=True)
print(f"[TOKEN-GATED] {tg if st_tg == 'OK' else tg}")

# --- [4] signers (module) ---
st_sg, sg = probe("getSigners(address)", args_hex=addr(CONTRACT), arr_types=True)
print(f"[SIGNED] signers={sg if st_sg == 'OK' else sg}")

# --- [5] active stage (module) ---
st_as, as_ = probe("getActiveStage(address)", args_hex=addr(CONTRACT))
print(f"[ACTIVE STAGE] {as_}")

# --- [6] mint stats (module) ---
st_ms, ms = probe("getMintStats(address,address)", args_hex=addr(CONTRACT) + addr(WALLET),
                  static_types=['uint256','uint256'])
if st_ms == "OK":
    print(f"[MINT STATS] totalMinted={ms[0]} walletMinted={ms[1]}")

# --- [7] SeaDrop sims ---
sim_pub = eth_call(MODULE, sel("mintPublic(address,address,address,uint256)") + addr(CONTRACT) + addr(WALLET) + "0"*64 + "0"*63 + "1", _from=WALLET)
print(f"[SIM mintPublic 0ETH x1] {sim_pub[:160]}")
if st_al == "OK" and root and root != "0"*64:
    calldata = sel("mintAllowList(address,address,uint256,bytes32[])") + addr(CONTRACT) + addr(WALLET) + "0"*63 + "1" + "0"*63 + "20" + "0"*63 + "0"
    sim_al = eth_call(MODULE, calldata, _from=WALLET)
    print(f"[SIM mintAllowList empty-proof x1] {sim_al[:160]}")

# --- [8] custom contract scan ---
CUSTOM_MINT_SIGS = [
    ("mint(uint256)", "1"), ("mint()", "0"), ("mintPublic(uint256)", "1"),
    ("mintPublic()", "0"), ("whitelistMint(uint256)", "1"), ("whitelistMint(address,uint256)", "2"),
    ("freeMint()", "0"), ("freeMint(uint256)", "1"), ("claim(uint256)", "1"), ("claim()", "0"),
    ("mintNFT(uint256)", "1"), ("publicMint(uint256)", "1"), ("mintToken(uint256)", "1"),
    ("buy(uint256)", "1"), ("allowlistMint(uint256)", "1"), ("whitelistedMint(uint256)", "1"),
    ("saleMint(uint256)", "1"), ("mintFree()", "0"), ("mintFree(uint256)", "1"),
]
CUSTOM_VIEW_SIGS = ["isWhitelisted(address)", "whitelisted(address)", "isAllowlisted(address)",
                    "onAllowlist(address)", "whitelistEnabled()", "publicMintEnabled()"]

custom_hits = []
custom_views = {}
if not SEADROP_NFT and not code_is_revert:
    cl = code.lower()
    print("\n[CUSTOM CONTRACT SCAN]")
    for sig, nargs in CUSTOM_MINT_SIGS:
        if selb(sig) in cl:
            if nargs == "1":
                cd = sel(sig) + "0"*63 + "1"
            elif nargs == "2":
                cd = sel(sig) + addr(WALLET) + "0"*63 + "1"
            else:
                cd = sel(sig)
            res = eth_call(CONTRACT, cd, _from=WALLET)
            reason = res[:150]
            custom_hits.append((sig, reason))
            print(f"  • {sig} → {reason}")
    for sig in CUSTOM_VIEW_SIGS:
        if selb(sig) in cl:
            res = eth_call(CONTRACT, sel(sig) + addr(WALLET))
            if res.startswith("REVERT"):
                custom_views[sig] = f"revert {res[:60]}"
            else:
                raw = bytes.fromhex(res[2:])
                custom_views[sig] = "TRUE" if b_off(raw, 0) else "FALSE"
            print(f"  • {sig} → {custom_views[sig]}")

# --- VERDICT ---
print("\n" + "=" * 60)
print("VERDICT / BYPASS PATH")
print("=" * 60)

public_active = st_pub == "OK" and st is not None and st != 0 and st <= now and (et == 0 or et >= now)
public_future = st_pub == "OK" and st is not None and st != 0 and st > now
public_unset = st_pub == "OK" and st is not None and st == 0
allowlist_dead = st_al == "OK" and root == "0"*64
allowlist_live = st_al == "OK" and root != "0"*64
signed_live = st_sg == "OK" and len(sg) > 0
token_live = st_tg == "OK" and len(tg) > 0

if SEADROP_NFT:
    if public_active:
        if mp == 0:
            print(f" • ✅ PUBLIC FREE MINT OPEN — langsung mintPublic 0 ETH" + (f", max {mx}/wallet" if mx else ""))
        else:
            print(f" • 💰 PUBLIC MINT OPEN — harga {mp/1e18:.8f} ETH (bukan free)")
    if public_future:
        print(f" • ⏳ PUBLIC drop belum buka — buka ts {st} ({time.strftime('%H:%M:%S UTC', time.gmtime(st))}). Snipe pas buka.")
    if public_unset:
        print(" • ⚪ PUBLIC drop belum di-set (start=0) — pantau kontrak.")
    if st_pub != "OK":
        print(" • ❓ getPublicDrop revert — module state tidak terbaca.")
    if allowlist_dead:
        print(" • 🔓 ALLOWLIST MATI (root=0x0) — WL gate tidak ada.")
    elif allowlist_live:
        print(" • 🔒 ALLOWLIST LIVE — butuh proof valid dari leaf address yang di-WL.")
        print("    Bypass path: (a) cari root+leaks (blockscout/github/twitter 'merkle' <collection>),")
        print("    (b) generate proof kalau wallet 0x1Afc... ada di tree,")
        print("    (c) daftar wallet ke WL official (form/Discord), (d) tunggu public stage.")
    if signed_live:
        print(" • ✍️ SIGNED MINT LIVE — butuh signature server; cek replay/forge + nonce.")
    if token_live:
        print(f" • 🎫 TOKEN-GATED — butuh hold token {tg[0] if tg else '?'}; cek saldo wallet.")
    if not (public_active or public_future or allowlist_live or signed_live or token_live):
        print(" • 🚫 Semua gate mati/belum aktif — drop kemungkinan belum di-set; pantau kontrak.")
else:
    # custom contract verdict from bytecode scan
    if code_is_revert:
        print(" • ⛔ eth_getCode gagal — kontrak tidak bisa dianalisa dari jaringan ini.")
    wl_true = [s for s, v in custom_views.items() if v == "TRUE"]
    wl_false = [s for s, v in custom_views.items() if v == "FALSE"]
    if wl_true:
        print(f" • ✅ WALLET KAMU KEBACA WL ({', '.join(wl_true)}) — langsung mint via selector yang cocok.")
    elif wl_false:
        print(f" • ❌ Wallet KEBACA BUKAN WL ({', '.join(wl_false)}) — cek method WL lain / daftar dulu.")
    if custom_hits:
        ok_hits = [(s, r) for s, r in custom_hits if not r.startswith("REVERT")]
        for s, r in ok_hits:
            print(f" • 🟢 MINT COKOK (sim sukses): {s} → {r[:100]}")
        if not ok_hits:
            print(" • 🟡 Selector mint ada tapi semua sim revert:")
            for s, r in custom_hits[:6]:
                print(f"      {s} → {r[:90]}")
    else:
        print(" • 🚫 Gak ada selector mint umum di bytecode — butuh analisa manual (decompile).")

print("\nNext: (1) PUBLIC FREE → osnm-z langsung. (2) WL LIVE + eligible → osnm-z (PK di .env). (3) WL LIVE + belum eligible → daftar / cari leaked proof. (4) CUSTOM → pakai selector yang cocok / decompile.")
print(f"Mint cmd: cd ~/nft-mint && mint.sh mint --chain {chain}   (RPC di .env; buat blast timing-critical pakai sequencer dari VPS; RPC list multichain: references/multichain-rpc-reference.md)")
