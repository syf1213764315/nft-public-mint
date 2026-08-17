---
name: nft-public-mint
description: "NFT mint arsenal — 2 tools: (1) nft-public-mint TS sniper for public SeaDrop mints (on-chain calldata, no OpenSea API, pre-sign + multi-RPC blast, Ethereum/Base/Robinhood); (2) osnm-z Rust pro CLI for WL/FCFS/public via OpenSea private API — multi-wallet self-funded (≤10) & sponsored EIP-7702 (≤25), eligibility check, tx replacement, wallet gen/fund/withdraw."
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
    when_not_to_use: |
      - Non-SeaDrop contracts (custom mint contracts need manual calldata)
---

# NFT Mint Arsenal — Sniper + Pro CLI

Dua tool dalam satu skill:

1. **nft-public-mint** (Node/TS) — fast local sniper untuk public SeaDrop mint. Calldata dibangun on-chain, tanpa OpenSea API, tanpa rate limit. Pre-sign sebelum stage buka, blast ke semua RPC.
2. **osnm-z / opensea-mint** (Rust) — pro CLI untuk WL/FCFS/public via OpenSea private API. Multi-wallet self-funded (≤10) & sponsored EIP-7702 (≤25), eligibility check, tx replacement, wallet generator, fund/withdraw.

> **Routing:** lihat `references/tool-comparison.md`. Singkatnya: public FCFS → nft-public-mint (lebih cepat, bebas API drift); WL/private stage → osnm-z (satu-satunya yang bisa); banyak wallet → osnm-z.

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

Untuk cron / auto-mint:

```bash
cd ~/.hermes/skills/nft-public-mint
echo "PRIVATE_KEY_1\nPRIVATE_KEY_2\n\n" | npm start
```

Tapi wizard lebih reliable untuk manual mint karena ada konfirmasi `Fire?` sebelum broadcast.

## Advanced: Add Chain

Edit `src/chains.ts` — tambah satu entry, no other code changes needed.

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