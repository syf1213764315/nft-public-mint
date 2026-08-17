<table width="100%">
  <tr>
    <td align="left" width="120">
      <img src="assets/osnm-z.svg" alt="OSNM-Z Mint Bot logo" width="100" />
    </td>
    <td align="right">
      <h1>OSNM-Z</h1>
      <h3>Single and multi-wallet Rust CLI for OpenSea-hosted mints</h3>
    </td>
  </tr>
</table>

---

<h2 align="center">Overview</h2>

OSNM-Z is a cross-platform Rust minting CLI for OpenSea-hosted SeaDrop NFT collections on OpenSea-supported EVM chains matching the configured RPC. It discovers the collection, authenticates each configured wallet, verifies eligibility, and supports one or more allowlist (WL), first-come-first-served (FCFS), and public phases. Users can mint in **single-wallet mode** or **multiple-wallet mode**, execute concurrent **self-funded multi-wallet** mints, sponsor multi-wallet gas through **EIP-7702 sponsored mode** on compatible chains, and fund up to 10 self-funded manifest wallets atomically in one verified Multicall3 transaction when the canonical deployment is available. Multi-phase selections execute sequentially by start time, and `SPONSORED=true` deliberately selects `WALLETS_FILE` when both wallet sources are configured.

```mermaid
flowchart TD
    CLI["OSNM-Z mint session"] --> SINGLE["Single wallet<br/>WALLET_KEY"]
    CLI --> MULTI["Multi wallet<br/>WALLETS_FILE"]

    SINGLE --> S1["Authenticate one wallet<br/>and load its eligibility"]
    S1 --> S2["Select one or more phases<br/>active or scheduled"]
    S2 --> S3["T-10: capture nonce, fees, balance,<br/>metadata, eligibility, and local funding"]
    S3 --> S4["T-2: fetch and validate<br/>wallet-specific calldata"]
    S4 --> S5["Wallet signs and submits<br/>its own EIP-1559 mint transaction"]
    S5 --> S6["Wallet pays mint value and gas<br/>NFT remains in that wallet"]

    MULTI --> SPONSORED["Sponsored EIP-7702<br/>maximum 25 wallets"]
    MULTI --> SELF["Self-funded concurrent mint<br/>maximum 10 wallets"]

    SPONSORED --> P1["Verify live EIP-7702 and EIP-1153<br/>and exact executor runtime"]
    P1 --> P2["Authenticate every wallet<br/>and keep only eligible candidates"]
    P2 --> P3["T-15: capture account state and fees<br/>sign new or replacement delegations when required"]
    P3 --> P4["T-2: fetch all wallet actions<br/>in one aliased GraphQL request"]
    P4 --> P5["Validate each action and sign an exact<br/>wallet EIP-712 mint operation"]
    P5 --> P6["Each wallet pays its signed mint value<br/>sponsor pays the complete outer gas"]
    P6 --> P7["Executor isolates each wallet call<br/>and verifies the expected safe mint"]
    P7 --> P8["Successful NFTs are forwarded atomically<br/>to the configured recipient"]
    P7 --> P9["Failed or skipped wallets retain their mint value<br/>without undoing other wallet successes"]
    P8 --> P10["Delegation remains active<br/>run opensea-mint mint --undelegate afterward"]
    P9 --> P10

    SELF --> F1["Authenticate every wallet<br/>and keep only eligible candidates"]
    F1 --> F2["During setup: calculate captured mint value when available,<br/>maximum gas, fees, and balance locally"]
    F2 --> F3["Prompt to top up, recheck, or skip<br/>each underfunded wallet"]
    F3 --> F3A["T-10: refresh nonce, fees, and balance<br/>with a non-interactive safety recheck"]
    F3A --> F4["T-2: fetch all wallet actions<br/>in one aliased GraphQL request"]
    F4 --> F5["Validate actions, then execute wallets<br/>concurrently and independently"]
    F5 --> F6["Each wallet signs, pays mint value,<br/>and pays its own EIP-1559 gas"]
    F6 --> F7["Verify each successful mint receipt<br/>and extract the minted NFT assets"]
    F7 --> F8["If needed, that wallet signs and pays<br/>a separate safe-transfer transaction"]
    F8 --> F9["NFT reaches the configured recipient<br/>failures do not stop other wallets"]
```

