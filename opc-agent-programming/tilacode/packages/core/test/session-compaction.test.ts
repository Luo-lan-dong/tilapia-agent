import { expect, test } from "bun:test"
import { LLM, Model, SystemPart, ToolDefinition } from "@opencode-ai/llm"
import * as OpenAIChat from "@opencode-ai/llm/protocols/openai-chat"
import { SessionCompaction, type Entry } from "@opencode-ai/core/session/compaction"
import { SessionMessage } from "@opencode-ai/core/session/message"
import { toLLMMessages } from "@opencode-ai/core/session/runner/to-llm-message"
import { DateTime } from "effect"

const model = Model.make({ id: "model", provider: "provider", route: OpenAIChat.route })
const created = DateTime.makeUnsafe(0)

const entry = (seq: number, text: string): Entry => ({
  seq,
  message: {
    id: SessionMessage.ID.make(`msg_${seq}`),
    type: "user",
    text,
    time: { created },
  },
})

const checkpoint = (seq: number): Entry => ({
  seq,
  message: {
    id: SessionMessage.ID.make(`msg_${seq}`),
    type: "compaction",
    reason: "auto",
    summary: "## Objective\n- earlier work",
    recent: "[User]: earlier work",
    time: { created },
  },
})

const tools = [ToolDefinition.make({ name: "read", description: "Read a file", inputSchema: { type: "object" } })]

test("compaction prompt preserves detailed work state and relevant files", () => {
  const prompt = SessionCompaction.buildPrompt({ context: ["conversation history"] })

  expect(prompt).toContain("## Work State\n### Completed")
  expect(prompt).toContain("### Active")
  expect(prompt).toContain("### Blocked")
  expect(prompt).toContain("## Relevant Files")
})

test("compaction describes tool media without embedding base64", () => {
  const base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"
  const serialized = SessionCompaction.serializeToolContent([
    { type: "text", text: "Image read successfully" },
    {
      type: "file",
      uri: `data:image/png;base64,${base64}`,
      mime: "image/png",
      name: "pixel.png",
    },
  ])

  expect(serialized).toBe("Image read successfully\n[Attached image/png: pixel.png]")
  expect(serialized).not.toContain(base64)
})

// The summary call must look like a strict extension of the live request, otherwise it pays full
// price for the entire conversation and DeepSeek cannot match a persisted cache prefix unit.
test("summary request replays the live request prefix", () => {
  const entries = [entry(1, "first"), entry(2, "second"), entry(3, "third"), entry(4, "fourth")]
  const request = LLM.request({
    model,
    system: [SystemPart.make("agent prompt"), SystemPart.make("frozen baseline")],
    messages: toLLMMessages(
      entries.map((item) => item.message),
      model,
    ),
    tools,
    providerOptions: { openai: { promptCacheKey: "ses_abc" } },
    generation: { temperature: 0.3 },
  })

  const prefix = entries.slice(0, 3)
  const summary = SessionCompaction.buildSummaryRequest({
    request,
    model,
    prefix,
    prompt: "summarize the conversation above",
    maxTokens: 2048,
  })

  const replayed = toLLMMessages(
    prefix.map((item) => item.message),
    model,
  )
  // Schema-class construction rebuilds nested values, so identity cannot be asserted; what matters
  // for prefix reuse is that every carried-over field serializes to the same bytes.
  expect(JSON.stringify(summary.system)).toBe(JSON.stringify(request.system))
  expect(JSON.stringify(summary.tools)).toBe(JSON.stringify(request.tools))
  expect(JSON.stringify(summary.model)).toBe(JSON.stringify(request.model))
  expect(JSON.stringify(summary.providerOptions)).toBe(JSON.stringify(request.providerOptions))
  expect(summary.messages.slice(0, replayed.length)).toEqual(replayed)
  expect(summary.messages.slice(0, replayed.length)).toEqual(request.messages.slice(0, replayed.length))
  expect(summary.messages.length).toBe(replayed.length + 1)
  expect(summary.messages.at(-1)?.role).toBe("user")
  expect(summary.messages.at(-1)?.content).toEqual([{ type: "text", text: "summarize the conversation above" }])
  expect(summary.generation?.maxTokens).toBe(2048)
  expect(summary.generation?.temperature).toBe(0.3)
})

// A character-level split cannot be replayed as request messages and also feeds the summarizer a
// mutilated copy of a single message, so the boundary has to land between two messages.
test("summary selection never splits a message across prefix and recent", () => {
  const padding = "x".repeat(2000)
  const entries = Array.from({ length: 6 }, (_, index) =>
    entry(index + 1, `BEGIN-${index}\n${padding}\nEND-${index}`),
  )

  const selected = SessionCompaction.selectForSummary(entries, 700)

  expect(selected).toBeDefined()
  expect(selected!.prefix.length).toBeGreaterThan(0)
  expect(selected!.prefix.length).toBeLessThan(entries.length)
  expect(selected!.prefix.every((item, index) => item === entries[index])).toBe(true)
  for (let index = 0; index < entries.length; index++) {
    const rendered = index < selected!.prefix.length ? selected!.head : selected!.recent
    expect(rendered).toContain(`BEGIN-${index}`)
    expect(rendered).toContain(`END-${index}`)
  }
})

test("summary selection keeps history verbatim while it fits the recent budget", () => {
  const selected = SessionCompaction.selectForSummary([entry(1, "alpha"), entry(2, "beta")], 100_000)

  expect(selected).toBeDefined()
  expect(selected!.prefix).toEqual([])
  expect(selected!.head).toBe("")
  expect(selected!.recent).toContain("alpha")
  expect(selected!.recent).toContain("beta")
})

test("summary selection has nothing to replace when history is only a checkpoint", () => {
  expect(SessionCompaction.selectForSummary([checkpoint(1)], 8000)).toBeUndefined()
})

test("summary selection keeps the checkpoint inside the replayed prefix", () => {
  const padding = "y".repeat(2000)
  const entries = [checkpoint(1), entry(2, padding), entry(3, padding)]
  const selected = SessionCompaction.selectForSummary(entries, 600)

  expect(selected).toBeDefined()
  expect(selected!.prefix).toEqual([entries[0], entries[1]])
  expect(selected!.recent).toContain(padding)
})
