// Build SeaDrop public-mint calldata locally, with no OpenSea involvement.
//
// A public stage is unsigned: SeaDrop.mintPublic() takes only the drop's own
// parameters, so the whole transaction can be assembled from on-chain reads.
// That removes the access token, its expiry, OpenSea's rate limits, and — the
// part that actually matters for FCFS — the ~1s API round-trip from the
// critical path, because every tx can be signed before the stage even opens.
//
// The allowlist/FCFS stage is different in kind: mintSigned() carries a
// server-produced signature bound to one wallet, so that path still needs
// OpenSea and there is no local equivalent.

import { Contract, Interface, JsonRpcProvider, keccak256 } from "ethers";

export const SEADROP_ADDRESS = "0x00005EA00Ac477B1030CE78506496e8C2dE24bf5";

// Keccak-256 hashes of the canonical OpenSea SeaDrop singleton runtime bytecode
// measured per chain (2026-08-30). The bytecode differs across chains because
// the embedded solc metadata block is chain/compiler-specific, so a single
// global hash is wrong — we keep an allowlist of known-good hashes and their
// measured code length. Any chain whose hash is NOT in this list is either a
// lookalike/scam mirror (C1 in full-skill review) or an unverified SeaDrop
// build — both must fail loudly unless explicitly overridden.
export const SEADROP_KNOWN_HASHES: { hash: string; codeLen: number; chain: string }[] = [
  {
    hash: "7200e8ad8178b88c4f40b7562834b163c961cac853dac50d209c82e27775f981",
    codeLen: 21081,
    chain: "ethereum (mainnet)",
  },
  {
    hash: "53e4b9339cf624803c9a7d0195576cca5b917920813508d86b3eb93dcbabeb5c",
    codeLen: 21081,
    chain: "robinhood (4663)",
  },
];

// Env override: set SEADROP_ALLOW_UNVERIFIED=1 to skip the hash allowlist check
// (still requires non-empty code of plausible length). For chains whose SeaDrop
// build we have not yet measured — use only after verifying the bytecode yourself.
function allowUnverified(): boolean {
  return (process.env.SEADROP_ALLOW_UNVERIFIED || "").trim() === "1";
}

// Minimum plausible SeaDrop runtime length (bytes) — a deployed singleton is
// ~21KB on every chain; anything drastically shorter is not the canonical
// implementation even if the hash check were to be skipped.
const SEADROP_MIN_CODE_LEN = 4096;

// Verify the SeaDrop singleton on a given RPC actually hosts a canonical
// OpenSea SeaDrop implementation. Throws on mismatch / empty code so the
// caller can refuse to build mint calldata against a lookalike.
export async function verifySeaDrop(rpcUrl: string): Promise<void> {
  const provider = new JsonRpcProvider(rpcUrl);
  const code = await provider.getCode(SEADROP_ADDRESS);
  if (!code || code === "0x") {
    throw new Error(
      `No contract at SeaDrop singleton ${SEADROP_ADDRESS} on this chain — refusing to mint against an empty address`
    );
  }
  const bytes = code.startsWith("0x") ? code.slice(2) : code;
  const len = bytes.length / 2;
  if (len < SEADROP_MIN_CODE_LEN) {
    throw new Error(
      `SeaDrop singleton at ${SEADROP_ADDRESS} is only ${len} bytes (expected ~21081) — likely a lookalike contract, refusing to mint`
    );
  }
  const hash = keccak256(code).slice(2);
  const known = SEADROP_KNOWN_HASHES.find((k) => k.hash === hash);
  if (!known && !allowUnverified()) {
    throw new Error(
      `SeaDrop singleton at ${SEADROP_ADDRESS} has unverified runtime hash ${hash} (${len} bytes) — not in known-good allowlist. ` +
        `If you verified this bytecode yourself, set SEADROP_ALLOW_UNVERIFIED=1.`
    );
  }
}

// OpenSea's standard fee collector — the usual allowed recipient on their drops.
// Only used when a drop leaves the recipient list empty and unrestricted, since
// SeaDrop rejects the zero address outright.
const OPENSEA_FEE_RECIPIENT = "0x0000a26b00c1F0DF003000390027140000fAa719";