| Wallet mode | Who pays? | Transactions | NFT destination | Failure boundary |
| --- | --- | --- | --- | --- |
| **Single wallet**<br/>`WALLET_KEY` | The configured wallet pays its mint value and gas | One independently signed mint transaction per selected phase | Remains in the configured wallet | That wallet and phase only |
| **Multiple wallets: sponsored EIP-7702**<br/>`WALLETS_FILE` + `SPONSORED=true` | Each manifest wallet pays its own mint value; `SPONSOR_KEY` pays the complete batch gas | One executor batch per selected phase, carrying authorizations for wallets that need a new or replacement delegation | Forwarded atomically to `RECIPIENT_ADDRESS`, or the sponsor fallback | One wallet can fail without stopping the others; outer execution failure reverts contract execution and value movement, but processed delegations may persist |
| **Multiple wallets: self-funded concurrent**<br/>`WALLETS_FILE` + `SPONSORED=false` | Every manifest wallet pays its own mint value, mint gas, and forwarding gas | Independent wallet transactions run concurrently, followed by safe-transfer transactions when the recipient differs | Forwarded after receipt verification to `RECIPIENT_ADDRESS`, or the sponsor fallback | Each wallet succeeds or fails independently |

The integrated [`SponsoredMintExecutor`](contracts/README.md) is used only by **sponsored EIP-7702 mode**. **Self-funded multi-wallet mode** does not delegate wallets or call the executor. Its interactive funding and top-up gate runs during setup, before approval or scheduling. T-10 and final pre-signing checks are non-interactive safety rechecks; a wallet is skipped only if its balance or required cost became insufficient after setup. EIP-7702 delegation can remain after success or failure, so sponsored users must run `opensea-mint mint --undelegate` and verify revocation.

> [!IMPORTANT]
> `SPONSORED=true` sponsors the on-chain transaction gas only. Every eligible wallet must hold `eligible mint price × its configured quantity`, plus the local OpenSea action-construction reserve calculated as `GAS_LIMIT × maximum configured fee per gas`. OpenSea may reject wallet-specific calldata construction without that reserve even for a free sponsored mint. The executor spends only the validated mint value from the wallet, so the unused reserve remains there; the sponsor pays EIP-7702 authorization processing, batch execution, mint-call gas, NFT verification, and forwarding gas. Setup verifies both balances before approval.

> [!CAUTION]
> This project uses OpenSea's private, unstable web API and trusts opaque transaction data returned by OpenSea. It is experimental and high risk. Use only dedicated wallets funded with the amount required for the intended mint.

---

<h2 align="center">Quick Start</h2>

### 1. Installation

The repository requires Git, Rust, and a native C/C++ compiler. Clone and compile it on the operating system where it will run.

#### **Windows**

