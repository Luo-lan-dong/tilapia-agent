export * as SessionDoomLoop from "./doom-loop"

/**
 * Blocks a tool call that repeats the immediately preceding identical calls.
 *
 * V1 judged the last calls *within one assistant message*, but a V2 provider turn usually emits a
 * single call, so that window can never fill and the guard would never fire. Counting has to happen
 * across steps instead. The block is deliberately non-fatal: the call is not executed, the model
 * receives a result explaining why, and it keeps going — the same shape the hub already uses for its
 * own orchestrator (ADR-043 / ADR-058).
 */

/** How many identical consecutive calls make a loop. */
export const threshold = 3

/** Model-visible result for a blocked call. */
export const hint = `Detected the same tool call repeated ${threshold} times with identical input; it was not executed. Change the approach — different arguments, a different tool, or explain why repeating it is required.`

/** Stable identity for one call: tool name plus its canonical input JSON. */
export function signature(name: string, input: unknown): string {
  let encoded: string | undefined
  try {
    encoded = JSON.stringify(input)
  } catch {
    encoded = undefined
  }
  return `${name}\u0000${encoded ?? String(input)}`
}

export interface Interface {
  /** Records one call and reports whether it must be blocked. */
  readonly observe: (sessionID: string, call: string) => boolean
}

export function make(): Interface {
  const windows = new Map<string, ReadonlyArray<string>>()
  return {
    observe(sessionID, call) {
      const window = [...(windows.get(sessionID) ?? []), call].slice(-threshold)
      windows.set(sessionID, window)
      return window.length === threshold && window.every((item) => item === call)
    },
  }
}
