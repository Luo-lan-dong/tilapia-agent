export * as SessionCompaction from "./compaction"

import {
  LLMError,
  LLMEvent,
  LLMRequest,
  Message,
  mergeGenerationOptions,
  type Model,
} from "@opencode-ai/llm"
import { toLLMMessages } from "./runner/to-llm-message"
import { DateTime, Effect, Stream } from "effect"
import type { Config } from "../config"
import type { EventV2 } from "../event"
import { SessionEvent } from "./event"
import { SessionMessage } from "./message"
import { SessionSchema } from "./schema"
import { Token } from "../util/token"

const DEFAULT_BUFFER = 20_000
const DEFAULT_KEEP_TOKENS = 8_000
const TOOL_OUTPUT_MAX_CHARS = 2_000
const SUMMARY_OUTPUT_TOKENS = 4_096
const SUMMARY_TEMPLATE = `Output exactly the Markdown structure shown inside <template> and keep the section order unchanged. Do not include the <template> tags in your response.
<template>
## Objective
- [one or two brief sentences describing what the user is trying to accomplish]

## Important Details
- [constraints/preferences, decisions and why, important facts/assumptions, exact context needed to continue, or "(none)"]

## Work State
### Completed
- [finished work, verified facts, or changes made; otherwise "(none)"]

### Active
- [current work, partial changes, or investigation state; otherwise "(none)"]

### Blocked
- [blockers, failing commands, or unknowns; otherwise "(none)"]

## Next Move
1. [immediate concrete action, or "(none)"]
2. [next action if known, or "(none)"]

## Relevant Files
- [file or directory path: why it matters, or "(none)"]
</template>

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact file paths, symbols, commands, error strings, URLs, and identifiers when known.
- Do not mention the summary process or that context was compacted.`

export type Entry = {
  readonly seq: number
  readonly message: SessionMessage.Message
}

type Settings = {
  readonly auto: boolean
  readonly buffer: number
  readonly tokens: number
}

type Dependencies = {
  readonly events: EventV2.Interface
  readonly llm: {
    readonly stream: (request: LLMRequest) => Stream.Stream<LLMEvent, LLMError>
  }
  readonly config: readonly Config.Entry[]
}

type Input = {
  readonly sessionID: SessionSchema.ID
  readonly entries: readonly Entry[]
  readonly model: Model
  readonly request: LLMRequest
}

const estimate = (value: unknown) => Token.estimate(JSON.stringify(value))

const truncate = (value: string) =>
  value.length <= TOOL_OUTPUT_MAX_CHARS ? value : `${value.slice(0, TOOL_OUTPUT_MAX_CHARS)}\n[truncated]`

export const serializeToolContent = (content: SessionMessage.ToolStateCompleted["content"]) =>
  content
    .map((item) =>
      item.type === "text" ? item.text : `[Attached ${item.mime}${item.name === undefined ? "" : `: ${item.name}`}]`,
    )
    .join("\n")

const serialize = (message: SessionMessage.Message) => {
  if (message.type === "user") {
    const files = message.files?.map((file) => `[Attached ${file.mime}: ${file.name ?? file.uri}]`) ?? []
    return [`[User]: ${message.text}`, ...files].join("\n")
  }
  if (message.type === "assistant") {
    return message.content
      .flatMap((part) => {
        if (part.type === "text") return [`[Assistant]: ${part.text}`]
        if (part.type === "reasoning") return part.text ? [`[Assistant reasoning]: ${part.text}`] : []
        const input = typeof part.state.input === "string" ? part.state.input : JSON.stringify(part.state.input)
        if (part.state.status === "completed")
          return [
            `[Assistant tool call]: ${part.name}(${input})`,
            `[Tool result]: ${truncate(serializeToolContent(part.state.content))}`,
          ]
        if (part.state.status === "error")
          return [`[Assistant tool call]: ${part.name}(${input})`, `[Tool error]: ${part.state.error.message}`]
        return [`[Assistant tool call]: ${part.name}(${input})`]
      })
      .join("\n")
  }
  if (message.type === "system") return `[System update]: ${message.text}`
  if (message.type === "synthetic") return `[Synthetic context]: ${message.text}`
  if (message.type === "shell") return `[Shell]: ${message.command}\n${truncate(message.output)}`
  return ""
}

const settings = (documents: readonly Config.Entry[]) => {
  const configured = documents
    .filter((entry): entry is Config.Document => entry.type === "document")
    .flatMap((entry) => (entry.info.compaction ? [entry.info.compaction] : []))
  return configured.reduce<Settings>(
    (result, current) => ({
      auto: current.auto ?? result.auto,
      buffer: current.buffer ?? result.buffer,
      tokens: current.keep?.tokens ?? result.tokens,
    }),
    { auto: true, buffer: DEFAULT_BUFFER, tokens: DEFAULT_KEEP_TOKENS },
  )
}

/**
 * Split history into the portion that the summary will replace (`prefix`) and the portion kept
 * verbatim (`recent`).
 *
 * The split index must land on a message boundary, never inside one: the summary call replays
 * `prefix` as the real request's own messages so that its prompt stays a byte-identical prefix of
 * the hot request (see `buildSummaryRequest`). A character-level split cannot be replayed that way,
 * and it also fed the summarizer a mutilated copy of a single message.
 */
