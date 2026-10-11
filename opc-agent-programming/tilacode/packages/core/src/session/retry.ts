export * as SessionRetry from "./retry"

/**
 * Bounded provider-retry policy for one Session turn.
 *
 * V1 retried unbounded — only non-retryable errors stopped it — which fights the product goal of not
 * burning tokens on a broken provider. The transport layer already retries twice per request, so this
 * session-level layer stays deliberately small: a few attempts inside a hard wall-clock budget.
 */

/** First backoff delay when the provider gives no explicit hint. */
export const INITIAL_DELAY_MS = 2_000
/** Exponential factor applied per attempt. */
export const BACKOFF_FACTOR = 2
/** Hard cap on a single backoff delay. */
export const MAX_DELAY_MS = 30_000
/** Maximum session-level attempts for one provider turn, including the first. */
export const MAX_ATTEMPTS = 5
/** Wall-clock budget for the whole retry sequence, delays included. */
export const MAX_TOTAL_DELAY_MS = 60_000

export type Decision =
  | { readonly retry: true; readonly delayMs: number; readonly attempt: number }
  | { readonly retry: false; readonly reason: "not-retryable" | "attempts-exhausted" | "budget-exhausted" }

/**
 * Delay before the given attempt. An explicit provider delay wins over the exponential default,
 * because the provider knows when it will accept traffic again.
 */
export function delay(attempt: number, retryAfterMs?: number): number {
  if (retryAfterMs !== undefined && Number.isFinite(retryAfterMs) && retryAfterMs >= 0)
    return Math.min(Math.ceil(retryAfterMs), MAX_DELAY_MS)
  return Math.min(INITIAL_DELAY_MS * Math.pow(BACKOFF_FACTOR, attempt - 1), MAX_DELAY_MS)
}

/** Whether to retry the provider turn, and how long to wait first. */
export function decide(input: {
  readonly attempt: number
  readonly elapsedMs: number
  readonly retryable: boolean
  readonly retryAfterMs?: number
}): Decision {
  if (!input.retryable) return { retry: false, reason: "not-retryable" }
  if (input.attempt >= MAX_ATTEMPTS) return { retry: false, reason: "attempts-exhausted" }
  const delayMs = delay(input.attempt, input.retryAfterMs)
  if (input.elapsedMs + delayMs > MAX_TOTAL_DELAY_MS) return { retry: false, reason: "budget-exhausted" }
  return { retry: true, delayMs, attempt: input.attempt + 1 }
}
