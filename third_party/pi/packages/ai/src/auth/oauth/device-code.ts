const CANCEL_MESSAGE = "Login cancelled";
const TIMEOUT_MESSAGE = "Device flow timed out";
const SLOW_DOWN_TIMEOUT_MESSAGE =
	"Device flow timed out after one or more slow_down responses. This is often caused by clock drift in WSL or VM environments. Please sync or restart the VM clock and try again.";
const MINIMUM_INTERVAL_MS = 1000;
// RFC 8628 section 3.2: if the authorization server omits `interval`, the client must use 5 seconds.
const DEFAULT_POLL_INTERVAL_SECONDS = 5;
// RFC 8628 section 3.5: `slow_down` means the polling interval must increase by 5 seconds.
const SLOW_DOWN_INTERVAL_INCREMENT_MS = 5000;
const MAX_RETRY_INTERVAL_MS = 30_000;
const RETRYABLE_FAILURE_THRESHOLD = 5;

type OAuthDeviceCodeIncompletePollResult =
	| { status: "pending" }
	| { status: "slow_down"; intervalSeconds?: number }
	| { status: "retryable" }
	| { status: "failed"; message: string };

export type OAuthDeviceCodePollResult<T> = OAuthDeviceCodeIncompletePollResult | { status: "complete"; value: T };

export type OAuthDeviceCodePollOptions<T> = {
	intervalSeconds?: number;
	expiresInSeconds?: number;
	waitBeforeFirstPoll?: boolean;
	poll: () => Promise<OAuthDeviceCodePollResult<T>>;
	signal?: AbortSignal;
	setRetryHandler?: (handler: (() => void) | undefined) => void;
	setRetryState?: (state: { consecutiveFailures: number; retryAt: number | null }) => void;
};

function abortableSleep(ms: number, signal: AbortSignal | undefined, cancelMessage: string): Promise<void> {
	return new Promise((resolve, reject) => {
		if (signal?.aborted) {
			reject(new Error(cancelMessage));
			return;
		}

		const onAbort = () => {
			clearTimeout(timeout);
			signal?.removeEventListener("abort", onAbort);
			reject(new Error(cancelMessage));
		};
		const timeout = setTimeout(() => {
			signal?.removeEventListener("abort", onAbort);
			resolve();
		}, ms);

		signal?.addEventListener("abort", onAbort, { once: true });
	});
}

function waitForRetry(
	ms: number,
	signal: AbortSignal | undefined,
	setRetryHandler: OAuthDeviceCodePollOptions<unknown>["setRetryHandler"],
): Promise<boolean> {
	if (!setRetryHandler) return abortableSleep(ms, signal, CANCEL_MESSAGE).then(() => false);

	return new Promise((resolve, reject) => {
		if (signal?.aborted) {
			reject(new Error(CANCEL_MESSAGE));
			return;
		}

		let settled = false;
		const cleanup = () => {
			clearTimeout(timeout);
			signal?.removeEventListener("abort", onAbort);
			setRetryHandler(undefined);
		};
		const finish = (manual: boolean) => {
			if (settled) return;
			settled = true;
			cleanup();
			resolve(manual);
		};
		const onAbort = () => {
			if (settled) return;
			settled = true;
			cleanup();
			reject(new Error(CANCEL_MESSAGE));
		};
		const timeout = setTimeout(() => finish(false), ms);
		signal?.addEventListener("abort", onAbort, { once: true });
		setRetryHandler(() => finish(true));
	});
}

export async function pollOAuthDeviceCodeFlow<T>(options: OAuthDeviceCodePollOptions<T>): Promise<T> {
	const deadline =
		typeof options.expiresInSeconds === "number"
			? Date.now() + options.expiresInSeconds * 1000
			: Number.POSITIVE_INFINITY;
	let intervalMs = Math.max(
		MINIMUM_INTERVAL_MS,
		Math.floor((options.intervalSeconds ?? DEFAULT_POLL_INTERVAL_SECONDS) * 1000),
	);
	let retryIntervalMs = intervalMs;
	let slowDownResponses = 0;
	let consecutiveFailures = 0;
	const setRetryState = (state: { consecutiveFailures: number; retryAt: number | null }) => {
		options.setRetryState?.(state);
	};

	if (options.waitBeforeFirstPoll) {
		const remainingMs = deadline - Date.now();
		if (remainingMs > 0) {
			await abortableSleep(Math.min(intervalMs, remainingMs), options.signal, CANCEL_MESSAGE);
		}
	}

	try {
		while (Date.now() < deadline) {
			if (options.signal?.aborted) throw new Error(CANCEL_MESSAGE);

			const result = await options.poll();
			if (result.status === "complete") return result.value;
			if (result.status === "failed") throw new Error(result.message);
			if (result.status === "retryable") {
				consecutiveFailures += 1;
				retryIntervalMs = Math.min(MAX_RETRY_INTERVAL_MS, Math.max(MINIMUM_INTERVAL_MS, retryIntervalMs * 2));
				const remainingMs = deadline - Date.now();
				if (remainingMs <= 0) break;
				const delayMs = Math.min(retryIntervalMs, remainingMs);
				const retryAt = Date.now() + delayMs;
				const canRetryManually = consecutiveFailures >= RETRYABLE_FAILURE_THRESHOLD;
				setRetryState({ consecutiveFailures, retryAt: canRetryManually ? retryAt : null });
				const manualRetry = await waitForRetry(
					delayMs,
					options.signal,
					canRetryManually ? options.setRetryHandler : undefined,
				);
				if (canRetryManually) {
					setRetryState({ consecutiveFailures, retryAt: null });
					if (manualRetry) retryIntervalMs = intervalMs;
				}

				continue;
			}

			consecutiveFailures = 0;
			retryIntervalMs = intervalMs;
			setRetryState({ consecutiveFailures, retryAt: null });
			if (result.status === "slow_down") {
				slowDownResponses += 1;
				// Use the server-provided interval when given (GitHub reports the new required minimum
				// in `interval`); trusting only a client-tracked value risks polling early forever under
				// WSL/VM clock drift. Otherwise apply RFC 8628 section 3.5: increase by 5 seconds.
				intervalMs =
					typeof result.intervalSeconds === "number" &&
					Number.isFinite(result.intervalSeconds) &&
					result.intervalSeconds > 0
						? Math.max(MINIMUM_INTERVAL_MS, Math.floor(result.intervalSeconds * 1000))
						: Math.max(MINIMUM_INTERVAL_MS, intervalMs + SLOW_DOWN_INTERVAL_INCREMENT_MS);
				retryIntervalMs = intervalMs;
			}

			const remainingMs = deadline - Date.now();
			if (remainingMs <= 0) break;
			await abortableSleep(Math.min(intervalMs, remainingMs), options.signal, CANCEL_MESSAGE);
		}

		throw new Error(slowDownResponses > 0 ? SLOW_DOWN_TIMEOUT_MESSAGE : TIMEOUT_MESSAGE);
	} finally {
		options.setRetryHandler?.(undefined);
	}
}
