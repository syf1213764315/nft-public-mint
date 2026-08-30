# Driving both CLIs non-interactively (pipe + bot wrapper)

Both `nft-public-mint` (Node) and `opensea-mint` (Rust) read their wizards as
**plain line-based stdin** — NOT raw-mode TUI / arrow-key menus. This means you can
drive them headless with a piped stdin or from a Node `child_process` — no
`node-pty`, no `script(1)`, no PTY allocation required.

## npm-start (nft-public-mint) — exact prompt order

The wizard's `ask()` layer buffers stdin into a queue when not a TTY, so piping
every answer as separate lines works. Prompt order (from `src/wizard.ts`):

```
<private key 1>
<private key 2>
(blank line)          <- ends the key list
<chain>              <- ethereum | base | robinhood
<quantity>           <- NFT per wallet
<nft link>           <- OpenSea link / slug / raw 0x address
<rpc>                <- BLANK auto-resolves from chain (safe to leave empty)
<gas ceiling>        <- BLANK = default
<gas tip>           <- BLANK = default
<timing>            <- "now" fires immediately; otherwise waits for stage
```

Confirmed-safe piped answer set (key + defaults):

```bash
cd ~/.hermes/skills/nft-public-mint
printf '%s\n' "$PK" '' 'base' '1' '0x<NFT-ADDRESS>' '' '' '' 'now' | npm start
```

Key gotchas:
- The key-list blank terminator consumes a line; do not skip it.
- Blank RPC does NOT prompt again — it auto-resolves from `CHAIN` and is harmless.
- Extra blank answers are harmless; fewer answers stalls the wizard until stdin closes.
- stdout transcript echoes your scripted answers (the `ask()` layer re-echoes when
  stdin is not a TTY) so the transcript is auditable.

## opensea-mint (Rust) — exact prompt order for `mint`

The Rust CLI (`osnm-z`) also uses line-based stdin via `prompt()`. For
`opensea-mint mint` the sequence is:

```
<collection slug / URL / 0x address>   <- "Mint target:"
(blank)                                  <- "Phases:" — blank auto-selects when exactly one phase is selectable
y                                        <- "Answer [y/N]:" confirm
```

```bash
cd ~/nft-mint
printf '%s\n' 'my-collection' '' 'y' | opensea-mint mint
```

- If more than one phase is selectable, the blank `Phases:` answer is rejected
  with a warning loop — you must instead pass committed-separated phase numbers
  (e.g. `1,2`), then optionally a "Token ID:" answer per phase if the token range
  is non-singular. `select_phases()` auto-picks when exactly one selectable,
  otherwise it loops until input parses.
- Every other `opensea-mint` command (`doctor`, `wallets create`,
  `calldata`, `mint --fund`, `mint --withdraw`, `mint --undelegate`,
  `deploy-executor`) is NON-interactive — no stdin needed, just run it.

## Telegram bot wrapper pattern

A Telegraf bot can expose these CLIs as commands by spawning `child_process` and
streaming stdout back to chat. Reusable shape (see `~/nft-mint-bot/` for the
full working example):

- `spawner.js` exports helpers: `run(cmd, args, {cwd,env,timeoutMs})` (capture
  stdout+stderr, resolve on close, SIGKILL on timeout) and
  `runInteractive(cmd,args,answers,{...})` (pipe answers one line each into
  `child.stdin`, stream output, end stdin 300ms after answers run out).
- Cap output at ~4000 chars per message (Telegram message limit ~4096).
- `ALLOWED_USERS` env gate so only the operator's chat id can run commands.
- Gate key hygiene: prefer `WALLET_KEY` in the bot `.env` over passing private
  keys through chat; warn but still allow inline key if the operator insists.
- Rust `opensea-mint` binary path: `~/.local/bin/opensea-mint`; config dir
  defaults to `~/nft-mint/` (has `manifest.json` + its own `.env`).

## Pitfall: node --check core-dumps on this host at exit

On the GCP VPS, `/usr/bin/node` (v22) AND `node --check` abort with
`core dumped` **at process exit** — even a trivial `node -e "console.log('hi')"`
prints the output then aborts with exit 134. This is noise, NOT a syntax error.
Verify syntax instead with `node -e "require('./file.js')"` (loads the module and
reports real syntax errors) or just run the file. First `node` on PATH is
`~/node-v20.20.2-linux-x64/bin/node` (v20) — prefer that for running bots.
