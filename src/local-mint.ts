// Public-mint execution with no OpenSea in the loop.
//
// Because the calldata is known ahead of time (see seadrop-public.ts), every
// transaction can be signed and serialised *before* the stage opens. At T-0 the
// only work left is writing bytes to sockets — no API poll, no signing, no
// encoding. That is strictly faster than the OpenSea path, which cannot sign
// until the API hands over calldata roughly a second after the stage starts.

import chalk from "chalk";
import { performance } from "perf_hooks";
import { JsonRpcProvider, Wallet, formatEther } from "ethers";
import { blastToAll, parseRpcEndpoints, prepareBlast, waitForReceipt, PreparedBlast } from "./rpc-blast";
import { warmConnections } from "./connection-warmer";
import { waitForMintTime } from "./timer";
import { explorerTx } from "./chains";
import { LocalMintPlan } from "./seadrop-public";
import { MintEvent, MintRunResult, MintWalletResult } from "./mint-events";

export interface LocalSnipeOpts {
  nftContract: string;
  quantity: number;
  walletKeys: string[];
  rpcUrls: string[];
  maxFeePerGas: bigint;
  maxPriorityFee: bigint;
  gasLimit: number;
  targetStart: Date | null;
  plan: LocalMintPlan;
  onEvent?: (event: MintEvent) => void;
  signal?: AbortSignal;
}

function emit(onEvent: LocalSnipeOpts["onEvent"], event: MintEvent): void {
  onEvent?.(event);
}

