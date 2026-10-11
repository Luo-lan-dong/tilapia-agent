import { describe, expect, test } from "bun:test"
import { SessionDoomLoop } from "@opencode-ai/core/session/doom-loop"

describe("SessionDoomLoop", () => {
  test("blocks only the third identical consecutive call", () => {
    const loop = SessionDoomLoop.make()
    const call = SessionDoomLoop.signature("echo", { text: "same" })

    expect(loop.observe("ses_1", call)).toBe(false)
    expect(loop.observe("ses_1", call)).toBe(false)
    expect(loop.observe("ses_1", call)).toBe(true)
    // stays blocked while the model keeps repeating it
    expect(loop.observe("ses_1", call)).toBe(true)
  })

  test("does not block when the arguments change", () => {
    const loop = SessionDoomLoop.make()

    expect(loop.observe("ses_1", SessionDoomLoop.signature("echo", { text: "1" }))).toBe(false)
    expect(loop.observe("ses_1", SessionDoomLoop.signature("echo", { text: "2" }))).toBe(false)
    expect(loop.observe("ses_1", SessionDoomLoop.signature("echo", { text: "3" }))).toBe(false)
    expect(loop.observe("ses_1", SessionDoomLoop.signature("echo", { text: "4" }))).toBe(false)
  })

  test("treats a different tool as different work and isolates sessions", () => {
    const loop = SessionDoomLoop.make()
    const echo = SessionDoomLoop.signature("echo", { text: "same" })

    expect(loop.observe("ses_1", echo)).toBe(false)
    expect(loop.observe("ses_1", SessionDoomLoop.signature("read", { text: "same" }))).toBe(false)
    expect(loop.observe("ses_2", echo)).toBe(false)
    expect(loop.observe("ses_2", echo)).toBe(false)
    expect(loop.observe("ses_2", echo)).toBe(true)
  })

  test("keeps the signature stable for key-order differences that JSON normalizes", () => {
    expect(SessionDoomLoop.signature("t", { a: 1, b: 2 })).toBe(SessionDoomLoop.signature("t", { a: 1, b: 2 }))
    expect(SessionDoomLoop.signature("t", undefined)).toBe("t\u0000undefined")
  })
})
