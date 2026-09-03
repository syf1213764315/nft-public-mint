export type MintEvent =
  | { type: "log"; level: "info" | "ok" | "warn" | "err"; message: string }
  | { type: "warming" }
  | { type: "warmed" }
  | { type: "signed"; count: number; ms: number; chainId: string }
  | { type: "waiting"; fireAt: string; remainingMs: number }
  | { type: "firing" }
  | {
      type: "dispatched";
      wallets: { idx: number; address: string; txHash: string }[];
      dispatchMs: number;
      sinceStageMs: number;
    }
  | { type: "rpc"; walletIdx: number; label: string; ok: boolean; message: string }
  | { type: "rejected"; idx: number; reasons: string[] }
  | {
      type: "receipt";
      idx: number;
      address: string;
      txHash: string;
      status: "SUCCESS" | "REVERTED" | "TIMEOUT";
      block?: number;
      position?: number;
      gasUsed?: number;
      explorer?: string | null;
    }
  | { type: "complete"; accepted: number; rejected: number; dispatched: number }
  | { type: "error"; message: string };

export interface MintWalletResult {
  idx: number;
  address: string;
  txHash: string;
  status: "rejected" | "TIMEOUT" | "SUCCESS" | "REVERTED";
  reasons?: string[];
  explorer?: string | null;
  block?: number;
  gasUsed?: number;
}

export interface MintRunResult {
  dispatched: number;
  accepted: number;
  rejected: number;
  wallets: MintWalletResult[];
}