export async function localPublicSnipe(opts: LocalSnipeOpts): Promise<MintRunResult> {
  const {
    nftContract, quantity, walletKeys, rpcUrls,
    maxFeePerGas, maxPriorityFee, gasLimit, targetStart, plan,
    onEvent, signal,
  } = opts;

  const provider = new JsonRpcProvider(rpcUrls[0]);
  const endpoints = parseRpcEndpoints(rpcUrls);
  const wallets = walletKeys.map((k) => new Wallet(k, provider));

  console.log(chalk.bold.magenta("\n── LOCAL PUBLIC MINT (no OpenSea) ──"));
  console.log(chalk.gray(`  SeaDrop:       ${plan.to}`));
  console.log(chalk.gray(`  NFT:           ${nftContract}`));
  console.log(chalk.gray(`  Fee recipient: ${plan.feeRecipient}`));
  console.log(
    chalk.gray(
      `  Price:         ${formatEther(plan.drop.mintPrice)} × ${quantity} = ${formatEther(plan.value)} per wallet`
    )
  );
  console.log(chalk.gray(`  Calldata:      ${(plan.data.length - 2) / 2} bytes (identical for every wallet)`));
  emit(onEvent, {
    type: "log",
    level: "info",
    message: `SeaDrop mint · ${nftContract} · ${formatEther(plan.value)} per wallet · ${wallets.length} wallet(s)`,
  });

  // ── Warm sockets and pre-fetch everything the signature depends on ──
  emit(onEvent, { type: "warming" });
  await warmConnections(rpcUrls);
  emit(onEvent, { type: "warmed" });

  const [nonces, network] = await Promise.all([
    Promise.all(wallets.map((w) => provider.getTransactionCount(w.address, "pending"))),
    provider.getNetwork(),
  ]);
  const chainId = network.chainId;
  console.log(chalk.gray(`  Nonces: [${nonces.join(", ")}] | chainId: ${chainId}`));

  if (signal?.aborted) {
    const err = new Error("Aborted — mint cancelled before signing");
    err.name = "AbortError";
    throw err;
  }

  // ── Sign everything now, well before the stage opens ──
  const signStart = performance.now();
  const prepared: { idx: number; address: string; blast: PreparedBlast }[] = [];

  for (let i = 0; i < wallets.length; i++) {
    const rawTx = await wallets[i].signTransaction({
      to: plan.to,
      data: plan.data,
      value: plan.value,
      nonce: nonces[i],
      maxFeePerGas,
      maxPriorityFeePerGas: maxPriorityFee,
      gasLimit: gasLimit || 250_000,
      type: 2,
      chainId,
    });
    prepared.push({ idx: i, address: wallets[i].address, blast: prepareBlast(rawTx) });
  }

  const signMs = performance.now() - signStart;
  console.log(
    chalk.green(
      `  ✓ ${prepared.length} tx(s) signed and serialised in ${signMs.toFixed(1)}ms — nothing left to compute at fire time`
    )
  );
  emit(onEvent, {
    type: "signed",
    count: prepared.length,
    ms: Number(signMs.toFixed(1)),
    chainId: chainId.toString(),
  });

  // ── Wait for the stage, then blast pre-built bytes ──
  if (targetStart) {
    await waitForMintTime(targetStart, 0, {
      signal,
      onTick: (remainingMs) =>
        emit(onEvent, {
          type: "waiting",
          fireAt: targetStart.toISOString(),
          remainingMs,
        }),
    });
  } else {
    console.log(chalk.bold.yellow("\n  🚀 Firing immediately..."));
  }

  if (signal?.aborted) {
    const err = new Error("Aborted — mint cancelled before broadcast");
    err.name = "AbortError";
    throw err;
  }

  emit(onEvent, { type: "firing" });

  const stageStartMs = targetStart ? targetStart.getTime() : Date.now();
  const dispatchStart = performance.now();

  const fired = prepared.map(({ idx, address, blast }) => {
    const { txHash, responsePromise } = blastToAll(blast, endpoints);
    return { idx, address, txHash, responsePromise };
  });

  const dispatchMs = Number((performance.now() - dispatchStart).toFixed(2));
  const sinceStage = Math.max(0, Date.now() - stageStartMs);
  console.log(
    chalk.bold.green(`  DISPATCHED ${fired.length} tx(s) (${dispatchMs}ms, +${sinceStage}ms after stage)`)
  );
  for (const f of fired) {
    console.log(chalk.gray(`    [W${f.idx}] ${f.txHash}`));
  }
  emit(onEvent, {
    type: "dispatched",
    wallets: fired.map((f) => ({ idx: f.idx, address: f.address, txHash: f.txHash })),
    dispatchMs,
    sinceStageMs: sinceStage,
  });

  // Dispatch only means "bytes written". Find out whether any endpoint actually
  // took the transaction before promising a receipt that may never exist.
  const settled = await Promise.all(
    fired.map(async (f) => ({ ...f, results: await f.responsePromise }))
  );

  for (const { idx, results } of settled) {
    for (const r of results) {
      emit(onEvent, {
        type: "rpc",
        walletIdx: idx,
        label: r.label,
        ok: r.txHash !== null || (r.error ?? "").includes("already known"),
        message: r.txHash ?? r.error ?? "unknown",
      });
    }
  }

  const accepted = settled.filter(({ results }) =>
    results.some((r) => r.txHash !== null || (r.error ?? "").includes("already known"))
  );
  const rejected = settled.filter((s) => !accepted.includes(s));

  const walletResults: MintWalletResult[] = [];

  for (const { idx, address, txHash, results } of rejected) {
    const reasons = [...new Set(results.map((r) => r.error).filter((e): e is string => Boolean(e)))];
    console.log(chalk.bold.red(`\n  ✗ [W${idx}] REJECTED by every RPC — never broadcast.`));
    for (const reason of reasons) console.log(chalk.red(`      ${reason}`));
    if (reasons.some((r) => (r ?? "").includes("less than block base fee"))) {
      console.log(chalk.yellow("      → Your max fee is under the chain's base fee. Raise it and re-run."));
    }
    emit(onEvent, { type: "rejected", idx, reasons });
    walletResults.push({ idx, address, txHash, status: "rejected", reasons });
  }

  if (accepted.length === 0) {
    console.log(chalk.bold.red("\n===== NOTHING WAS BROADCAST — no receipts to wait for =====\n"));
    emit(onEvent, { type: "complete", accepted: 0, rejected: rejected.length, dispatched: fired.length });
    return {
      dispatched: fired.length,
      accepted: 0,
      rejected: rejected.length,
      wallets: walletResults,
    };
  }

  // ── Receipts (only for txs an endpoint actually accepted) ──
  console.log(chalk.gray("\n  Waiting for receipts..."));
  emit(onEvent, { type: "log", level: "info", message: "Waiting for receipts..." });
  await Promise.all(
    accepted.map(async ({ idx, address, txHash }) => {
      const receipt = await waitForReceipt(txHash, rpcUrls[0], 60_000);
      const track = explorerTx(chainId, txHash);
      if (!receipt) {
        const url = explorerTx(chainId, txHash);
        console.log(chalk.yellow(`  [W${idx}] TIMEOUT — check: ${url ?? txHash}`));
        emit(onEvent, {
          type: "receipt",
          idx,
          address,
          txHash,
          status: "TIMEOUT",
          explorer: url,
        });
        walletResults.push({ idx, address, txHash, status: "TIMEOUT", explorer: url });
        return;
      }
      const color = receipt.status === "SUCCESS" ? chalk.bold.green : chalk.bold.red;
      console.log(
        color(`  [W${idx}] Block: ${receipt.block} | Pos: ${receipt.position} | ${receipt.status} | Gas: ${receipt.gasUsed}`)
      );
      console.log(chalk.gray(`  [W${idx}] Track: ${track ?? txHash} (no explorer for chain ${chainId})`));
      emit(onEvent, {
        type: "receipt",
        idx,
        address,
        txHash,
        status: receipt.status as "SUCCESS" | "REVERTED",
        block: receipt.block,
        position: receipt.position,
        gasUsed: receipt.gasUsed,
        explorer: track,
      });
      walletResults.push({
        idx,
        address,
        txHash,
        status: receipt.status as "SUCCESS" | "REVERTED",
        explorer: track,
        block: receipt.block,
        gasUsed: receipt.gasUsed,
      });
    })
  );

  console.log(chalk.bold.white("\n===== LOCAL PUBLIC MINT COMPLETE ====="));
  emit(onEvent, {
    type: "complete",
    accepted: accepted.length,
    rejected: rejected.length,
    dispatched: fired.length,
  });
  return {
    dispatched: fired.length,
    accepted: accepted.length,
    rejected: rejected.length,
    wallets: walletResults.sort((a, b) => a.idx - b.idx),
  };
}
