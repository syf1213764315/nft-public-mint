# OpenSea Studio Drop Triage — Kontrak Bukan SeaDrop

Sebagian drop OpenSea baru (OpenSea Studio, mis. Pixel Monkey Ink Aug 2026)
BUKAN SeaDrop module klasik. Kalau probe SeaDrop (`getSeaDrop()`,
`publicDrop()`, `merkleRoot()`, `allowedTokens()`) semua REVERT, jangan
simpulkan "contract rusak" — itu tanda kontrak beda TYPE. Classify dulu.

## Step 0: Klasifikasi tipe kontrak (WAJIB sebelum probe)

```bash
# 1. eth_getCode — kalau panjang code = 92 (0x5c) itu minimal proxy
curl -s -X POST <RPC> -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_getCode","params":["0x<NFT>","latest"],"id":1}'
```

Proxy 92-byte OpenSea Studio Drop:
```
0x363d3d373d3d3d363d73<20-byte IMPLEMENTATION>5af43d82803e903d91602b57fd5bf3
```
Decode: byte offset 10 (setelah `363d3d373d3d3d363d73`) sampai byte 30 =
address implementation. Contoh nyata (PMI Ink): `0x09a26fc8fcef18192e267d7a6da9dfb4be81dd6a5`.

Bukan ERC-1967 (slot implementation = 0x0) — minimal proxy, bukan upgradeable proxy.

## Ciri-ciri OpenSea Studio Drop

View function yang **BERHASIL** di clone:
- `owner()` — collection owner
- `totalSupply()` / `maxSupply()` — state supply
- `name()` / `symbol()`
- `getMintStats(address)` — (mintedByWallet, ...)

View function yang **REVERT** (tidak ada di implementasi ini):
- `getSeaDrop()`, `seaDrop()`, `dropModule()` — bukan SeaDrop
- `publicDrop()`, `merkleRoot()`, `allowedTokens()` — config bukan via selector ini

Setup function factory: `multiConfigure(...)` — satu panggilan berisi semua
config drop (merkleRoot, stages, allowedTokens, fee recipients, dll).
Selector `0x911f456b`. Kalau lihat tx setup ke factory OpenSea
(`0x008EbCCaE39d001200c3003c3225ce0A00690066` di Ink; cek per-chain)
dengan method `createClone` + `multiConfigure` → ini Studio Drop.

## PITFALL TERBESAR: Eligibility cross-chain dicek BACKEND OpenSea

Stage allowlist "Holders <Collection X>" dengan koleksi X di chain BEDA
(contoh nyata: hold **Pixel Monkey Hood** di Robinhood Chain 4663 → free mint
**Pixel Monkey Ink** di Ink Chain 57073):

- Eligibility TIDAK bisa diverifikasi via `eth_call` — OpenSea backend yang
  cek holding di chain lain, lalu serve mint ke frontend.
- Merkle root bisa saja 0x0 di contract karena proof/signature di-generate
  backend saat user connect wallet di halaman drop.
- Satu-satunya verifikasi: user connect wallet di halaman OpenSea drop →
  klik "View eligibility" → status eligible/not.

Jadi workflow allowlist cross-chain:
1. Baca syarat stage di halaman drop (label "Holders X", FREE, limit/wallet).
2. Cek saldo wallet di CHAIN ASAL token gate (beli cukup gak?) DAN chain
   tujuan mint (gas cukup gak?).
3. Kalau belum hold token gate → beli floor di chain asal (OpenSea listing).
4. Verifikasi eligibility via UI OpenSea (butuh user connect wallet, manual).
5. Mint via halaman drop / osnm-z.

## Cek saldo wallet multi-chain (satu script)

```python
def bal(addr, rpc_url):
    body = json.dumps({'jsonrpc':'2.0','method':'eth_getBalance',
        'params':[addr,'latest'],'id':1}).encode()
    req = urllib.request.Request(rpc_url, data=body,
        headers={'User-Agent':'Mozilla/5.0','Content-Type':'application/json'})
    return int(json.loads(urllib.request.urlopen(req, timeout=20).read())['result'], 16)/1e18
```

RPC publik yang work (GCP VPS, Aug 2026):
- Ink: `https://ink.drpc.org` (flaky, retry) atau `https://ink.gateway.tenderly.co` (lebih stabil)
- Robinhood: `https://rpc.mainnet.chain.roborhood.com` → TYPO! bener: `https://rpc.mainnet.chain.robinhood.com`
- ETH mainnet: `https://1rpc.io/eth` / `https://ethereum-rpc.publicnode.com` (llamarpc 521, ankr error — jangan dipakai)

## PITFALL: Relay.link bridge mahal untuk amount kecil

Relay.link (`https://api.relay.link/quote`) support RH chain (4663) & Ink
(57073) dengan ETH bridging, TAPI fee relayer berbasis persen dengan minimum
efektif yang besar: bridge 0.001 INK (~$2.44) fee ~$6.37; bridge 0.005 INK
(~$12.20) fee ~$31.76. Artinya bridge sub-$1 TIDAK VIABLE — jangan buang
waktu nge-quote untuk isi gas kecil. Kalau user punya dana di chain salah,
minta top-up native (exchange/app → chain tujuan) daripada bridge.

## Contoh nyata (Pixel Monkey Ink, 23 Aug 2026)

- PMI: Ink chain, contract 0x310Fe780D268AC86e1364B2B3a65D816dC158b18 (proxy → impl 0x09a26fc8...)
- Syarat allowlist: hold PMH (0x3b9f49db8730518FDBf3307C90aC7679999738fE, RH chain) — FREE, limit 2/wallet
- Public stage: 1 ETH (mahal, skip)
- PMH floor $0.45 (0.0002 ETH), gas RH ~0.02 gwei (Seaport buy ≈ 0.000005 ETH)
- Wallet user: RH 0.000059 ETH (gak cukup beli floor) → blocker funding, bukan teknis mint
- Kunci: totalSupply berjalan (90 → 346 → 361 dalam menit) — drop populer, jangan tunda
