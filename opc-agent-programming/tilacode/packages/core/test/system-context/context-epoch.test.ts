import { describe, expect } from "bun:test"
import { Effect, Layer, Ref, Schema } from "effect"
import { eq } from "drizzle-orm"
import { Database } from "@opencode-ai/core/database/database"
import { AppNodeBuilder } from "@opencode-ai/core/effect/app-node-builder"
import { LayerNode } from "@opencode-ai/core/effect/layer-node"
import { EventV2 } from "@opencode-ai/core/event"
import { EventTable } from "@opencode-ai/core/event/sql"
import { ProjectV2 } from "@opencode-ai/core/project"
import { ProjectTable } from "@opencode-ai/core/project/sql"
import { AbsolutePath } from "@opencode-ai/core/schema"
import { SessionContextEpoch } from "@opencode-ai/core/session/context-epoch"
import { SessionHistory } from "@opencode-ai/core/session/history"
import { SessionProjector } from "@opencode-ai/core/session/projector"
import { SessionSchema } from "@opencode-ai/core/session/schema"
import { SessionTable } from "@opencode-ai/core/session/sql"
import { SystemContext } from "@opencode-ai/core/system-context"
import { testEffect } from "../lib/effect"

const it = testEffect(
  AppNodeBuilder.build(LayerNode.group([Database.node, EventV2.node, SessionProjector.node]), []),
)
const sessionID = SessionSchema.ID.make("ses_prefix_cache")

const date = (value: Ref.Ref<string>) =>
  Effect.succeed(
    SystemContext.make({
      key: SystemContext.Key.make("core/date"),
      codec: Schema.toCodecJson(Schema.String),
      load: Ref.get(value),
      baseline: (current) => `Today's date: ${current}`,
      update: (_previous, current) => `Today's date is now: ${current}`,
    }),
  )

const seed = Effect.fnUntraced(function* (db: Database.Interface["db"]) {
  yield* db
    .insert(ProjectTable)
    .values({ id: ProjectV2.ID.global, worktree: AbsolutePath.make("/project"), sandboxes: [] })
    .onConflictDoNothing()
    .run()
  yield* db
    .insert(SessionTable)
    .values({
      id: sessionID,
      project_id: ProjectV2.ID.global,
      slug: "prefix-cache",
      directory: "/project",
      title: "Prefix cache",
      version: "test",
    })
    .run()
})

const contextEvents = Effect.fnUntraced(function* (db: Database.Interface["db"]) {
  return yield* db.select().from(EventTable).where(eq(EventTable.aggregate_id, sessionID)).all()
})

// Prefix reuse depends on the system block staying byte-identical while a Session runs, and on
// every later change arriving as an appended history message instead of a rewrite.
describe("SessionContextEpoch prefix stability", () => {
  it.effect("keeps the baseline byte-identical while sources are unchanged", () =>
    Effect.gen(function* () {
      const db = (yield* Database.Service).db
      const events = yield* EventV2.Service
      yield* seed(db)
      const value = yield* Ref.make("2026-09-27")

      const initialized = yield* SessionContextEpoch.initialize(db, date(value), sessionID)
      expect(initialized?.baseline).toBe("Today's date: 2026-09-27")

      const prepared = yield* SessionContextEpoch.prepare(db, events, date(value), sessionID)
      expect(prepared.baseline).toBe(initialized!.baseline)
      expect(prepared.baselineSeq).toBe(initialized!.baselineSeq)
      expect(yield* contextEvents(db)).toEqual([])
    }),
  )

  it.effect("appends a source change instead of rewriting the baseline", () =>
    Effect.gen(function* () {
      const db = (yield* Database.Service).db
      const events = yield* EventV2.Service
      yield* seed(db)
      const value = yield* Ref.make("2026-09-27")

      const initialized = yield* SessionContextEpoch.initialize(db, date(value), sessionID)
      yield* Ref.set(value, "2026-09-28")
      const prepared = yield* SessionContextEpoch.prepare(db, events, date(value), sessionID)

      expect(prepared.baseline).toBe(initialized!.baseline)
      expect(prepared.baselineSeq).toBe(initialized!.baselineSeq)

      const published = yield* contextEvents(db)
      expect(published.map((event) => event.type.startsWith("session.next.context.updated"))).toEqual([true])
      expect(published[0].data.text).toBe("Today's date is now: 2026-09-28")

      const history = yield* SessionHistory.load(db, sessionID)
      expect(history.map((message) => message.type)).toContain("system")
      expect(history.flatMap((message) => (message.type === "system" ? [message.text] : []))).toEqual([
        "Today's date is now: 2026-09-28",
      ])

      // The same unchanged value must not append a second time.
      yield* SessionContextEpoch.prepare(db, events, date(value), sessionID)
      expect((yield* contextEvents(db)).length).toBe(1)
    }),
  )
})