const PUBLIC_ABI = [
  "function mintPublic(address nftContract, address feeRecipient, address minterIfNotPayer, uint256 quantity) payable",
  "function getPublicDrop(address nftContract) view returns (tuple(uint80 mintPrice, uint48 startTime, uint48 endTime, uint16 maxTotalMintableByWallet, uint16 feeBps, bool restrictFeeRecipients))",
  "function getAllowedFeeRecipients(address nftContract) view returns (address[])",
];

const IFACE = new Interface(PUBLIC_ABI);

export interface PublicDrop {
  mintPrice: bigint;
  startTime: number;
  endTime: number;
  maxTotalMintableByWallet: number;
  feeBps: number;
  restrictFeeRecipients: boolean;
}

export interface LocalMintPlan {
  to: string; // always the SeaDrop singleton
  data: string; // identical for every wallet — see minterIfNotPayer below
  value: bigint; // mintPrice × quantity, exactly what SeaDrop expects
  drop: PublicDrop;
  feeRecipient: string;
}

// Returns null when this contract has no public drop on the SeaDrop singleton —
// either it isn't a SeaDrop collection at all, or it uses a newer variant that
// keeps drop config on the token contract itself.
export async function fetchPublicDrop(
  rpcUrl: string,
  nftContract: string
): Promise<PublicDrop | null> {
  const provider = new JsonRpcProvider(rpcUrl);
  const seadrop = new Contract(SEADROP_ADDRESS, PUBLIC_ABI, provider);

  try {
    const raw = await seadrop.getPublicDrop(nftContract);
    const drop: PublicDrop = {
      mintPrice: BigInt(raw.mintPrice),
      startTime: Number(raw.startTime),
      endTime: Number(raw.endTime),
      maxTotalMintableByWallet: Number(raw.maxTotalMintableByWallet),
      feeBps: Number(raw.feeBps),
      restrictFeeRecipients: Boolean(raw.restrictFeeRecipients),
    };
    // An unset mapping entry decodes to all zeros rather than reverting.
    if (drop.startTime === 0 && drop.endTime === 0 && drop.maxTotalMintableByWallet === 0) {
      return null;
    }
    return drop;
  } catch {
    return null;
  }
}

// SeaDrop reverts on a zero fee recipient, and on a disallowed one when the drop
// restricts them — so this has to come from the chain, not a guess.
export async function resolveFeeRecipient(
  rpcUrl: string,
  nftContract: string,
  restricted: boolean
): Promise<{ address: string; source: string } | null> {
  const provider = new JsonRpcProvider(rpcUrl);
  const seadrop = new Contract(SEADROP_ADDRESS, PUBLIC_ABI, provider);

  let allowed: string[] = [];
  try {
    allowed = await seadrop.getAllowedFeeRecipients(nftContract);
  } catch {
    allowed = [];
  }

  if (allowed.length > 0) {
    return { address: allowed[0], source: "allowed fee recipient on-chain" };
  }
  if (restricted) {
    // Nothing allowed and the drop enforces the list — a public mint cannot be
    // constructed at all, locally or otherwise.
    return null;
  }
  return { address: OPENSEA_FEE_RECIPIENT, source: "OpenSea default (drop does not restrict)" };
}

// minterIfNotPayer = address(0) means "credit the caller", so the calldata is
// byte-identical for every wallet and can be encoded once and shared.
export function encodeMintPublic(
  nftContract: string,
  feeRecipient: string,
  quantity: number
): string {
  return IFACE.encodeFunctionData("mintPublic", [
    nftContract,
    feeRecipient,
    "0x0000000000000000000000000000000000000000",
    BigInt(quantity),
  ]);
}

export async function buildLocalMintPlan(
  rpcUrl: string,
  nftContract: string,
  quantity: number
): Promise<LocalMintPlan | null> {
  // C1: refuse to build calldata against a lookalike / empty SeaDrop address.
  await verifySeaDrop(rpcUrl);

  const drop = await fetchPublicDrop(rpcUrl, nftContract);
  if (!drop) return null;

  const fee = await resolveFeeRecipient(rpcUrl, nftContract, drop.restrictFeeRecipients);
  if (!fee) return null;

  return {
    to: SEADROP_ADDRESS,
    data: encodeMintPublic(nftContract, fee.address, quantity),
    value: drop.mintPrice * BigInt(quantity),
    drop,
    feeRecipient: fee.address,
  };
}
