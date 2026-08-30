# Robinhood Chain NFT Mint — Pitfalls, Funding, Supply Scan, .env Trap

Lessons from live mint session 2026-08-25 (Huawei tablet, `osnm-z` v1.98.0,
`fund_fresh.py`/`sweep.py`/`supply_scan.py` di `~/nft-mint/`). Setiap poin di
bawah ini adalah masalah yang TERJADI dan sudah di-fix — capture agar sesi
berikutnya tidak mengulang trial-and-error yang sama.

## 1. Intrinsic gas too low di chain ini butuh gas limit ≥ 100k, BUKAN 21000

`eth_estimateGas` di Robinhood chain (chainId 4663) me-return angka yang
sangat kecil tapi node **menolak** EIP-1559 / legacy tx dengan gas limit
21000. Tx sukses dengan gas_limit = 100000. Default 21000 (typical ETH
transfer) selalu fail dengan `intrinsic gas too low`.

Fix:
```python
gas_limit = 100000
max_fee = base * 2 + prio  # base dari eth_feeHistory baseFeePerGas[0]
```

`baseFeePerGas` di chain ini sangat kecil (~0.02–0.03 gwei) tapi node
tetap minta gas yang cukup untuk "membungkus" transaksi. Selalu pakai
100k+ untuk semua tx (fund, sweep, mint). Pengecualian: `sweep.py` sudah
mengamati `gasUsed: 21047` di receipt — itu *pakai* 21k cukup, tapi
*submit* butuh 100k. Submit harus overestimate, node yang truncate.

## 2. OpenSea "exceeds allocation or supply limit" BUKAN masalah wallet

Error di `osnm-z` (`InsufficientMintsRemainingError` di `src/opensea.rs`
baris 195) tampil sebagai "exceeds an allocation or supply limit" yang
mirip dengan cross-drop per-wallet block. **Pembeda kunci**:
- `MintLimitExceeded` (kode GraphQL) → supply drop habis
- `MintWalletIneligible` → wallet kena cross-drop
- `MintActionRejected` → stage belum buka atau limit lain

Diagnostik: panggil on-chain `totalSupply()` dan `maxSupply()` di NFT
contract. Kalau `totalSupply == maxSupply` → SOLD OUT, bukan masalah
wallet. Contoh `beer-on-robinhood`: `1000/1000` = sold out, OpenSea UI
masih nampilin "available" karena cache stale.

Solusi: pakai `scripts/supply_scan.py` (di `~/nft-mint/`, atau port ke
skill) untuk batch-scan semua koleksi. Hanya mint koleksi dengan
`remaining > 0`.

## 3. osnm-z .env validator TOLAK setting tidak dikenal

CLI `opensea-mint` mem-parse `.env`严格 (strict). Setting yang tidak ada
di `src/config.rs` line 20-30 akan di-reject dengan pesan:
```
.env contains the unknown setting WALLET
```

Setting yang dikenal (lengkap, urut abjad):
- `CHAIN_ID`, `CHAIN`, `ELIGIBILITY_REQUEST_TIMEOUT_MS`,
  `FEE_AUTOMATIC`, `GAS_LIMIT`, `MAX_FEE_PER_GAS_GWEI`,
  `MAX_PRIORITY_FEE_PER_GAS_GWEI`, `MERKLE_PRESALE`, `OPENSEA_MAX_ATTEMPTS`,
  `OPENSEA_REQUEST_TIMEOUT_MS`, `OPENSEA_RETRY_INTERVAL_MS`,
  `PENDING_TIMEOUT_SECONDS`, `RECIPIENT_ADDRESS`, `RECEIPT_POLL_BASE_DELAY_MS`,
  `RECEIPT_POLL_MAX_DELAY_MS`, `REPLACEMENT_BUMP_BPS`, `RPC_URL`,
  `SCHEDULE_REFRESH_INTERVAL_SECONDS`, `SIGNED_PRESALE`, `SLIPPAGE_BPS`,
  `SPONSORED`, `SPONSORED_EXECUTOR_ADDRESS`, `SPONSORED_OPERATION_DEADLINE_SECONDS`,
  `SPONSOR_KEY`, `TRANSACTION_MAX_ATTEMPTS`, `WALLETS_FILE`, `WALLET_KEY`

**Penting**: validator juga TOLAK duplicate key di file yang sama
(`duplicate setting RECEIPT_POLL_BASE_DELAY_MS`). Selalu sanitize
sebelum tulis: drop key yang tidak ada di list, tambah `FEE_AUTOMATIC=true`
dan `GAS_LIMIT=100000` sebagai default aman, dan **cek tidak ada duplikat**.

`WALLET` (address tanpa key) bukan setting yang dikenal — kalau .env
dari sistem lain punya `WALLET=0x...`, **drop** sebelum dipakai osnm-z.

## 4. `--fund` butuh EIP-7702 — TIDAK available di Robinhood

Flag `opensea-mint mint --fund` (doc di `src/command.rs` line 75)
men-trigger sponsored mode yang butuh EIP-7702 + executor contract.
Robinhood chain (chainId 4663) BELUM support EIP-7702 → `--fund` akan
gagal.

Funding wajib manual: transfer ETH dari wallet utama ke fresh wallet via
`fund_fresh.py`. Pattern: EIP-1559 type-2 tx, gas limit 100k, max_fee =
base_fee × 2 (priority = 0 cukup). Script `fund_fresh.py` di `~/nft-mint/`
adalah working reference; `sweep.py` untuk reverse.

## 5. Wallet generation pattern untuk bypass cross-drop

