import { JsonRpcProvider, Wallet, formatEther, getAddress, isAddress } from "ethers";
import { CHAINS, ChainProfile, resolveChain } from "./chains";
import { parseNftLink } from "./nft-link";
import { resolveSlug } from "./slug-resolver";
import {
  maskRpc,
  planRpcs,
  privateRpcsFromEnv,
  resolveRpcsForChain,
  toRpcUrl,
  verifyChainId,
  RpcPlan,
} from "./rpc-resolver";
import { parseRpcEndpoints } from "./rpc-blast";
import { buildLocalMintPlan, LocalMintPlan } from "./seadrop-public";
import { istTimeToDate, toIST } from "./time-format";

export interface MintRequest {
  keys: string[];
  chainKey: string;
  quantity: number;
  nftLink: string;
  rpc?: string;
  maxFeeGwei?: number;
  priorityGwei?: number;
  gasLimit?: number;
  timing: "wait" | "now" | "custom";
  customTime?: string;
  continueUnverifiedRpc?: boolean;
}

export interface PreparedWallet {
  index: number;
  address: string;
  balanceEth: string | null;
  funded: boolean;
}

export interface PreparedMint {
  walletKeys: string[];
  chain: ChainProfile;
  quantity: number;
  nftContract: string;
  label: string;
  rpcUrls: string[];
  rpcPlan: RpcPlan;
  mintPlan: LocalMintPlan;
  maxFeeGwei: number;
  priorityGwei: number;
  maxFeePerGas: bigint;
  maxPriorityFee: bigint;
  gasLimit: number;
  baseFeeGwei: number | null;
  targetStart: Date | null;
  timingLabel: string;
  wallets: PreparedWallet[];
  warnings: string[];
  canFire: boolean;
  blockReason?: string;
}

export class PrepareError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PrepareError";
  }
}

export function parseWalletKeys(rawKeys: string[]): { keys: string[]; addresses: string[] } {
  const keys: string[] = [];
  const addresses: string[] = [];
  const seen = new Set<string>();

  for (const raw of rawKeys) {
    const trimmed = (raw || "").trim();
    if (!trimmed) continue;
    const normalized = trimmed.startsWith("0x") ? trimmed : `0x${trimmed}`;
    let wallet: Wallet;
    try {
      wallet = new Wallet(normalized);
    } catch {
      throw new PrepareError("Not a valid private key — check the hex and try again.");
    }
    const addr = wallet.address.toLowerCase();
    if (seen.has(addr)) continue;
    seen.add(addr);
    keys.push(normalized);
    addresses.push(wallet.address);
  }

  if (keys.length === 0) {
    throw new PrepareError("Need at least one private key.");
  }
  return { keys, addresses };
}

export function normalizeAddress(
  raw: string
): { address: string; checksumWarning: boolean } | null {
  const value = raw.trim();
  if (!/^0x[0-9a-fA-F]{40}$/.test(value)) return null;
  const body = value.slice(2);
  const mixedCase = /[a-f]/.test(body) && /[A-F]/.test(body);
  return {
    address: getAddress(value.toLowerCase()),
    checksumWarning: mixedCase && !isAddress(value),
  };
}

export function gweiToWei(gwei: number): bigint {
  return BigInt(Math.round(gwei * 1e9));
}

