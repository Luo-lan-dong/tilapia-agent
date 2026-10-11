import path from "path"

process.env.TILACODE_DB = ":memory:"
process.env.TILACODE_MODELS_PATH = path.join(import.meta.dir, "plugin", "fixtures", "models-dev.json")
process.env.TILACODE_DISABLE_MODELS_FETCH = "true"
