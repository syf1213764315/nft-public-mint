# NFT Mint Arsenal — Tool Comparison & Routing

Dua tool dalam satu skill, pilih berdasarkan kebutuhan mint.

## Decision Table

| Kebutuhan | Tool | Kenapa |
|---|---|---|
| Public SeaDrop mint, FCFS snipe, tanpa OpenSea API | **nft-public-mint** (Node/TS) | Calldata dibangun on-chain, pre-sign semua tx sebelum stage buka, blast ke semua RPC. Bebas rate limit OpenSea. |
| Allowlist (WL) / FCFS / public, eligibility check per wallet | **osnm-z / opensea-mint** (Rust) | Pakai OpenSea private API (GraphQL + SIWE auth). Bisa deteksi & mint WL stage yang butuh signature. |
| Multi-wallet 10x self-funded concurrent | **osnm-z** (self-funded) | Wallets file manifest, tiap wallet bayar gas sendiri, independent failure boundary. |
| Multi-wallet 25x sponsored EIP-7702 | **osnm-z** (sponsored) | Sponsor bayar gas batch, wallet cuma bayar mint value. Butuh deploy executor contract + chain support EIP-7702/EIP-1153 (Prague EVM). |
| Auto wallet generation + fund + withdraw | **osnm-z** | `wallets create`, `mint --fund`, `mint --withdraw`, `mint --undelegate`. |
| Tx replacement (same-nonce bump fee) | **osnm-z** | `TRANSACTION_MAX_ATTEMPTS`, `REPLACEMENT_BUMP_BPS` — kalo pending gak ke-mine, auto replace dengan fee lebih tinggi. |
| Chain apa pun | **nft-public-mint** | Chains registry: Ethereum, Base, Robinhood. Tambah chain = 1 entry di chains.ts. |
| Chain EIP-7702 capable | **osnm-z** | Baca chain ID dari RPC_URL langsung. |

## Chain Coverage

| Chain | nft-public-mint | osnm-z |
|---|---|---|
| Ethereum (1) | ✅ | ✅ (RPC-based) |
| Base (8453) | ✅ | ✅ |
| Robinhood (4663) | ✅ | ✅ (RPC-based) |
| Chain lain | ➕ 1 entry di chains.ts | ➕ ganti RPC_URL |

## Kapan pake yang mana

1. **Mint public tanpa WL** → nft-public-mint. Lebih cepat (pre-sign), gak kena rate limit OpenSea, gak butuh auth OpenSea.
2. **Mint ada WL / private stage / FCFS** → osnm-z. Ini satu-satunya yang bisa dapet calldata wallet-specific via OpenSea API.
3. **Banyak wallet** → osnm-z (self-funded ≤10, sponsored ≤25).
4. **Gas competition ketat** → osnm-z punya tx replacement; nft-public-mint punya blast multi-RPC. Kombinasi ideal: nft-public-mint buat FCFS public, osnm-z buat WL.

## Catatan Penting

- osnm-z butuh Rust toolchain (rustc 1.97.1) — install via rustup, build `cargo build --release --locked`, binary di `target/release/opensea-mint`.
- osnm-z pakai OpenSea private API yang unstable — dedicated wallet + dana secukupnya.
- Sponsored EIP-7702 butuh: chain support EIP-7702 + EIP-1153 (Prague EVM), deploy executor via `opensea-mint deploy-executor`, verifikasi runtime hash.
- nft-public-mint gak pernah sentuh OpenSea — aman dari API drift.
- Private keys: kedua tool terima di runtime/env, jangan commit.
