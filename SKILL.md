---
name: nft-public-mint
description: "NFT mint arsenal — 2 tools: (1) nft-public-mint TS sniper for public SeaDrop mints (on-chain calldata, no OpenSea API, pre-sign + multi-RPC blast, Ethereum/Base/Robinhood); (2) osnm-z Rust pro CLI for WL/FCFS/public via OpenSea private API — multi-wallet self-funded (≤10) & sponsored EIP-7702 (≤25), eligibility check, tx replacement, wallet gen/fund/withdraw. Plus opensea-rest/ (merged from ProjectOpenSea/opensea-skill v2.20.0): full OpenSea REST API client (64 scripts) — drops, collections, NFTs, tokens, real-time stream, Seaport fulfillment, free-mint REST scanner."
category: crypto
metadata:
  hermes:
    tags: [nft, mint, seadrop, sniper, ethereum, base, robinhood]
    related_skills: [superagent-web3-ops]
    when_to_use: |
      - Minting public NFTs on SeaDrop contracts
      - Sniping mints that open at a specific time
      - Multi-wallet parallel minting
      - When you need to avoid OpenSea API rate limits
      - Allowlist/FCFS/private stage mints (osnm-z)
      - Sponsored EIP-7702 multi-wallet minting (osnm-z)
      - Querying drops, collections, NFTs, tokens via OpenSea REST API (opensea-rest/)
      - Discovering free mints with official REST scanner (opensea-rest/scripts/osa_free_scan_rest.py)
      - Real-time mint/listing monitoring via WebSocket stream (opensea-rest/scripts/stream/)
      - Pre-mint analysis: collection stats, floor prices, holders, token OHLCV
      - Building Seaport fulfillment data (buy/sell) via REST (opensea-rest/scripts/)
    when_not_to_use: |
      - Non-SeaDrop contracts (custom mint contracts need manual calldata)
---

# NFT Mint Arsenal — Sniper + Pro CLI

Dua tool dalam satu skill:

1. **nft-public-mint** (Node/TS) — fast local sniper untuk public SeaDrop mint. Calldata dibangun on-chain, tanpa OpenSea API, tanpa rate limit. Pre-sign sebelum stage buka, blast ke semua RPC.
2. **osnm-z / opensea-mint** (Rust) — pro CLI untuk WL/FCFS/public via OpenSea private API. Multi-wallet self-funded (≤10) & sponsored EIP-7702 (≤25), eligibility check, tx replacement, wallet generator, fund/withdraw.

> **Security-first mint pipeline (port dari Robinhood-nft-sniper):** `scripts/safe_mint.py` — Python CLI dengan encrypted Web3 keystore, hard spending limits, mandatory eth_call simulation sebelum sign, duplicate guard, watch-then-fire engine, free-mint on-chain verification (getPublicDrop().price == 0 — fix "token=0 trap"), OpenSea validator, dan **RPCPool** (multi-endpoint health-ranked: probe sekali + cache, failover transport error, negative cache untuk method yang di-reject -32601, live demotion, 429 = transport, `invalidate()`/`best_client()` pin fire-time reads, `--function-call` parsing). Reviewed by MiniMax M3 2026-08-30 — ALL findings fixed (keccak vs sha3_256 CRITICAL fix, nonce-too-low raise, FAILED-state retry, max-price 0 = no limit, SAFE_MINT_PASSWORD env). Routing: `references/security-first-mint-pipeline.md`. Commands: `keystore`, `check`, `os-check`, `watch`, `rpc-test`. **Default RPC (tanpa --rpc): NodeFlare primary (`rpc.nodeflare.app/robinhood/public` — satu-satunya full-method, 1 req/10s) + DRPC backup (`robinhood.drpc.org` — broadcast/receipt only). Gunakan `check` atau `os-check` SEBELUM mint apapun — ini mencegah kerugian akibat mint berbayar yang dikira free.**
>
> **Routing:** lihat `references/tool-comparison.md`. Singkatnya: public FCFS → nft-public-mint (lebih cepat, bebas API drift); WL/private stage → osnm-z (satu-satunya yang bisa); banyak wallet → osnm-z; safety-first pre-flight → safe_mint.py.
>
> **Multichain RPC:** `references/multichain-rpc-reference.md` (merged dari febfrmn/nft-s + osnm-z chains.ts) — 8 EVM chain + solana: chainId, blast RPCs, send-only sequencers, Alchemy templates, explorer. Probe `wl_bypass_probe.py` support `--chain <name>` buat pindah chain.
>
> **Cari NFT free yang lagi LIVE di OpenSea (per chain):** `scripts/osa_free_scan.py <chain-keyword>`. Filter `chain.identifier == "robinhood"` (atau `ethereum/base/...`), terus query `dropBySlug` GraphQL POST (non-persisted — APQ hash cuma untuk query lengkap osnm-z), filter stage `now ∈ [startTime, endTime]` + `eligiblePrice.token.unit == 0` (kalau field null = UN-AUTH GraphQL, bukan free). Output: slug + address + stage info. **Aturan user: khusus free mint, jangan nembak paid collection.**
>
> **OpenSea REST API arsenal (merged dari ProjectOpenSea/opensea-skill v2.20.0):** `opensea-rest/` — client curl+jq lengkap (64 script) buat query drops/collections/NFTs/tokens via official REST API + real-time WebSocket stream + Seaport fulfillment_data. Instant free key (600 read/h, 7-day expiry). Scanner REST: `opensea-rest/scripts/osa_free_scan_rest.py` (filter `price == 0`, bedain `PUBLIC-FREE` vs `NEEDS-ALLOWLIST`). Routing lengkap + key setup: `opensea-rest/SKILL.md`. Sifatnya query-only (bukan eksekusi mint — itu tetep osnm-z / sniper).

