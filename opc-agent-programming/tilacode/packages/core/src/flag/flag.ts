import { Config } from "effect"

export function truthy(key: string) {
  const value = process.env[key]?.toLowerCase()
  return value === "true" || value === "1"
}

const copy = process.env["TILACODE_EXPERIMENTAL_DISABLE_COPY_ON_SELECT"]
const fff = process.env["TILACODE_DISABLE_FFF"]

function enabledByExperimental(key: string) {
  return process.env[key] === undefined ? truthy("TILACODE_EXPERIMENTAL") : truthy(key)
}

export const Flag = {
  OTEL_EXPORTER_OTLP_ENDPOINT: process.env["OTEL_EXPORTER_OTLP_ENDPOINT"],
  OTEL_EXPORTER_OTLP_HEADERS: process.env["OTEL_EXPORTER_OTLP_HEADERS"],

  OPENCODE_AUTO_HEAP_SNAPSHOT: truthy("TILACODE_AUTO_HEAP_SNAPSHOT"),
  OPENCODE_GIT_BASH_PATH: process.env["TILACODE_GIT_BASH_PATH"],
  OPENCODE_CONFIG: process.env["TILACODE_CONFIG"],
  OPENCODE_CONFIG_CONTENT: process.env["TILACODE_CONFIG_CONTENT"],
  OPENCODE_DISABLE_AUTOUPDATE: truthy("TILACODE_DISABLE_AUTOUPDATE"),
  OPENCODE_ALWAYS_NOTIFY_UPDATE: truthy("TILACODE_ALWAYS_NOTIFY_UPDATE"),
  OPENCODE_DISABLE_PRUNE: truthy("TILACODE_DISABLE_PRUNE"),
  OPENCODE_DISABLE_TERMINAL_TITLE: truthy("TILACODE_DISABLE_TERMINAL_TITLE"),
  OPENCODE_SHOW_TTFD: truthy("TILACODE_SHOW_TTFD"),
  OPENCODE_DISABLE_AUTOCOMPACT: truthy("TILACODE_DISABLE_AUTOCOMPACT"),
  OPENCODE_DISABLE_MODELS_FETCH: truthy("TILACODE_DISABLE_MODELS_FETCH"),
  OPENCODE_DISABLE_MOUSE: truthy("TILACODE_DISABLE_MOUSE"),
  OPENCODE_FAKE_VCS: process.env["TILACODE_FAKE_VCS"],
  OPENCODE_SERVER_PASSWORD: process.env["TILACODE_SERVER_PASSWORD"],
  OPENCODE_SERVER_USERNAME: process.env["TILACODE_SERVER_USERNAME"],
  OPENCODE_DISABLE_FFF: fff === undefined ? process.platform === "win32" : truthy("TILACODE_DISABLE_FFF"),

  // Experimental
  OPENCODE_EXPERIMENTAL_FILEWATCHER: Config.boolean("TILACODE_EXPERIMENTAL_FILEWATCHER").pipe(
    Config.withDefault(false),
  ),
  OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER: Config.boolean("TILACODE_EXPERIMENTAL_DISABLE_FILEWATCHER").pipe(
    Config.withDefault(false),
  ),
  OPENCODE_EXPERIMENTAL_DISABLE_COPY_ON_SELECT:
    copy === undefined ? process.platform === "win32" : truthy("TILACODE_EXPERIMENTAL_DISABLE_COPY_ON_SELECT"),
  OPENCODE_MODELS_URL: process.env["TILACODE_MODELS_URL"],
  OPENCODE_MODELS_PATH: process.env["TILACODE_MODELS_PATH"],
  OPENCODE_DB: process.env["TILACODE_DB"],

  OPENCODE_WORKSPACE_ID: process.env["TILACODE_WORKSPACE_ID"],
  OPENCODE_EXPERIMENTAL_WORKSPACES: enabledByExperimental("TILACODE_EXPERIMENTAL_WORKSPACES"),

  // Evaluated at access time (not module load) because tests, the CLI, and
  // external tooling set these env vars at runtime.
  get OPENCODE_DISABLE_PROJECT_CONFIG() {
    return truthy("TILACODE_DISABLE_PROJECT_CONFIG")
  },
  get OPENCODE_EXPERIMENTAL_REFERENCES() {
    return enabledByExperimental("TILACODE_EXPERIMENTAL_REFERENCES")
  },
  get OPENCODE_TUI_CONFIG() {
    return process.env["TILACODE_TUI_CONFIG"]
  },
  get OPENCODE_CONFIG_DIR() {
    return process.env["TILACODE_CONFIG_DIR"]
  },
  get OPENCODE_PURE() {
    return truthy("TILACODE_PURE")
  },
  get OPENCODE_PERMISSION() {
    return process.env["TILACODE_PERMISSION"]
  },
  get OPENCODE_PLUGIN_META_FILE() {
    return process.env["TILACODE_PLUGIN_META_FILE"]
  },
  get OPENCODE_CLIENT() {
    return process.env["TILACODE_CLIENT"] ?? "cli"
  },
}
