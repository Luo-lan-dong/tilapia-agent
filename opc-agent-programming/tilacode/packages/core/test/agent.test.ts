import { describe, expect } from "bun:test"
import { Effect, Exit, Scope } from "effect"
import { AgentV2 } from "@opencode-ai/core/agent"
import { AppNodeBuilder } from "@opencode-ai/core/effect/app-node-builder"
import { Location } from "@opencode-ai/core/location"
import { AgentPlugin } from "@opencode-ai/core/plugin/agent"
import { AbsolutePath } from "@opencode-ai/core/schema"
import { location } from "./fixture/location"
import { testEffect } from "./lib/effect"
import { agentHost, host } from "./plugin/host"

const it = testEffect(AppNodeBuilder.build(AgentV2.node))

/** 装配内置 agent 定义（与 "does not ambiently opt built-in agents into bash" 同一路径） */
const builtInAgents = Effect.gen(function* () {
  const agent = yield* AgentV2.Service
  yield* AgentPlugin.Plugin.effect(
    host({
      agent: agentHost(agent),
    }),
  ).pipe(
    Effect.provideService(
      Location.Service,
      Location.Service.of(location({ directory: AbsolutePath.make("/project") })),
    ),
  )
  return agent
})

describe("AgentV2", () => {
  it.effect("starts without agents", () =>
    Effect.gen(function* () {
      const agent = yield* AgentV2.Service

      expect(yield* agent.all()).toEqual([])
      expect(yield* agent.get(AgentV2.ID.make("build"))).toBeUndefined()
    }),
  )

  it.effect("materializes replayable agent transforms", () =>
    Effect.gen(function* () {
      const agent = yield* AgentV2.Service
      const id = AgentV2.ID.make("reviewer")
      yield* agent.transform((editor) =>
        editor.update(id, (info) => {
          info.description = "Reviews code"
          info.mode = "subagent"
        }),
      )

      expect(yield* agent.get(id)).toMatchObject({ id, description: "Reviews code", mode: "subagent" })
      expect((yield* agent.all()).map((info) => info.id)).toEqual([id])
    }),
  )

  it.effect("rebuilds state when a transform is replaced", () =>
    Effect.gen(function* () {
      const agent = yield* AgentV2.Service
      const id = AgentV2.ID.make("reviewer")
      let description = "Old description"
      let hidden = true
      yield* agent.transform((editor) =>
        editor.update(id, (info) => {
          info.description = description
          info.hidden = hidden
        }),
      )
      description = "New description"
      hidden = false
      yield* agent.reload()

      expect(yield* agent.get(id)).toMatchObject({ description: "New description", hidden: false })
    }),
  )

  it.effect("removes a transform when its scope closes", () =>
    Effect.gen(function* () {
      const agent = yield* AgentV2.Service
      const id = AgentV2.ID.make("scoped")
      const scope = yield* Scope.make()
      yield* agent.transform((editor) => editor.update(id, () => {})).pipe(Scope.provide(scope))
      expect(yield* agent.get(id)).toBeDefined()

      yield* Scope.close(scope, Exit.void)
      expect(yield* agent.get(id)).toBeUndefined()
    }),
  )

  it.effect("applies direct agent updates", () =>
    Effect.gen(function* () {
      const agent = yield* AgentV2.Service
      const id = AgentV2.ID.make("build")

      yield* agent.transform((editor) =>
        editor.update(id, (info) => {
          info.mode = "primary"
          info.hidden = true
        }),
      )

      expect(yield* agent.get(id)).toMatchObject({ id, mode: "primary", hidden: true })
    }),
  )

  it.effect("creates agents with runtime defaults and supports direct removal", () =>
    Effect.gen(function* () {
      const agent = yield* AgentV2.Service
      const id = AgentV2.ID.make("custom")

      yield* agent.transform((editor) => editor.update(id, () => {}))
      expect(yield* agent.get(id)).toEqual(AgentV2.Info.empty(id))

      yield* agent.transform((editor) => editor.remove(id))
      expect(yield* agent.get(id)).toBeUndefined()
    }),
  )

  it.effect("does not ambiently opt built-in agents into bash", () =>
    Effect.gen(function* () {
      const agent = yield* builtInAgents

      const agents = yield* agent.all()
      expect(agents.map((item) => String(item.id)).sort()).toEqual([
        "build",
        "compaction",
        "explore",
        "general",
        "plan",
        "summary",
        "title",
      ])
      for (const item of agents) {
        expect(item.permissions.some((rule) => rule.action === "bash" && rule.effect !== "deny")).toBe(false)
      }
    }),
  )

  // M1a / UT-M1a-1：默认 agent 必须自带"验证自己"的纪律（V1 有、V2 迁移时丢失）
  it.effect("gives the default agent an explicit verification discipline", () =>
    Effect.gen(function* () {
      const agent = yield* builtInAgents
      const system = (yield* agent.get(AgentV2.defaultID))?.system ?? ""

      expect(system).toContain("AI coding agent")
      expect(system).toContain("run the project's lint and type-check commands")
      expect(system).toContain("Never assume a command or a test framework")
      expect(system).toContain("read the full error, fix the cause, and run it again")
      expect(system).toContain("Never ask whether you should run tests or proceed")
      expect(system).toContain("what you changed, what you ran to verify it, and what is still unverified")
      expect(system).toContain("Never present unverified work as verified")
    }),
  )

  // 2026-10-02 问题修复：默认 agent 必须具备待办纪律（多步任务先列 todo，
  // 否则 tasks.todos 长期为空、任务摘要上段永远「暂未上报待办明细」）
  it.effect("gives the default agent a todo list discipline", () =>
    Effect.gen(function* () {
      const agent = yield* builtInAgents
      const system = (yield* agent.get(AgentV2.defaultID))?.system ?? ""

      expect(system).toContain("todowrite")
      expect(system).toContain("exactly one item in_progress")
    }),
  )

  // M1a / UT-M1a-2：纪律只能落在编码 agent 上，不得串进只读探索/摘要 agent
  it.effect("keeps the verification discipline off the non-coding agents", () =>
    Effect.gen(function* () {
      const agent = yield* builtInAgents
      const discipline = "run the project's lint and type-check commands"

      const explore = (yield* agent.get(AgentV2.ID.make("explore")))?.system ?? ""
      const compaction = (yield* agent.get(AgentV2.ID.make("compaction")))?.system ?? ""

      expect(explore).toContain("file search specialist")
      expect(explore).not.toContain(discipline)
      expect(compaction).toContain("anchored context summarization assistant")
      expect(compaction).not.toContain(discipline)
      expect((yield* agent.get(AgentV2.ID.make("plan")))?.system).toBeUndefined()
    }),
  )

  // M1a / UT-M1a-3：既有内置提示词保持原样（本次改动只应影响 BUILD_SYSTEM）
  it.effect("leaves the existing built-in prompts unchanged", () =>
    Effect.gen(function* () {
      const agent = yield* builtInAgents

      const explore = (yield* agent.get(AgentV2.ID.make("explore")))?.system ?? ""
      const compaction = (yield* agent.get(AgentV2.ID.make("compaction")))?.system ?? ""

      expect(explore).toContain(
        "Do not create any files, or run bash commands that modify the user's system state in any way",
      )
      expect(compaction).toContain("Do not answer the conversation itself.")
      expect(compaction).toContain("Respond in the same language as the conversation.")
    }),
  )
})