Install [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) with **Desktop development with C++**, [Git for Windows](https://git-scm.com/downloads/win), and [Rust](https://www.rust-lang.org/tools/install). Reopen PowerShell, then run:

```powershell
git clone https://github.com/zunmax/osnm-z.git
Set-Location osnm-z
cargo install --path . --locked
opensea-mint --version
```

#### **Linux or WSL**

Install the prerequisites for the distribution:

**Ubuntu or Debian:**

```bash
sudo apt-get update
sudo apt-get install -y build-essential curl ca-certificates git
```

**Fedora or RHEL:**

```bash
sudo dnf install -y gcc gcc-c++ make curl ca-certificates git
```

**Arch Linux:**

```bash
sudo pacman -S --needed base-devel curl ca-certificates git
```

Install Rust, clone the repository, and install the CLI:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
git clone https://github.com/zunmax/osnm-z.git
cd osnm-z
cargo install --path . --locked
opensea-mint --version
```

#### **macOS**

Install Apple's command-line developer tools, then install Rust and the CLI:

```bash
xcode-select --install
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
git clone https://github.com/zunmax/osnm-z.git
cd osnm-z
cargo install --path . --locked
opensea-mint --version
```

### 2. Set Up `.env` and `wallets.json`

Create the active environment file:

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# Linux or macOS
cp .env.example .env
```

Keep the shared settings and select one wallet mode.

```dotenv
RPC_URL=https://your-chain-rpc.example
FEE_AUTOMATIC=true
GAS_LIMIT=300000
```

**Single-wallet mode:**

```dotenv
WALLET_KEY=0x<64-hex-character-private-key>
```

**Multiple-wallet mode: self-funded:**

```text
opensea-mint wallets create --count 10 --quantity 1 --output wallets.json
```

```dotenv
# Remove or comment WALLET_KEY from the copied example.
WALLETS_FILE=wallets.json
SPONSORED=false
RECIPIENT_ADDRESS=0x<40-hex-character-recipient-address>
# SPONSOR_KEY=0x<required-only-for-funding-or-recipient-fallback>
```

**Multiple-wallet mode: sponsored EIP-7702:**

```text
opensea-mint wallets create --count 25 --quantity 1 --output wallets.json
```

```dotenv
# Remove or comment WALLET_KEY from the copied example.
WALLETS_FILE=wallets.json
SPONSORED=true
SPONSOR_KEY=0x<64-hex-character-sponsor-private-key>
RECIPIENT_ADDRESS=0x<40-hex-character-recipient-address>
# Add SPONSORED_EXECUTOR_ADDRESS after running deploy-executor.
```

```text
opensea-mint deploy-executor
```

Copy the printed executor address into `.env` as `SPONSORED_EXECUTOR_ADDRESS=0x...`. Keep `.env` and `wallets.json` private; both contain private keys.

### 3. Run the Command

Validate the selected mode, then start the interactive mint session:

```text
opensea-mint doctor
opensea-mint mint
```

If the installed command is unavailable, run the same arguments through Cargo from the repository root:

```text
cargo run --release --locked -- doctor
cargo run --release --locked -- mint
```

To use the direct release binary, build it once. On Windows PowerShell:

```powershell
cargo build --release --locked
.\target\release\opensea-mint.exe doctor
.\target\release\opensea-mint.exe mint
```

On Linux or macOS:

```bash
cargo build --release --locked
./target/release/opensea-mint doctor
./target/release/opensea-mint mint
```

---

<h2 align="center">Available Commands</h2>

`wallets create` does not load `.env`; help and version output also exit before configuration is loaded. All operational network commands use the active `.env`.

| Command | Mode | What it does | Broadcasts? |
| --- | --- | --- | --- |
| `opensea-mint doctor` | All | Validates configuration, wallet input, RPC connectivity, and the active mode | No |
| `opensea-mint deploy-executor` | **Sponsored setup** | Deploys or verifies the deterministic per-sponsor executor and prints its address | Only when deployment is needed |
| `opensea-mint mint` | All | Opens the interactive collection, eligibility, phase, quantity, and mint flow | Yes, after confirmation |
| `opensea-mint mint --fund <NATIVE_AMOUNT>` | **Sponsored or self-funded multi-wallet** | Sends the same native-token amount to every wallet in `wallets.json` | Yes, after confirmation |
| `opensea-mint mint --withdraw` | **Self-funded multi-wallet** | Withdraws each wallet's safely signable native-token balance to the configured recipient | Yes, after confirmation |
| `opensea-mint mint --undelegate` | **Multi-wallet cleanup** | Revokes EIP-7702 delegation for every manifest wallet | Yes, after confirmation |
| `opensea-mint calldata ...` | **Read-only multi-wallet** | Authenticates wallets and fetches validated active-stage mint calldata | No |
| `opensea-mint wallets create ...` | Local utility | Creates a new private-key manifest without loading `.env` or connecting to a network | No |

### `doctor` and `deploy-executor` parameters

| Command | Command-line parameters | Required configuration |
| --- | --- | --- |
| `opensea-mint doctor` | None | A complete **single-wallet**, **self-funded multi-wallet**, or **sponsored multi-wallet** `.env` |
| `opensea-mint deploy-executor` | None | `RPC_URL`, `FEE_AUTOMATIC`, `GAS_LIMIT`, and `SPONSOR_KEY`; `SPONSORED_EXECUTOR_ADDRESS` is optional for this command |

### `mint` parameters

The three options are mutually exclusive. Running `mint` without an option starts minting.

| Parameter | Value | Default | Requirements |
| --- | --- | --- | --- |
| `--fund <NATIVE_AMOUNT>` | Positive decimal native-token amount with up to 18 decimal places, such as `0.001` | None | `WALLETS_FILE` and `SPONSOR_KEY`; maximum 10 self-funded wallets or 25 sponsored wallets; sponsor must not be a manifest wallet |
| `--withdraw` | Flag; no value | Off | `WALLETS_FILE` and `SPONSORED=false`; maximum 10 wallets |
| `--undelegate` | Flag; no value | Off | `WALLETS_FILE`, `SPONSOR_KEY`, and an EIP-7702-compatible RPC |

### `calldata` parameters

```text
opensea-mint calldata --collection <COLLECTION> --wallets <WALLETS> --token-id <TOKEN_ID>
```

| Parameter | Value | Default | Required? |
| --- | --- | --- | --- |
| `--collection <COLLECTION>` | OpenSea slug, OpenSea collection URL, or NFT contract address | None | Yes |
| `--wallets <WALLETS>`, `-w <WALLETS>` | Path to a version-1 wallet JSON file | None | Yes |
| `--token-id <TOKEN_ID>` | Unsigned decimal token ID; ERC-721 conventionally uses `0` | `0` | No |

The read-only request supports at most 250 wallet aliases and requires one unambiguous active stage.

### `wallets create` parameters

```text
opensea-mint wallets create --count <COUNT> --quantity <QUANTITY> --output <OUTPUT>
```

| Parameter | Value | Default | Requirements |
| --- | --- | --- | --- |
| `--count <COUNT>` | Positive integer number of wallets | `1` | Generated file must remain within 1 MiB |
| `--quantity <QUANTITY>` | Positive integer mint quantity stored for every wallet | `1` | Final mint quantity is still limited by the selected phase |
| `--output <OUTPUT>`, `-o <OUTPUT>` | New output file path | `wallets.json` | Existing files are never overwritten |

### Help and version parameters

| Parameter | Value | What it does |
| --- | --- | --- |
| `--help`, `-h` | No value | Shows top-level help, or command help when placed after a command |
| `--version`, `-V` | No value | Shows the CLI version when used at the top level |

Any installed-command example can be replaced with `cargo run --release --locked -- <arguments>`. A direct target path can be used instead on the matching operating system.

---

<h2 align="center">Configuration</h2>

All user configuration lives in one `.env`; there is no separate multi-wallet environment file, TOML configuration, or runtime override layer. `WALLET_KEY` selects **single-wallet mode**, while `WALLETS_FILE=wallets.json` selects **multiple-wallet mode** and loads that manifest. If both remain present, only `SPONSORED=true` resolves the conflict by selecting the manifest; self-funded configuration still requires one wallet source. Unknown or duplicate settings are rejected so misspellings cannot silently change behavior.

### Required settings

| Setting | Example | Purpose |
| --- | --- | --- |
| `WALLET_KEY` | `0x...` | One of `WALLET_KEY` or `WALLETS_FILE` is required; this key selects **single-wallet mode** |
| `WALLETS_FILE` | `wallets.json` | One of `WALLET_KEY` or `WALLETS_FILE` is required; this strict version-1 manifest selects **multiple-wallet mode** |
| `RPC_URL` | `https://...` | Required RPC endpoint for every network command; `wallets create` is the only command that does not load `.env` |
| `FEE_AUTOMATIC` | `true` | Required Boolean fee-mode selection; `true` is the value supplied by `.env.example` |
| `GAS_LIMIT` | `300000` | Required nonzero mint-call gas allowance per wallet; `300000` is the value supplied by `.env.example` |

The chain ID is read from `RPC_URL`; the user does not configure a separate chain name or chain ID. Non-HTTPS RPC URLs are rejected except for local loopback development endpoints.

### Fee and transaction settings

| Setting | Default | Purpose |
| --- | ---: | --- |
| `MAX_FEE_PER_GAS_GWEI` | unset | Required manual maximum fee when automatic fees are disabled |
| `MAX_PRIORITY_FEE_PER_GAS_GWEI` | unset | Required manual priority fee when automatic fees are disabled |
| `TRANSACTION_MAX_ATTEMPTS` | `3` | Maximum initial submission and same-nonce replacement attempts; range `1-10` |
| `PENDING_TIMEOUT_SECONDS` | `20` | Time before a pending transaction becomes eligible for replacement; range `1-86400` |
| `RECEIPT_POLL_BASE_DELAY_MS` | `250` | Initial receipt polling delay; range `50-60000` |
| `RECEIPT_POLL_MAX_DELAY_MS` | `2000` | Maximum receipt polling delay; range `50-60000` and not below the initial delay |
| `REPLACEMENT_BUMP_BPS` | `11250` | Replacement fee factor; range `10001-20000` basis points (`11250` means 112.5%) |

### Scheduling and request settings

| Setting | Default | Purpose |
| --- | ---: | --- |
| `SCHEDULE_REFRESH_INTERVAL_SECONDS` | `600` | Metadata and eligibility refresh interval for selected phases; range `10-86400` seconds |
| `OPENSEA_REQUEST_TIMEOUT_MS` | `10000` | General OpenSea request timeout; range `100-120000` ms |
| `ELIGIBILITY_REQUEST_TIMEOUT_MS` | `5000` | Eligibility request timeout; range `100-120000` ms |
| `OPENSEA_MAX_ATTEMPTS` | `3` | Maximum attempts for transient metadata, authentication, eligibility, and pre-launch private-stage probes; range `1-10` |
| `OPENSEA_RETRY_INTERVAL_MS` | `250` | Fixed delay between retryable OpenSea requests, including calldata; range `50-30000` ms |
| `OPENSEA_CALLDATA_MAX_ATTEMPTS` | `40` | Maximum T-2 calldata requests for not-ready, transient, malformed, or locally inconsistent actions; range `1-1000` |

### Multi-wallet settings

| Setting | Mode | Purpose |
| --- | --- | --- |
| `SPONSORED` | **Multiple wallets** | Required Boolean: `true` selects **sponsored EIP-7702 mode**; `false` selects up to 10 concurrent wallets in **self-funded mode** |
| `RECIPIENT_ADDRESS` | **Multiple wallets** | Receives every minted NFT; may be omitted only when `SPONSOR_KEY` supplies the fallback and must differ from the sponsored executor |
| `SPONSOR_KEY` | **Sponsored/deployment/funding/fallback** | Pays only outer transaction gas in sponsored mints; also pays executor deployment, undelegation, and `opensea-mint mint --fund`, and may supply the fallback recipient |
| `SPONSORED_EXECUTOR_ADDRESS` | **Sponsored** | Required by sponsored mint and `doctor`; `deploy-executor` can calculate and print it when unset, and its runtime is verified before use |
| `SPONSORED_OPERATION_DEADLINE_SECONDS` | **Sponsored** | Wallet mint-signature validity window; default `120`, range `30-3600` seconds |

### `.env` discovery

When an uninstalled binary inside the project tree is launched from a parent directory, the program searches upward from the binary location first. This prevents an unrelated parent `.env` from shadowing the project file. The installed `opensea-mint` command searches the launch directory and its parents.

---

<div align="center">
  <h2>Disclaimer and License</h2>
  <p><strong>Use this software entirely at your own risk.</strong> It uses an unaudited EIP-7702 executor smart contract and OpenSea's private internal API, which may change, become incompatible, or stop working at any time. Blockchain transactions are irreversible and may result in loss of funds or digital assets.</p>
  <p>The software is provided "as is" without warranties of any kind. To the maximum extent permitted by law, the author and contributors will not be liable for any direct, indirect, incidental, consequential, financial, technical, or other loss, damage, injury, or harm arising from use of, inability to use, or reliance on this software.</p>
  <p>The project is distributed under the <a href="LICENSE">MIT License</a>.</p>
</div>