export const selectForSummary = (
  entries: readonly Entry[],
  tokens: number,
): { readonly head: string; readonly recent: string; readonly prefix: readonly Entry[] } | undefined => {
  if (entries.every((entry) => entry.message.type === "compaction")) return
  const serialized = entries.map((entry) => (entry.message.type === "compaction" ? "" : serialize(entry.message)))
  // Walk backwards accumulating the recent-context budget; the first message that would overflow it
  // (and every message before it) belongs to the summary prefix.
  let total = 0
  let start = entries.length
  for (let index = entries.length - 1; index >= 0; index--) {
    if (entries[index].message.type === "compaction") {
      start = index
      continue
    }
    const next = total + Token.estimate(serialized[index])
    if (next > tokens) break
    total = next
    start = index
  }
  const text = (from: number, to: number) => serialized.slice(from, to).filter(Boolean).join("\n\n")
  return { head: text(0, start), recent: text(start, entries.length), prefix: entries.slice(0, start) }
}

/**
 * Build the compaction summary request as a true prefix of the live request.
 *
 * `system`, `tools`, `model`, `providerOptions`, and every other request field are carried over by
 * reference; only `messages` and the output budget change. DeepSeek (and prefix caches generally)
 * match persisted prefix units, so replaying the hot prefix turns an otherwise cold, full-price
 * summarization call into a mostly cached one.
 */
export const buildSummaryRequest = (input: {
  readonly request: LLMRequest
  readonly model: Model
  readonly prefix: readonly Entry[]
  readonly prompt: string
  readonly maxTokens: number
}) =>
  LLMRequest.update(input.request, {
    messages: [...toLLMMessages(input.prefix.map((entry) => entry.message), input.model), Message.user(input.prompt)],
    generation: mergeGenerationOptions(input.request.generation, { maxTokens: input.maxTokens }),
  })

export const buildPrompt = (input: { readonly previousSummary?: string; readonly context: readonly string[] }) =>
  [
    input.previousSummary
      ? `Update the anchored summary below using the conversation history above.\nPreserve still-true details, remove stale details, and merge in the new facts.\n<previous-summary>\n${input.previousSummary}\n</previous-summary>`
      : "Create a new anchored summary from the conversation history.",
    SUMMARY_TEMPLATE,
    ...input.context,
  ].join("\n\n")

export const make = (dependencies: Dependencies) => {
  const config = settings(dependencies.config)
  const compactAfterOverflow = Effect.fn("SessionCompaction.compactAfterOverflow")(function* (input: Input) {
    const context = input.model.route.defaults.limits?.context
    if (context === undefined || context <= 0) return false
    const output = input.request.generation?.maxTokens ?? input.model.route.defaults.limits?.output ?? 0
    const selected = selectForSummary(input.entries, config.tokens)
    const previousSummary = input.entries.find((entry) => entry.message.type === "compaction")?.message
    if (!selected || (selected.prefix.length === 0 && previousSummary?.type !== "compaction")) return false
    // The discarded conversation is replayed as real request messages whenever there is a message
    // boundary to replay; only when nothing can be replayed (the whole history fits the keep budget,
    // e.g. a few oversized tool outputs dominating the request) does it ride in the instruction.
    const summaryPrompt = buildPrompt({
      previousSummary: previousSummary?.type === "compaction" ? previousSummary.summary : undefined,
      context:
        selected.prefix.length === 0 && previousSummary?.type === "compaction" ? [previousSummary.recent] : [],
    })
    const summaryOutput = Math.min(output || SUMMARY_OUTPUT_TOKENS, SUMMARY_OUTPUT_TOKENS)
    const summaryRequest = buildSummaryRequest({
      request: input.request,
      model: input.model,
      prefix: selected.prefix,
      prompt: summaryPrompt,
      maxTokens: summaryOutput,
    })
    if (
      estimate({
        system: summaryRequest.system,
        messages: summaryRequest.messages,
        tools: summaryRequest.tools,
      }) >
      context - summaryOutput
    )
      return false
    const messageID = SessionMessage.ID.create()
    yield* dependencies.events.publish(SessionEvent.Compaction.Started, {
      sessionID: input.sessionID,
      messageID,
      timestamp: yield* DateTime.now,
      reason: "auto",
    })

    const chunks: string[] = []
    let failed = false
    const summarized = yield* dependencies.llm
      .stream(summaryRequest)
      .pipe(
        Stream.runForEach((event) => {
          if (LLMEvent.is.providerError(event)) failed = true
          if (LLMEvent.is.textDelta(event)) chunks.push(event.text)
          return Effect.void
        }),
        Effect.as(true),
        Effect.catchTag("LLM.Error", () => Effect.succeed(false)),
      )
    const summary = chunks.join("")
    if (!summarized || failed || !summary.trim()) return false
    yield* dependencies.events.publish(SessionEvent.Compaction.Ended, {
      sessionID: input.sessionID,
      messageID,
      timestamp: yield* DateTime.now,
      reason: "auto",
      text: summary,
      recent: selected.recent,
    })
    return true
  })
  const compactIfNeeded = Effect.fn("SessionCompaction.compactIfNeeded")(function* (input: Input) {
    if (!config.auto) return false
    const context = input.model.route.defaults.limits?.context
    if (context === undefined || context <= 0) return false
    const output = input.request.generation?.maxTokens ?? input.model.route.defaults.limits?.output ?? 0
    if (
      estimate({ system: input.request.system, messages: input.request.messages, tools: input.request.tools }) <=
      context - Math.max(output, config.buffer)
    )
      return false
    return yield* compactAfterOverflow(input)
  })
  return {
    compactIfNeeded,
    compactAfterOverflow,
  }
}
