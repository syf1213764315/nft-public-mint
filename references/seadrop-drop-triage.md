# SeaDrop Drop Triage — Menentukan Jalur Mint Sebelum Nembak

Sebelum mint/snipe koleksi SeaDrop mana pun, probe config on-chain dulu.
Tujuannya: tahu jalur mint apa yang ADA (public/allowlist/token-gated/signed),
harganya berapa, kapan buka, dan apakah ada celah yang bisa dimanfaatkan.

Reusable probe: `scripts/seadrop_probe.py CONTRACT [MODULE] [WALLET]` — dump
semua config dalam satu jalan. Pelajaran di bawah ini dari audit Rekt Tradooor
(RH chain, Aug 2026) — hasilnya: NO exploit path, semua jalur ketutup.

## Selector yang dipakai (SeaDrop module, ABI v2)

| Fungsi | Arti |
|---|---|
| `getPublicDrop(address)` | mintPrice, startTime, endTime, maxTotalMintableByWallet, feeBps, restrictFeeRecipients |
| `getAllowListMerkleRoot(address)` | `0x0` = allowlist MATI total |
| `getTokenGatedAllowedTokens(address)` | `[]` = token-gated MATI |
| `getTokenGatedDrop(address,address)` | per-token stage; revert kalau token bukan allowed |
| `getSigners(address)` | daftar signer untuk signed mint |
| `getSignedMintValidationParams(address,address)` | params per (contract, signer); semua nol = signed MATI utk wallet itu |
| `getActiveStage(address)` | revert = tidak ada stage aktif / stage belum diset |
| `getAllowedFeeRecipients(address)` | daftar fee recipient yang diizinkan |
| `config(address)` | config umum (sering revert di module tertentu — bukan berarti salah, cek selector lain) |
| `totalSupply()` / `maxSupply()` / `owner()` | state koleksi (dipanggil ke NFT contract, bukan module) |

Decode `PublicDrop` (tuple, 6 field):
```
uint80 mintPrice, uint48 startTime, uint48 endTime,
uint16 maxTotalMintableByWallet, uint16 feeBps, bool restrictFeeRecipients
```

## Matriks keputusan

- **merkleRoot == 0x0** → allowlist tidak bisa dimint siapa pun. Kalau website bilang
  "allowlist for holders", itu biasanya eligibility via OpenSea/snapshot yang
  DITERJEMAHKAN ke merkle root — kalau root belum diset, jalurnya belum hidup.
- **getTokenGatedAllowedTokens == []** → token-gated mati. Jangan buang waktu cari
  token gate.
- **restrictFeeRecipients == true** → fee recipient terkunci ke daftar
  `getAllowedFeeRecipients`; tidak ada celah redirect fee.
- **getSigners ada + params nol untuk wallet kita** → signed mint ada tapi butuh
  signature dari signer (EIP-712). Signature tidak bisa dipalsukan tanpa private key
  signer. Revert tx orang lain dengan qty besar ≠ signature bocor; digest terikat
  wallet, dan rollback berarti digest belum ditandai used — tapi tetap milik wallet itu.
- **getActiveStage revert** → stage tidak aktif. Konfigurasi bisa berubah sebelum
  jam buka; kalau timing penting, pasang monitor config change, jangan asumsi stage
  yang di-decode dari tx MultiConfigure lama masih valid.

## PITFALL: Eligibility berbasis snapshot

Ini yang paling sering bikin buang waktu. Kalau FAQ/website bilang "holders get X
mints", CEK DULU kapan snapshot-nya. Rekt Tradooor: snapshot 17 Aug 12PM ET, kita
probe 21 Aug — snapshot sudah lewat 4 hari. Beli token gate ($REKT) SEKARANG
tidak ngaruh sama sekali karena saldo diukur di snapshot, bukan live.

Rule: **snapshot sudah lewat + kita tidak pegang aset di snapshot = jalur itu mati,
langsung skip, jangan hitung-hitung harga beli token gate.**

Cara cek: FAQ/scrape website koleksi (cari kata "snapshot"), cek saldo wallet di
chain asal (Blockscout token-balances), bandingkan dengan tanggal snapshot.

## PITFALL: Stage timeline ≠ public drop timeline

Satu koleksi bisa punya beberapa timeline:
- Stage dari tx `MultiConfigure` (decode array stage) — bisa beda jam buka dari
  public drop. Rekt Tradooor: stage 16:00 UTC, public drop 19:15 UTC.
- `getPublicDrop().startTime` adalah sumber kebenaran untuk jalur public.
- Konversi timestamp: `datetime.fromtimestamp(ts, tz=timezone.utc)`.

## PITFALL: Blockscout + OpenSea API

- Blockscout `token-balances?token=***` dengan placeholder → HTTP 422. Jangan pakai
  param `token` kecuali ada address asli; ambil semua token-balance tanpa param.
- `exchange_rate` sering `None` untuk token kecil di chain baru → jangan andalkan
  untuk nilai token.
- OpenSea API v2 `collection/{slug}` → 404 kalau koleksi belum di-index/chain tidak
  didukung penuh; v1 → 410 Gone. Fallback: scrape halaman OpenSea langsung atau
  cek on-chain (totalSupply, floor via marketplace lain).

## Output laporan (format yang dipakai user)

Tidak ada exploit path ≠ tidak ada info. Laporkan:
1. Semua jalur yang dicek + status (mati/terkunci/butuh X)
2. Satu-satunya jalur tersisa (biasanya public paid mint) + harga + jam buka + max/wallet
3. Saldo wallet yang relevan (cukup gak buat paid mint?)
4. Opsi: skip / mint 1 / pasang monitor config change
