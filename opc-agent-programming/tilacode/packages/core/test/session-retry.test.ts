import { describe, expect, test } from "bun:test"
import { SessionRetry } from "@opencode-ai/core/session/retry"

describe("SessionRetry.decide", () => {
  const base = { attempt: 1, elapsedMs: 0, retryable: true }

  test("refuses a non-retryable error", () => {
    expect(SessionRetry.decide({ ...base, retryable: false })).toEqual({ retry: false, reason: "not-retryable" })
  })

  test("retries a retryable error and advances the attempt", () => {
    expect(SessionRetry.decide(base)).toEqual({
      retry: true,
      delayMs: SessionRetry.INITIAL_DELAY_MS,
      attempt: 2,
    })
  })

  test("stops at the attempt cap instead of retrying forever", () => {
    expect(SessionRetry.decide({ ...base, attempt: SessionRetry.MAX_ATTEMPTS })).toEqual({
      retry: false,
      reason: "attempts-exhausted",
    })
  })

  test("stops when the total time budget would be exceeded", () => {
    expect(SessionRetry.decide({ ...base, elapsedMs: SessionRetry.MAX_TOTAL_DELAY_MS })).toEqual({
      retry: false,
      reason: "budget-exhausted",
    })
  })
})

describe("SessionRetry.delay", () => {
  test("backs off exponentially and caps the delay", () => {
    expect(SessionRetry.delay(1)).toBe(2_000)
    expect(SessionRetry.delay(2)).toBe(4_000)
    expect(SessionRetry.delay(3)).toBe(8_000)
    expect(SessionRetry.delay(20)).toBe(SessionRetry.MAX_DELAY_MS)
  })

  test("prefers an explicit provider delay, capped", () => {
    expect(SessionRetry.delay(3, 1_500)).toBe(1_500)
    expect(SessionRetry.delay(3, SessionRetry.MAX_DELAY_MS * 10)).toBe(SessionRetry.MAX_DELAY_MS)
  })

  test("ignores a nonsensical provider delay", () => {
    expect(SessionRetry.delay(1, Number.NaN)).toBe(2_000)
    expect(SessionRetry.delay(1, -1)).toBe(2_000)
  })
})
