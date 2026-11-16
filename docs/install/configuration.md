# Configuration

Logic-Loom reads its settings from a TOML file. Most people only ever change them in the
app's **Settings** screens; this page is for editing the file directly.

## Where settings come from

Highest priority first:

1. Values the app passes in directly (for example, when the sidecar is started with
   `--port`).
2. `LOGIC_LOOM_*` environment variables, one per key (`LOGIC_LOOM_VAULT_PATH`, ...).
3. The user config file: `~/.config/logic-loom/config.toml`, or the file named by
   `LOGIC_LOOM_CONFIG`.
4. Built-in defaults.

Placeholder values such as `<PATH>` count as unset, so the first-run dialog can tell that
setup is not finished.

## A minimal file

```toml
vault_path = "/Users/me/Obsidian/ZotSums"

# One provider for Learn, zsum and node generation (see LLM providers).
llm_provider = "claude-cli"

# Optional: where the Graph tab reads AKMS nodes from.
# akms_global_vault = "/Users/me/.claude/akms/nodes"
```

## Lists of projects and stores

Failure Memory projects and akms-records stores are never discovered by scanning your
disk. List each one:

```toml
failure_memory_mode = "read-only"   # disabled | read-only | operator

[[failure_memory_projects]]
id = "my-project"
config = "/path/to/my-project/.failure-memory/config.toml"

[[records_stores]]
id = "my-project"
scope = "project:my-project"
repo = "/path/to/my-project"
```

`failure_memory_mode = "operator"` can only be set in this file or with
`LOGIC_LOOM_FAILURE_MEMORY_MODE`, never from the app, because it grants write access to
every listed project. See [Failure Memory](../guide/failure-memory.md).

## Every key

The [configuration reference](../reference/configuration.md) lists every key with its
type, default, environment variable and description. It is generated from the code for
each release.