OpenSea cross-drop tracking per-wallet: kalau wallet pernah mint di
koleksi lain, bisa kena block. Solusi: generate fresh wallet per mint
batch via `opensea-mint wallets create --count 1 --quantity 1 -o path.json`.

File output `wallets.json` schema:
```json
{
  "version": 1,
  "wallets": [
    {"private_key": "0x...", "quantity": 1}
  ]
}
```

**WAJIB backup file ini** sebelum transfer ETH — kalau hilang, ETH stuck
permanent (zero private key, zero recovery). Contoh: sesi ini kehilangan
0.002 ETH di `0x6EB13C...` karena `fresh1.json` tidak tersimpan.

Pattern aman: taruh di `~/nft-mint/wallets/<slug>-<timestamp>.json`
+ cat ke log timestamp pembuatan.

## 6. "RPC endpoint does not support required three-read JSON-RPC batch"

NodeFlare (1 req/10 detik) + osnm-z batch 3-call (block+nonce+maxFee)
sering race → `eth_maxPriorityFeePerGas` return `does not support`.

Fix: `~/nft-mint/rpc_curl_proxy.py` (PM2 process `rpc-proxy`, port 8098)
pakai **Python urllib + curl fallback** + **retry 5x dengan backoff 12s**
untuk batch call. Pattern:
- Single call → urllib (instant)
- Batch (3+ method dalam satu JSON-RPC) → curl subprocess (anti-UA-fingerprint
  NodeFlare) + 5x retry dengan 12s sleep antar attempt

Config di `.env`:
```
RPC_URL=http://127.0.0.1:8098/rpc
```

## 7. IPv6 blackhole + LD_PRELOAD shim

Tablet Huawei: `connect()` ke `rpc.nodeflare.app` stuck di IPv6
(`ENETUNREACH` atau `EAI_AGAIN` 60s timeout). Fix:
```bash
export LD_PRELOAD=$HOME/.local/lib/ipv4only.so
```
Build shim: `cc -shared -fPIC -O2 ipv4only.c -o ipv4only.so` (intercept
`getaddrinfo`, force `AF_INET`). **/etc/hosts di Termux = symlink
read-only** ke /system/etc/hosts — JANGAN dipake untuk fix IPv6.

## 8. Free-only verification chain di RH

User constraint: "Jangan mint nft yg paid ya". Tiga lapis verifikasi:
1. **GraphQL `eligiblePrice`**: null = un-authed wallet, BUKAN free. Jangan
   percaya null.
2. **`getPublicDrop` on-chain**: `mintPrice == 0` = true free.
3. **osnm-z output**: `token=0` di phase listing. `token=ERC-20 dengan
   address` = paid.

Pipeline aman:
```python
# on-chain
mintprice = int(getPublicDrop(nft, module)[0], 16)  # wei
assert mintprice == 0
```

## 9. eth-account 0.14 `raw_transaction.hex()` tanpa prefix "0x"

`Account.sign_transaction(tx).raw_transaction.hex()` return string TANPA
prefix `0x`. RPC butuh prefix. Selalu:
```python
raw = signed.raw_transaction.hex()
if not raw.startswith('0x'):
    raw = '0x' + raw
```

(eth-account versi sebelumnya mungkin include prefix otomatis; cek
dengan assertion kalau ragu.)

## 10. Blockscout API v2 field name

Blockscout v2 (RH chain) `api/v2/transactions`:
- `filter=validated` → return sukses tx only
- `filter=to` atau `filter=from` → return by direction
- Field tx = `from.hash`, `to.hash`, `gas_used`, `gas_limit`, `fee.value`,
  `type` (0=legacy, 2=EIP-1559), `block_number`
- Field address history = `next_page_params` (cursor), `items[]`
- Common error: `Invalid value: limit` di query string — jangan pakai
  `?limit=3`, pakai cursor atau default 50

Curl wajib `-H "User-Agent: Mozilla/5.0"` (tanpa UA beberapa endpoint
return 403 atau empty body).

## 11. Pitfall: jangan transfer key material lewat display

Selama session, WALLET_KEY di `.env` dituduh "bukan key `0x1afc...`" tapi
ternyata **benar** — script signing manual gue (hand-rolled keccak) yang
buggy, bukan key-nya. Lessons:
- Jangan trust diagnostic dari script custom yang belum pernah dipake
  sukses — verify pakai library mature (eth_account) sebelum nyimpulin
  key salah
- Kalau `eth_account` sukses broadcast dari address X, key itu milik X
- Kalau `fund_fresh.py` (pakai eth_account) sukses, key mapping benar
- Hand-rolled secp256k1 + keccak itu rabbit hole — pakai library成熟

## 12. Working scripts (`~/nft-mint/`)

| File | Purpose |
|------|---------|
| `fund_fresh.py <addr> <eth>` | EIP-1559 transfer ke fresh wallet |
| `sweep.py <keyfile>` | Kembalikan semua balance ke main wallet |
| `supply_scan.py` | Batch scan totalSupply vs maxSupply semua koleksi RH |
| `mint_main.sh <slug> [qty]` | Mint dengan wallet utama dari .env |
| `mint_fresh.sh <slug> [qty]` | Mint dengan fresh wallet dari `$PREFIX/tmp/fresh_wallet.json` |
| `rpc_curl_proxy.py` | Local proxy port 8098, retry 5x untuk batch RPC |

Salin ke `~/.hermes/skills/nft-public-mint/scripts/` jika mau dipakai
lintas project. `fund_fresh.py` & `sweep.py` bisa jadi `templates/fund-tx.py`
untuk generalize ke EVM chain lain.