## Instalasi (nft-public-mint)

```bash
cd ~/.hermes/skills/nft-public-mint
npm install
npm run build
```

## Konfigurasi

```bash
cp .env.example .env
```

Edit `.env` dengan private RPC URL untuk chain yang lo pake:

```
RPC_URL_BASE=https://base-mainnet.g.alchemy.com/v2/YOUR_KEY
RPC_URL_ETHEREUM=https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY
RPC_URL_ROBINHOOD=https://rpc.mainnet.chain.robinhood.com
```

Dapatkan free key dari [Alchemy](https://alchemy.com) — ini faktor terbesar buat menang mint.

> **Private keys NEVER disimpan di .env.** Lo paste pas run time, di memory, gak pernah ke disk.

## Cara Pake

```bash
cd ~/.hermes/skills/nft-public-mint
npm start
```

Wizard akan nanya 7 hal:

| Step | Apa yang diminta |
|------|------------------|
| 1 | Private keys — paste satu per baris, enter kosong selesai |
| 2 | Chain — Ethereum, Base, atau Robinhood |
| 3 | Quantity — jumlah NFT **per wallet** |
| 4 | NFT link — OpenSea link, slug, atau raw `0x` address |
| 5 | RPC — paste URL atau cukup Alchemy key (auto-expand) |
| 6 | Gas — ceiling + tip (base fee ditampilkan di prompt) |
| 7 | Timing — wait for stage (pre-sign, fire pas buka) atau fire now |

**Gas penting:**
- `maxFee` = base fee + tip (ceiling lo)
- `tip` = priority fee ke block producer
- Lo bayar `base fee + tip` — max fee cuma cap, bukan biaya aktual
- Wallet must hold `gasLimit × maxFee + mint price` — tool cek before fire

SeaDrop mint ≈ 135,000 gas untuk quantity 1.

## Quick Run

```bash
# Wizard
npm start

# Help
npm start -- --help
```

## Supported Chains

| Chain | Chain ID | Explorer |
|-------|----------|----------|
| Ethereum | 1 | etherscan.io |
| Base | 8453 | basescan.org |
| Robinhood Chain | 4663 | robinhoodchain.blockscout.com |

## Security Notes

- Private keys paste di runtime, **never written to disk**
- `.env`, `wallets/`, `*.key` semua di git-ignore
- Pake hot wallet khusus dengan dana secukupnya

## Troubleshooting

**`Set-Location : A positional parameter cannot be found`** (Windows PowerShell)
Jalankan perintah satu per satu, jangan di-paste bareng.

**`Could not read package.json` / `Missing script: build`**
Lo di folder salah. Run `cd ~/.hermes/skills/nft-public-mint` dulu.

**`npm` or `git` not recognised**
Install [Node.js 18+](https://nodejs.org) dan [Git](https://git-scm.com/downloads).

## Gas Strategy

1. Cek base fee di prompt — itu harga network
2. Tip 0.05-0.1 gwei untuk mint normal, 0.5-1 gwei untuk FOMO
3. Max fee = base fee + tip + buffer 10-20%
4. Tool cek wallet balance sebelum fire — kalo balance tipis, kasih tau ceiling maksimal

## Automation Pattern

Untuk cron / auto-mint / bot wrapper, kedua CLI bisa di-drive non-interaktif via
piped stdin (line-based wizard, bukan raw-mode TUI — gak butuh node-pty).
Prompt order lengkap + blank-answer gotchas + pola Telegraf bot wrapper lengkap ada
di `references/non-interactive-drive.md`.

```bash
cd ~/.hermes/skills/nft-public-mint
printf '%s\n' "$PK" '' 'base' '1' '0x<NFT-ADDRESS>' '' '' '' 'now' | npm start
```

Tapi wizard interaktif lebih reliable untuk manual mint karena ada konfirmasi `Fire?`
sebelum broadcast.

## Advanced: Add Chain

Edit `src/chains.ts` — tambah satu entry, no other code changes needed.

## SeaDrop Drop Triage — Sebelum Mint

Jangan langsung nembak. **Classify tipe kontrak DULU**: kalau `eth_getCode`
panjang 92 byte (minimal proxy, bukan ERC-1967) → kemungkinan OpenSea Studio
Drop, BUKAN SeaDrop — semua view function SeaDrop akan revert dan eligibility
cross-chain dicek backend OpenSea (bukan on-chain). Triage lengkap tipe ini:
`references/opensea-studio-drop-triage.md`.

Setelah tahu tipe-nya SeaDrop → probe config on-chain untuk tahu jalur mana yang
ADA dan mana yang buntu. Metodologi lengkap: `references/seadrop-drop-triage.md`.

**Reusable probe (1 command):**
```bash
python3 ~/.hermes/skills/nft-public-mint/scripts/seadrop_probe.py 0x<NFT_CONTRACT> [0x<MODULE>] [0x<WALLET>] [RPC_URL]
```

Dump semua config: publicDrop (harga, jam buka, fee lock), allowlist merkle root,
token-gated allowed tokens, signers, signed validation params, active stage,
fee recipients, mint stats, supply.

Yang dicek:
- **merkleRoot == 0x0** → allowlist mati
- **allowedTokens == []** → token-gated mati
- **restrictFeeRecipients == true** → fee terkunci, gak bisa redirect
- **getActiveStage revert** → stage tidak aktif
- **Snapshot sudah lewat** → beli token gate sekarang gak ngaruh (saldo diukur di snapshot)

## License

MIT (nft-public-mint) + MIT (osnm-z)

---

## osnm-z / opensea-mint — Pro CLI (Rust)

Source ada di `osnm-z/` dalam skill dir. CLI name: `opensea-mint`.

### Instalasi

```bash
# Rust toolchain (rustc 1.97.1 required — Cargo.toml pins it)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
source "$HOME/.cargo/env"

cd ~/.hermes/skills/nft-public-mint/osnm-z
cargo build --release --locked
# binary: ./target/release/opensea-mint
# atau install global:
cargo install --path . --locked
```

### Mode Wallet

| Mode | Config | Max wallets | Gas payer |
|---|---|---|---|
| Single | `WALLET_KEY=0x...` | 1 | wallet sendiri |
| Multi self-funded | `WALLETS_FILE=wallets.json` + `SPONSORED=false` | 10 | tiap wallet |
| Multi sponsored | `WALLETS_FILE` + `SPONSORED=true` + `SPONSOR_KEY` | 25 | sponsor bayar gas batch, wallet bayar mint value |

### Setup Cepat (multi-wallet)

```bash
# 1. Generate manifest
opensea-mint wallets create --count 10 --quantity 1 --output wallets.json

# 2. .env minimal
cat > .env <<'EOF'
WALLETS_FILE=wallets.json
SPONSORED=false
RECIPIENT_ADDRESS=0x<recipient>
RPC_URL=https://rpc.mainnet.chain.robinhood.com
FEE_AUTOMATIC=true
GAS_LIMIT=300000
EOF

# 3. Validasi
opensea-mint doctor

# 4. Mint interaktif (pilih collection, phase, quantity)
opensea-mint mint
```

### Sponsored EIP-7702 (advanced)

```bash
# 1. Deploy executor (sekali per sponsor, deterministic address)
opensea-mint deploy-executor
# → copy SPONSORED_EXECUTOR_ADDRESS ke .env

# 2. .env tambahan
SPONSORED=true
SPONSOR_KEY=0x<sponsor-key>
SPONSORED_EXECUTOR_ADDRESS=0x<dari deploy>

# 3. Mint
opensea-mint mint

# 4. SELALU revoke delegation setelah selesai
opensea-mint mint --undelegate
```

⚠️ Sponsored butuh chain dengan EIP-7702 + EIP-1153 (Prague EVM). Tool verify live sebelum dipakai. Executor contract **belum diaudit independen** — gunakan wallet khusus, jangan wallet utama.

### Perintah Lain

```bash
opensea-mint mint --fund 0.001     # kirim 0.001 native ke semua wallet manifest (Multicall3)
opensea-mint mint --withdraw       # tarik balance semua wallet ke recipient
opensea-mint calldata --collection <slug> --wallets wallets.json --token-id 0  # read-only, gak sign
opensea-mint doctor                # validasi config + RPC + mode
```

### Config Penting (.env)

| Setting | Default | Fungsi |
|---|---|---|
| `FEE_AUTOMATIC=true` | true | Auto fee dari network; false = manual `MAX_FEE_PER_GAS_GWEI` + `MAX_PRIORITY_FEE_PER_GAS_GWEI` |
| `GAS_LIMIT` | 300000 | Gas allowance mint per wallet |
| `TRANSACTION_MAX_ATTEMPTS` | 3 | Max submit + same-nonce replacement |
| `PENDING_TIMEOUT_SECONDS` | 20 | Waktu pending sebelum eligible replacement |
| `REPLACEMENT_BUMP_BPS` | 11250 | Fee bump factor replacement (112.5%) |
| `OPENSEA_CALLDATA_MAX_ATTEMPTS` | 40 | Max retry T-2 calldata fetch |
| `SPONSORED_OPERATION_DEADLINE_SECONDS` | 120 | Validitas signature wallet |

### Catatan Kritis

- osnm-z pakai **OpenSea private API yang unstable** — dedicated wallet, dana secukupnya.
- WL/private stage butuh OpenSea auth (SIWE) — tool handle otomatis, tapi butuh OpenSea account yang eligible.
- EIP-7702 delegation **persist setelah tx** — wajib `--undelegate` setelah selesai, termasuk kalau batch revert.
- Fee model: sponsored = sponsor bayar gas batch, tiap wallet masih wajib pegang `mint price × quantity` + OpenSea action reserve (`GAS_LIMIT × max fee`).

### Pitfalls (lesson learned)

- **`eligiblePrice.token.unit == 0` = free, `null` = UN-AUTHED** — GraphQL `dropBySlug` field `eligiblePrice` return null kalau wallet belum SIWE/authenticated. Bukan berarti free. Cara pasti cek free: `osnm-mint mint` (yg authorize + decode) output `token=0` di stage header, atau on-chain `getPublicDrop(nftContract)` view call.
- **"exceeds allocation or supply limit"** — kode GraphQL `MintLimitExceeded`
  → `InsufficientMintsRemainingError` di `src/opensea.rs` line 195.
  Penyebab paling umum di RH chain: **SOLD-OUT (`totalSupply == maxSupply`)**,
  OpenSea UI masih nampilin "available" karena cache stale. BUKAN cross-drop
  wallet block (cross-drop biasanya muncul sebagai `MintActionRejected` /
  `MintWalletIneligible`). Diagnostik: probe `totalSupply()` & `maxSupply()`
  on-chain — kalau sama, drop habis. Cara pasti verifikasi sebelum
  troubleshoot wallet: panggil `getPublicDrop(contract)` view + `totalSupply()`.
- **"RPC endpoint 1 does not support the required three-read JSON-RPC batch"** — NodeFlare batch `eth_getBlockByNumber + eth_getTransactionCount + eth_maxPriorityFeePerGas` intermittent. Root cause: rate limit (1 req/10s) atau reqwest connection pool reset. Retry after 12s biasanya lulus. Kalau persistent: turunin `OPENSEA_CALLDATA_MAX_ATTEMPTS` (default 40 = banyak round-trip) atau switch ke DRPC/sequencer (tapi DRPC `-32601` di `eth_maxPriorityFeePerGas`).
- **APQ hash `e1b54354...` cuma untuk query lengkap osnm-z** — kalo lo modifikasi query (tambah field, rename), persisted query hash invalid → 400 Bad Request. Pakai POST non-persisted (`{"query": "...", "variables": {...}, "operationName": "D"}`) untuk custom scan.
- **"ended" di osnm-z = OpenSea bilang stage endTime < now** — tapi `endTime` di GraphQL kadang >1 tahun (e.g. `2027-08-12` untuk collection "ended"). Pola: cek `endTime` terbaru = urutkan descending, baru filter yang `now ∈ [startTime, endTime]`.
- **User rule: free mint only** — kalo user minta "cari NFT free di X, mint 1x buat tes", **SELALU filter `eligiblePrice.unit == 0` atau on-chain `getPublicDrop.price == 0`** sebelum run `osnm-mint mint`. Jangan gaskan paid collection karena user revisi "Jangan mint nft yg paid ya".
- **Keccak vs SHA3-256 (CRITICAL, found by MiniMax M3 review 2026-08-30)** — EVM selector/checksum/tx-hash pakai **Keccak-256**, BUKAN NIST SHA3. `hashlib.sha3_256` (Python) menghasilkan hash BERBEDA. Kalau lo bikin calldata/checksum manual: `from eth_hash.auto import keccak; keccak(sig.encode()).hex()`. `safe_mint.py` udah di-fix semua (sebelumnya `abi_encode_call`, `checksum_address`, `broadcast_same_raw` pakai sha3_256 → selector salah, tx-hash palsu, checksum invalid).

## Robinhood Chain Spesifik — Funding, Gas, SOLD-OUT

Lihat `references/robinhood-chain-pitfalls.md` untuk lesson lengkap dari
live session 2026-08-25. Highlights:

- **Gas limit ≥ 100k (bukan 21000)** untuk semua tx di RH chain —
  `intrinsic gas too low` kalau pakai default ETH transfer. Submit harus
  overestimate; node yang truncate berdasarkan actual gas_used.
- **SOLD-OUT vs cross-drop wallet block** — error "exceeds allocation or
  supply limit" bisa berarti `totalSupply == maxSupply` (drop habis),
  BUKAN masalah wallet. Selalu probe `totalSupply()` on-chain sebelum
  troubleshoot wallet. Scan pattern: `scripts/supply_scan.py` di
  `~/nft-mint/` (port ke `scripts/` kalau mau share).
- **`opensea-mint mint --fund` butuh EIP-7702** — TIDAK available di RH
  chain. Funding wajib manual via `fund_fresh.py` (EIP-1559, gas 100k,
  max_fee = base × 2). Sweep ke main wallet via `sweep.py`.
- **osnm-z .env validator STRICT** — tolak unknown key (WALLET tanpa
  _KEY), tolak duplicate key. Selalu sanitize .env sebelum dipakai CLI:
  whitelist ke list di `src/config.rs` line 20-30, tambah default
  `FEE_AUTOMATIC=true` + `GAS_LIMIT=100000`, cek no duplicate.
- **Lost-wallet trap** — `opensea-mint wallets create` HARUS backup ke
  path persisten sebelum transfer ETH. Kalau file hilang, ETH stuck
  permanent. Pattern aman: `~/nft-mint/wallets/<slug>-<ts>.json` + log
  timestamp.