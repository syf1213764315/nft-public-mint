# Security-First Mint Pipeline

## Sumber: `Robinhood-nft-sniper` (gowthamaran, MIT)

Pola keamanan yang di-port dari repo [Robinhood-nft-sniper](https://github.com/gowthamaran/Robinhood-nft-sniper) ke skill `nft-public-mint`.

## 10 Kekuatan Repo Yang Diadopsi

| # | Strength | Status di Skill Kita |
|---|----------|---------------------|
| 1 | **Encrypted Web3 keystore** (mode 600) — gak ada plaintext pk di disk | ✅ `scripts/safe_mint.py keystore create` |
| 2 | **Chain ID validation** sebelum setiap action | ✅ built-in di `safe_mint.py` |
| 3 | **Mandatory eth_call simulation** sebelum sign | ✅ `safe_mint.py check` + `watch` mode |
| 4 | **eth_estimateGas + 15% buffer** | ✅ `watch` mode engine |
| 5 | **Hard spending limits** (max price/NFT, max fee, max total, balance buffer) | ✅ `safe_mint.py` enforce_limits() |
| 6 | **Duplicate guard** (SQLite state — no accidental re-mint) | ✅ `watch` mode state_db |
| 7 | **Watch-then-fire** (warm poll eth_call → parallel refresh → sign → broadcast) | ✅ `watch` mode engine |
| 8 | **On-chain free-mint verification** (getPublicDrop().price == 0) | ✅ `probe_public_drop_price()` |
| 9 | **OpenSea Drops API validation** (chain, value==0, domain) | ✅ `os-check` command |
| 10 | **Broadcast same raw tx to up to 2 RPCs** | ✅ double-broadcast in `watch` |
| + | **Rate-limit retry** (NodeFlare 1 req/10s) | ✅ added beyond original repo |

## Tool: `safe_mint.py`

Path: `~/.hermes/skills/nft-public-mint/scripts/safe_mint.py`

### Commands

```
keystore create --key 0x... --password 'STRONG' --out ~/.nft-keystore/seadrop.json
keystore addr --keystore ~/.nft-keystore/seadrop.json --password 'STRONG'

check --rpc <url> --contract <addr> [--keystore ...] [--max-total ...]
  Pre-flight: chain ID, contract code, balance vs limits, free-mint price

os-check --url https://opensea.io/drops/<slug> --wallet <addr>
  Verifikasi: domain opensea.io, chain robinhood, value == 0 (FREE)

watch --rpc <url> --contract <addr> --data <calldata> --keystore ... [--password ...]
  [--mode watch|confirm|auto|dry-run] [--value 0] [--max-price 0] [--max-fee 0.0005]
  Watch-then-fire: poll eth_call sampai sukses, lalu execute dengan semua safety checks
  --function-call 'name(uint256,address)' '1' '0x..' — ALTERNATIF --data: signature + args
    di-parse otomatis (selector keccak + ABI encode). Support address/bool/uintN/bytes32.
  --password: optional kalau set env SAFE_MINT_PASSWORD (lebih aman — gak keliatan di ps/history)
```

### Safety Layers (urutan eksekusi)

1. **Chain lock** — RPC chainId harus cocok dengan expected (4663 / 46630)
2. **Contract code check** — `eth_getCode` bukan "0x" → kontrak beneran ada
3. **Duplicate guard** — SQLite: wallet+contract combo belum pernah broadcast
4. **Supply check** — totalSupply < maxSupply (kalau interface tersedia)
5. **Free-mint probe** — getPublicDrop().price == 0 (informatif, non-blocking)
6. **Watch loop** — eth_call pending blok sampai sukses (mint udah buka)
7. **Parallel refresh** — nonce + gasPrice + balance + chainId dalam 4 call
8. **Final simulation** — eth_call pending + eth_estimateGas
9. **Hard limits** — max_price_per_nft, max_fee, max_total, balance_buffer
10. **Local signing** — private key gak pernah keluar dari mesin
11. **Double broadcast** — raw tx yang sama dikirim ke max 2 RPC endpoint
12. **Receipt wait** — 120s timeout, CONFIRMED / FAILED

### Perbandingan: osnm-z / TS Sniper vs safe_mint.py

| Aspek | osnm-z (Rust) | TS Sniper | safe_mint.py |
|-------|--------------|-----------|--------------|
| Keystore | pk.txt | wallets.json | ✅ Web3 keystore encrypted |
| Simulasi wajib | ❌ | ❌ | ✅ eth_call + estimateGas |
| Hard limits | ❌ | ❌ | ✅ 3-layer + buffer |
| Duplicate guard | ❌ | ❌ | ✅ SQLite |
| Free-mint verify | ❌ ("token=0" trap) | ❌ | ✅ getPublicDrop().price == 0 |
| Watch-then-fire | ❌ | ✅ (pre-sign) | ✅ poll eth_call + parallel refresh |
| Rate-limit retry | Generic Rust timeout | Minimal | ✅ 429 retry with backoff |

### "Token=0 Trap" — Sudah Teratasi

Dulu kita rugi $1.55 karena percaya `token=0` di header osnm-z padahal mint berbayar. Sekarang:

```bash
# 1. Cek dulu via safe_mint
python3 safe_mint.py check --rpc https://rpc.nodeflare.app/robinhood/public --contract 0x...

# Atau via OpenSea API
python3 safe_mint.py os-check --url https://opensea.io/drops/<slug> --wallet <your-wallet>

# 2. Value == 0 di OpenSea response = FREE. Value != 0 = PAID — jangan auto-mint.
```

### RPC Recommendations

| RPC | eth_chainId | eth_call | eth_getCode | eth_sendRawTransaction | Rate Limit | Notes |
|-----|-------------|----------|-------------|------------------------|------------|-------|
| rpc.nodeflare.app/robinhood/public | ✅ | ✅ | ✅ | ✅ | 1 req/10s | **Satu-satunya RPC full-method** — wajib untuk check + watch. 429 retry built-in (11s backoff). |
| robinhood.drpc.org | ✅ | ❌ (-32601) | ❌ (-32601) | ✅ (dikenali) | moderate | **Broadcast sink only** — tidak support eth_call/getCode/getBalance/estimateGas. Hanya chainId + sendRawTransaction + getTransactionReceipt. |
| rpc.mainnet.chain.robinhood.com | TLS-blocked dari Indonesia | | | | | Diblokir DPI Telkomsel. Lewat proxy curl saja. |
| robinhoodchain.blockscout.com/api/eth-rpc | ✅ | ✅ | ✅ | ? | 429-prone | Cloudflare challenge — script langsung kena 403. |

### RPCPool — Multi-Endpoint Health-Ranked Pool

`safe_mint.py` sekarang punya **RPCPool** yang otomatis:
- **Probe + rank** semua endpoint: mengukur chainId (wajib), block freshness (best-effort), latency
- **Cache ranking** — probe hanya sekali, hot loop tidak re-probe; `invalidate()` untuk refresh paksa sebelum fire-time reads
- **Failover on transport error** — endpoint down → coba berikutnya. **429 rate-limit = transport error** (fail over, bukan contract-level reply)
- **Negative cache** — endpoint yang reject method (-32601) di-skip otomatis (DRPC skip eth_call, dll)
- **Live demotion** — endpoint gagal dalam sesi dipindah ke belakang ranking
- **Double broadcast** — raw tx dikirim ke max 2 endpoint yang support sendRawTransaction
- **`best_client()`** — pin fire-time reads (nonce/gas/balance/chainId/estimateGas) ke SATU endpoint yang baru diprobe, supaya semua angka share mempool view yang sama (fix review: cegah nonce dari endpoint A + gas dari endpoint B yang inkonsisten)
- **`max_retries` passthru** — hot poll loop pakai `max_retries=1` supaya kena 429 langsung fail over, gak nge-block `RATE_LIMIT_RETRIES * BACKOFF` (6 × 11s = 66s)

### `rpc-test` — Test & Rank RPC Endpoints

```bash
# Test default RPC (NodeFlare + DRPC)
python3 safe_mint.py rpc-test

# Test custom RPC list
python3 safe_mint.py rpc-test --rpc https://rpc.nodeflare.app/robinhood/public --backup https://robinhood.drpc.org

# Output: daftar endpoint dengan latency, block, health, dan ranking
```

### Default RPC (tanpa flag --rpc)
- **Primary**: `https://rpc.nodeflare.app/robinhood/public` (full methods, 1 req/10s)
- **Backup**: `https://robinhood.drpc.org` (broadcast/receipt only)

Note: karena NodeFlare 1 req/10s, watch-then-fire polling 175ms TIDAK optimal. Polling efektif ~10s per iterasi. Untuk mint satu kali, ini cukup.

### Quick Start: Watch + Mint (Dry Run)

```bash
# 1. Siapkan keystore dari existing private key
python3 safe_mint.py keystore create \
    --key 0x<YOUR_PRIVATE_KEY> \
    --password 'StrongPassword123!' \
    --out ~/.nft-keystore/rh-main.json

# 2. Cek pre-flight (chain, contract, balance, free-mint)
python3 safe_mint.py check \
    --rpc https://rpc.nodeflare.app/robinhood/public \
    --contract 0x<COLLECTION_ADDRESS> \
    --max-total 0.001 \
    --keystore ~/.nft-keystore/rh-main.json \
    --password 'StrongPassword123!'

# 3. Dapatkan calldata (dari OpenSea, Etherscan, atau osnm-z)
# Contoh: mintPublic(uint256) 1 — pakai keccak (bukan sha3_256! NIST SHA3 ≠ EVM Keccak)
CALLLDATA=$(python3 -c "
from eth_hash.auto import keccak
sig = 'mintPublic(uint256)'
sel = keccak(sig.encode()).hex()[:8]
print('0x' + sel + '0000000000000000000000000000000000000000000000000000000000000001')
")

# 4. Watch-then-fire dalam dry-run mode
python3 safe_mint.py watch \
    --rpc https://rpc.nodeflare.app/robinhood/public \
    --contract 0x<COLLECTION_ADDRESS> \
    --data "$CALLLDATA" \
    --value 0 \
    --max-price 0 \
    --max-fee 0.0005 \
    --max-total 0.001 \
    --keystore ~/.nft-keystore/rh-main.json \
    --password 'StrongPassword123!' \
    --mode dry-run \
    --poll-interval 175 \
    --stop-after 3600
```

### Peringatan

- **JANGAN** broadcast mainnet tanpa dry-run sukses dulu
- Watch mode di `confirm` = bakal nanya "Broadcast now? [y/N]" sebelum sign
- **`auto` mode TIDAK ada konfirmasi** — langsung broadcast setelah simulation + limits pass. Gunakan hanya untuk free mint yang sudah diverifikasi on-chain (`getPublicDrop().price == 0`). Ini bukan "lebih aman"; ini fire-and-forget.
- **`--max-price 0` = NO LIMIT** (bukan "harga max 0 ETH"). Buat batasan per-NFT, set nilai > 0 (e.g. `--max-price 0.005`). Default 0 = skip check.
- NodeFlare rate-limit 1 req/10s — watch bisa lambat, tapi aman. Hot poll loop pakai `max_retries=1` (fail over cepat ke endpoint lain, gak nge-block 66s kena 429)
- **Keccak, bukan sha3_256** — semua selector/checksum/tx-hash pakai Ethereum Keccak-256. NIST SHA3 (`hashlib.sha3_256`) adalah hashing BERBEDA dan akan menghasilkan calldata/tx-hash yang salah. (Fix kritikal dari review MiniMax M3 2026-08-30.)
- **Password lewat env** — kalau gak mau password keliatan di `ps aux`/history: `export SAFE_MINT_PASSWORD=...` lalu skip `--password`
- **`os-check --wallet` bisa RESERVE drop slot** — POST `/drops/{slug}/mint` ke OpenSea bisa mengunci slot server-side. Jangan jalankan berulang sebelum siap mint beneran.