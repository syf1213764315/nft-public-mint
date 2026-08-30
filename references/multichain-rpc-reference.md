# Multichain RPC Reference — merged from febfrmn/nft-s (evm_async.py PUBLIC_RPCS, config.py, bot.py) + osnm-z chains.ts

Verified structure 2026-08-25. Patterns:
- `public` = blast list: query-capable + send-only endpoints mixed (send-only kept for fastest inclusion)
- Alchemy template = main RPC used LAST as fallback; `{API_KEY}` placeholder
- `probe_plan()`: probes eth_chainId, keeps matching + send-only (None), DROPS wrong-chain
- SeaDrop singleton `0x00005EA00Ac477B1030CE78506496e8C2dE24bf5` — SAME across all EVM chains

| Chain | ChainID | Public RPCs (blast) | Send-only (blast) | Alchemy template (main) | Explorer |
|-------|---------|--------------------|--------------------|--------------------------|-----------|
| ethereum | 1 | ethereum-rpc.publicnode.com, eth.merkle.io, cloudflare-eth.com, eth.llamarpc.com | — | eth-mainnet.g.alchemy.com/v2/{API_KEY} | https://etherscan.io |
| base | 8453 | mainnet.base.org, base-rpc.publicnode.com | mainnet-sequencer.base.org | base-mainnet.g.alchemy.com/v2/{API_KEY} | https://basescan.org |
| polygon | 137 | polygon-rpc.com, polygon-bor-rpc.publicnode.com | — | polygon-mainnet.g.alchemy.com/v2/{API_KEY} | https://polygonscan.com |
| arbitrum | 42161 | arb1.arbitrum.io/rpc | — | arb-mainnet.g.alchemy.com/v2/{API_KEY} | https://arbiscan.io |
| optimism | 10 | mainnet.optimism.io | — | opt-mainnet.g.alchemy.com/v2/{API_KEY} | https://optimistic.etherscan.io |
| bsc | 56 | bsc-dataseed.binance.org | — | (none in repo) | https://bscscan.com |
| avalanche | 43114 | api.avax.network/ext/bc/C/rpc | — | (none in repo) | https://snowtrace.io |
| robinhood | 4663 | rpc.mainnet.chain.robinhood.com | sequencer.mainnet.chain.robinhood.com | robinhood-mainnet.g.alchemy.com/v2/{API_KEY} | https://robinhoodchain.blockscout.com |
| solana | — | — | — | — |

> C3: solana di-bar dari tabel mint — tool ini EVM-only, tidak ada encoder Solana. Baris dipertahankan hanya sebagai catatan bahwa OpenSea REST (bukan mint) support Solana.

## ⚠ L2 L1-data fee advisory (M1)

Arbitrum, Optimism, dan Base menggunakan mekanisme L1 data fee (calldata posting) di luar EIP-1559 base fee. Estimasi gas lokal (`max_fee = base_fee * 2 + priority`) **underprices** L2 tx karena L1 data fee tidak tercakup.

**Rekomendasi:**
- Default `max_fee = base_fee * 5 + priority` untuk L2 chain (Arbitrum, Optimism, Base)
- Atau gunakan `eth_feeHistory` + `eth_estimateGas` dengan `maxFeePerGas` tinggi (2-5x estimasi normal)
- `max_fee = base_fee * 2 + priority` tetap aman untuk L1 (Ethereum), Polygon, BSC, Avalanche C-Chain, Robinhood

## Local fallbacks per chain (probe priority when network blocks the primary)
- Tablet/regional blocking: official RPCs (rpc.mainnet.chain.robinhood.com, sequencer) TLS-blocked → curl proxy + NodeFlare (`https://rpc.nodeflare.app/robinhood/public`, 1 req/10s) works
- NodeFlare + DRPC (`robinhood.drpc.org`) support eth_chainId; DRPC does NOT support eth_maxPriorityFeePerGas (-32601)
- Blockscout eth-rpc (`{chain}.blockscout.com/api/eth-rpc`) is 429-prone but works for reads

## Probe usage (wl_bypass_probe.py, multichain)
```bash
python3 wl_bypass_probe.py 0x<CONTRACT> --chain base --wallet 0x<WALLET>
python3 wl_bypass_probe.py 0x<CONTRACT> --chain ethereum --wallet 0x<WALLET>
python3 wl_bypass_probe.py 0x<CONTRACT> --chain robinhood --wallet 0x<WALLET>   # default
python3 wl_bypass_probe.py 0x<CONTRACT> https://<custom-rpc> 0x<WALLET>         # explicit RPC
```