export function formatRemaining(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export async function currentBaseFeeGwei(provider: JsonRpcProvider): Promise<number | null> {
  try {
    const fee = await provider.getFeeData();
    const wei = fee.gasPrice ?? fee.maxFeePerGas;
    return wei === null || wei === undefined ? null : Number(wei) / 1e9;
  } catch {
    return null;
  }
}

async function resolveTarget(
  nftLink: string,
  chainKey: string
): Promise<{ contract: string; label: string; chainKey: string; warnings: string[] }> {
  const warnings: string[] = [];
  let activeChain = chainKey;
  let parsed;
  try {
    parsed = parseNftLink(nftLink);
  } catch (err: unknown) {
    throw new PrepareError(err instanceof Error ? err.message : String(err));
  }

  if (parsed.chainHint && parsed.chainHint !== activeChain && resolveChain(parsed.chainHint)) {
    const hinted = resolveChain(parsed.chainHint)!;
    warnings.push(`Link points at ${hinted.name}; switched chain from ${resolveChain(activeChain)?.name}.`);
    activeChain = hinted.key;
  }

  if (parsed.kind === "address") {
    const normalized = normalizeAddress(parsed.value);
    if (!normalized) {
      throw new PrepareError(`"${parsed.value}" is not a 20-byte address.`);
    }
    if (normalized.checksumWarning) {
      warnings.push("Mixed-case address whose EIP-55 checksum doesn't match — likely a typo.");
    }
    return {
      contract: normalized.address,
      label: short(normalized.address),
      chainKey: activeChain,
      warnings,
    };
  }

  const apiKey = (process.env.OPENSEA_API_KEY || "").trim();
  try {
    const info = await resolveSlug(parsed.value, apiKey || undefined, activeChain);
    const resolved = normalizeAddress(info.contractAddress);
    if (!resolved) {
      throw new PrepareError(`Unusable address returned: ${info.contractAddress}`);
    }
    if (info.chain && resolveChain(info.chain) && info.chain !== activeChain) {
      warnings.push(`Collection listed on "${info.chain}", not "${activeChain}". Switched.`);
      activeChain = resolveChain(info.chain)!.key;
    }
    return {
      contract: resolved.address,
      label: info.name || parsed.value,
      chainKey: activeChain,
      warnings,
    };
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new PrepareError(
      `${msg} Paste the contract address (0x…) instead — that always works, no API key needed.`
    );
  }
}

function parseManualRpcs(raw: string | undefined, chainKey: string): string[] {
  if (!raw || !raw.trim()) return [];
  const parts = raw.split(",").map((s) => s.trim()).filter(Boolean);
  const urls: string[] = [];
  for (const part of parts) {
    const url = toRpcUrl(part, chainKey);
    if (!url) {
      throw new PrepareError(`"${part}" is not a URL or a usable Alchemy API key.`);
    }
    urls.push(url);
  }
  return urls;
}

function resolveTiming(
  timing: MintRequest["timing"],
  customTime: string | undefined,
  startTime: number
): { targetStart: Date | null; timingLabel: string; warnings: string[] } {
  const warnings: string[] = [];
  const startsInFuture = startTime * 1000 > Date.now();
  const at = new Date(startTime * 1000);

  if (timing === "wait") {
    if (!startsInFuture) {
      return { targetStart: null, timingLabel: "stage already live — fire immediately", warnings };
    }
    return {
      targetStart: at,
      timingLabel: `wait for stage — ${toIST(at)} IST`,
      warnings,
    };
  }

  if (timing === "now") {
    if (startsInFuture) {
      throw new PrepareError(
        `Stage is not open yet (${toIST(at)} IST). Firing now would revert with NotActive. Choose “Wait for stage”.`
      );
    }
    return { targetStart: null, timingLabel: "fire immediately", warnings };
  }

  if (!customTime) {
    throw new PrepareError("Custom time requires HH:MM (24-hour IST).");
  }
  const custom = istTimeToDate(customTime);
  if (custom.getTime() < startTime * 1000) {
    warnings.push(
      `Custom time is before the stage opens (${toIST(at)} IST) — the mint will revert with NotActive.`
    );
  }
  return { targetStart: custom, timingLabel: `custom — ${toIST(custom)} IST`, warnings };
}

export async function prepareMint(req: MintRequest): Promise<PreparedMint> {
  const warnings: string[] = [];
  const { keys: walletKeys } = parseWalletKeys(req.keys);

  const quantity = Math.floor(Number(req.quantity));
  if (!Number.isFinite(quantity) || quantity < 1 || quantity > 100) {
    throw new PrepareError("Quantity must be between 1 and 100.");
  }

  let chainKey = (req.chainKey || "base").trim().toLowerCase();
  if (!resolveChain(chainKey)) {
    throw new PrepareError(`Unknown chain "${req.chainKey}".`);
  }

  const target = await resolveTarget((req.nftLink || "").trim(), chainKey);
  warnings.push(...target.warnings);
  chainKey = target.chainKey;
  const chain = resolveChain(chainKey)!;

  const manualRpcs = parseManualRpcs(req.rpc, chainKey);
  const { urls: candidateRpcs } = resolveRpcsForChain(chainKey, manualRpcs);
  const rpcPlan = await planRpcs(candidateRpcs, chain.chainId);

  if (rpcPlan.urls.length === 0) {
    throw new PrepareError(`No usable RPC endpoint for ${chain.name}`);
  }

  if (!rpcPlan.verified) {
    const actualChainId = await verifyChainId(rpcPlan.urls[0]);
    if (actualChainId !== null && actualChainId !== chain.chainId) {
      const wrong = resolveChain(actualChainId);
      throw new PrepareError(
        `Chain mismatch: selected ${chain.name} (${chain.chainId}) but RPC ${maskRpc(rpcPlan.urls[0])} reports ` +
          `chain ${actualChainId}${wrong ? ` (${wrong.name})` : ""}. Refusing to sign against the wrong network.`
      );
    }
    warnings.push(`No endpoint confirmed chain id ${chain.chainId}.`);
    if (!req.continueUnverifiedRpc) {
      throw new PrepareError(
        `Could not verify the RPC chain id (${chain.chainId}). Tick “continue anyway” only if you trust these endpoints.`
      );
    }
  }

  const rpcUrls = rpcPlan.urls;
  const mintPlan = await buildLocalMintPlan(rpcUrls[0], target.contract, quantity);
  if (!mintPlan) {
    throw new PrepareError(
      `No SeaDrop public drop readable for ${target.contract} on ${chain.name}. ` +
        "Either it isn't a SeaDrop collection, or it keeps drop config on the token contract."
    );
  }

  const drop = mintPlan.drop;
  const startsAt = new Date(drop.startTime * 1000);
  const endsAt = new Date(drop.endTime * 1000);

  if (drop.maxTotalMintableByWallet > 0 && quantity > drop.maxTotalMintableByWallet) {
    warnings.push(
      `This drop allows only ${drop.maxTotalMintableByWallet} per wallet — ${quantity} will revert.`
    );
  }
  if (Date.now() >= endsAt.getTime()) {
    warnings.push("This public stage has already ended on-chain.");
  }

  const provider = new JsonRpcProvider(rpcUrls[0]);
  const baseFeeGwei = await currentBaseFeeGwei(provider);
  const envMaxFee = Number(process.env.MAX_FEE_PER_GAS || (chainKey === "ethereum" ? 80 : 2));
  const envPriority = Number(process.env.MAX_PRIORITY_FEE || (chainKey === "ethereum" ? 5 : 0.05));

  let defaultMaxFee = envMaxFee;
  if (baseFeeGwei !== null) {
    const suggested = Math.ceil((baseFeeGwei * 2 + envPriority) * 1000) / 1000;
    if (envMaxFee < baseFeeGwei) defaultMaxFee = suggested;
  }

  let maxFeeGwei = Number(req.maxFeeGwei);
  if (!Number.isFinite(maxFeeGwei) || maxFeeGwei <= 0) maxFeeGwei = defaultMaxFee;
  if (baseFeeGwei !== null && maxFeeGwei < baseFeeGwei) {
    throw new PrepareError(
      `Max fee ${maxFeeGwei} gwei is below the current base fee ${baseFeeGwei.toFixed(6)} gwei — every node will reject it.`
    );
  }

  let priorityGwei = Number(req.priorityGwei);
  if (!Number.isFinite(priorityGwei) || priorityGwei < 0) {
    priorityGwei = Math.min(envPriority, maxFeeGwei);
  }
  if (priorityGwei > maxFeeGwei) {
    throw new PrepareError("Priority fee cannot exceed the max fee ceiling (EIP-1559).");
  }

  const maxFeePerGas = gweiToWei(maxFeeGwei);
  const maxPriorityFee = gweiToWei(priorityGwei);
  const gasLimit = Math.floor(Number(req.gasLimit) || parseInt(process.env.GAS_LIMIT || "0", 10) || 250_000);
  if (gasLimit < 21000 || gasLimit > 5_000_000) {
    throw new PrepareError("Gas limit must be between 21000 and 5000000.");
  }

  const timing = resolveTiming(req.timing || "wait", req.customTime, drop.startTime);
  warnings.push(...timing.warnings);

  const wallets = walletKeys.map((k) => new Wallet(k));
  const balances = await Promise.all(
    wallets.map((w) => provider.getBalance(w.address).catch(() => null))
  );
  const required = BigInt(gasLimit) * maxFeePerGas + mintPlan.value;

  const preparedWallets: PreparedWallet[] = wallets.map((w, i) => {
    const bal = balances[i];
    return {
      index: i,
      address: w.address,
      balanceEth: bal === null ? null : Number(formatEther(bal)).toFixed(6),
      funded: bal === null ? true : bal >= required,
    };
  });

  const shortCount = preparedWallets.filter((w) => w.balanceEth !== null && !w.funded).length;
  if (shortCount > 0) {
    warnings.push(
      `${shortCount} wallet(s) hold less than the ${formatEther(required)} ${chain.nativeSymbol} ` +
        `nodes reserve (gasLimit × maxFee${mintPlan.value > 0n ? " + mint price" : ""}).`
    );
  }

  let canFire = true;
  let blockReason: string | undefined;
  if (shortCount === wallets.length) {
    canFire = false;
    blockReason = "Every wallet is underfunded — nothing could be broadcast.";
  }
  if (Date.now() >= endsAt.getTime()) {
    canFire = false;
    blockReason = "This public stage has already ended on-chain.";
  }

  return {
    walletKeys,
    chain,
    quantity,
    nftContract: target.contract,
    label: target.label,
    rpcUrls,
    rpcPlan,
    mintPlan,
    maxFeeGwei,
    priorityGwei,
    maxFeePerGas,
    maxPriorityFee,
    gasLimit,
    baseFeeGwei,
    targetStart: timing.targetStart,
    timingLabel: timing.timingLabel,
    wallets: preparedWallets,
    warnings,
    canFire,
    blockReason,
  };
}

export function serializePreview(prepared: PreparedMint) {
  const drop = prepared.mintPlan.drop;
  const startsAt = new Date(drop.startTime * 1000);
  const endsAt = new Date(drop.endTime * 1000);
  const live = Date.now() >= startsAt.getTime() && Date.now() < endsAt.getTime();
  const envRpcs = privateRpcsFromEnv(prepared.chain.key);

  return {
    ok: true,
    canFire: prepared.canFire,
    blockReason: prepared.blockReason ?? null,
    chain: {
      key: prepared.chain.key,
      name: prepared.chain.name,
      chainId: prepared.chain.chainId,
      symbol: prepared.chain.nativeSymbol,
      explorer: prepared.chain.explorer,
    },
    contract: prepared.nftContract,
    label: prepared.label,
    quantity: prepared.quantity,
    totalMints: prepared.quantity * prepared.wallets.length,
    rpcs: parseRpcEndpoints(prepared.rpcUrls).map((ep, i) => ({
      label: ep.label,
      masked: maskRpc(ep.url),
      role: prepared.rpcPlan.sendOnly.includes(ep.url)
        ? "send"
        : i === 0
          ? "read"
          : "blast",
    })),
    envRpcHint: envRpcs.map(maskRpc),
    drop: {
      feeRecipient: prepared.mintPlan.feeRecipient,
      pricePerNft: formatEther(drop.mintPrice),
      valuePerWallet: formatEther(prepared.mintPlan.value),
      valueTotal: formatEther(prepared.mintPlan.value * BigInt(prepared.wallets.length)),
      maxPerWallet: drop.maxTotalMintableByWallet || 0,
      startTime: drop.startTime,
      endTime: drop.endTime,
      startsAtIso: startsAt.toISOString(),
      endsAtIso: endsAt.toISOString(),
      windowIst: `${toIST(startsAt)} → ${toIST(endsAt)} IST`,
      live,
      opensInMs: Math.max(0, startsAt.getTime() - Date.now()),
    },
    gas: {
      baseFeeGwei: prepared.baseFeeGwei,
      maxFeeGwei: prepared.maxFeeGwei,
      priorityGwei: prepared.priorityGwei,
      gasLimit: prepared.gasLimit,
      requiredEth: formatEther(BigInt(prepared.gasLimit) * prepared.maxFeePerGas + prepared.mintPlan.value),
    },
    wallets: prepared.wallets,
    timing: {
      mode: prepared.targetStart ? "wait" : "now",
      fireAt: prepared.targetStart ? prepared.targetStart.toISOString() : null,
      label: prepared.timingLabel,
    },
    warnings: prepared.warnings,
  };
}

export function chainCatalog() {
  return CHAINS.map((c) => ({
    key: c.key,
    name: c.name,
    chainId: c.chainId,
    symbol: c.nativeSymbol,
    alchemyHost: c.rpc.alchemyHost ?? null,
    hasEnvRpc: privateRpcsFromEnv(c.key).length > 0,
  }));
}

function short(addr: string): string {
  return addr.length > 12 ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr;
}
