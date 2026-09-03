import chalk from "chalk";
import ora from "ora";

export interface WaitForMintTimeOpts {
  onTick?: (remainingMs: number) => void;
  signal?: AbortSignal;
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    const err = new Error("Aborted — mint wait cancelled");
    err.name = "AbortError";
    throw err;
  }
}

export async function waitForMintTime(
  mintTime: Date,
  earlyFireMs: number = 0,
  opts: WaitForMintTimeOpts = {}
): Promise<void> {
  // Fire early by earlyFireMs — tx sits in mempool and lands the moment contract allows
  const fireTime = new Date(mintTime.getTime() - earlyFireMs);
  const now = new Date();
  const diff = fireTime.getTime() - now.getTime();

  if (diff <= 0) {
    console.log(chalk.yellow("  Fire time already passed — sending immediately."));
    return;
  }

  console.log(chalk.bold.white(`\n⏰ Mint time: ${mintTime.toISOString()}`));
  if (earlyFireMs > 0) {
    console.log(chalk.bold.yellow(`  🔥 Early fire: ${earlyFireMs}ms before mint → firing at ${fireTime.toISOString()}`));
  }
  console.log(chalk.gray(`  Now: ${now.toISOString()} | Waiting ${Math.ceil(diff / 1000)}s...\n`));
  opts.onTick?.(diff);

  const useSpinner = !opts.onTick && Boolean(process.stdout.isTTY) && diff > 10000;

  // If more than 10 seconds away, show a countdown spinner
  if (diff > 10000) {
    const spinner = useSpinner
      ? ora({
          text: formatCountdown(fireTime),
          color: "cyan",
        }).start()
      : null;

    await new Promise<void>((resolve, reject) => {
      const interval = setInterval(() => {
        try {
          throwIfAborted(opts.signal);
        } catch (err) {
          clearInterval(interval);
          spinner?.stop();
          reject(err);
          return;
        }
        const remaining = fireTime.getTime() - Date.now();
        opts.onTick?.(Math.max(0, remaining));

        if (remaining <= 5000) {
          clearInterval(interval);
          spinner?.stop();
          resolve();
        } else if (spinner) {
          spinner.text = formatCountdown(fireTime);
        }
      }, 500);

      if (opts.signal) {
        const onAbort = () => {
          clearInterval(interval);
          spinner?.stop();
          const err = new Error("Aborted — mint wait cancelled");
          err.name = "AbortError";
          reject(err);
        };
        opts.signal.addEventListener("abort", onAbort, { once: true });
      }
    });
  }

  // Precise wait for the last few seconds using a tight loop
  throwIfAborted(opts.signal);
  const remaining = fireTime.getTime() - Date.now();
  if (remaining > 0) {
    if (remaining > 100) {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(resolve, remaining - 100);
        if (!opts.signal) return;
        opts.signal.addEventListener(
          "abort",
          () => {
            clearTimeout(timer);
            const err = new Error("Aborted — mint wait cancelled");
            err.name = "AbortError";
            reject(err);
          },
          { once: true }
        );
      });
    }

    // Tight spin-wait for the final milliseconds
    while (Date.now() < fireTime.getTime()) {
      throwIfAborted(opts.signal);
    }
  }

  opts.onTick?.(0);
  console.log(chalk.bold.green("  🟢 FIRING!\n"));
}

function formatCountdown(target: Date): string {
  const diff = target.getTime() - Date.now();
  const hours = Math.floor(diff / 3600000);
  const minutes = Math.floor((diff % 3600000) / 60000);
  const seconds = Math.floor((diff % 60000) / 1000);

  if (hours > 0) {
    return `  Waiting... ${hours}h ${minutes}m ${seconds}s remaining`;
  }
  if (minutes > 0) {
    return `  Waiting... ${minutes}m ${seconds}s remaining`;
  }
  return `  Waiting... ${seconds}s remaining`;
}
