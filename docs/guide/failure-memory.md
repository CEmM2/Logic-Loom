# Failure Memory mode

Failure Memory mode shows the lesson registries that
[akms-failure-memory](https://cemm2.github.io/AKMS/reference/failure-memory/index.html)
keeps in a project's `.failure-memory/` folder: recorded failures, the routes that deliver
each lesson to the files and symbols it concerns, and what an agent would be told for a
given task.

It needs the `failure-memory` extra (release builds include it) and at least one project
listed in your config. Logic-Loom only reads through the package's public API; the
package stays the only thing that records or compiles lessons.

## Tabs

| Tab | What it does |
| --- | --- |
| Overview | Registry status, staleness (have lessons changed since the last compile?) and compiler diagnostics |
| Lessons | The lesson inventory, filterable by path, symbol, category, root cause and related lesson |
| Routes | The committed route index the provider reads, and its coverage |
| Preview | The required, co-activated and advisory lessons the provider would return for a task or a diff; nothing is written |
| Graph | A graph overlay of lessons, files and symbols, on its own canvas (it never changes the AKMS graph) |
| Record | Operator mode only: the recording form, compile and refresh jobs, write diagnostics |

## Configure projects

Projects are never discovered by scanning your disk. List each one:

```toml
[[failure_memory_projects]]
id = "my-project"
config = "/path/to/my-project/.failure-memory/config.toml"
# root = "/path/to/my-project"   # only if the config is not directly under the repo root
```

Until a project is listed, the Failure Memory tab shows an example with a **Copy
snippet** button and the path of your `config.toml`. Restart Logic-Loom after editing
the file.

## Modes

| `failure_memory_mode` | What it allows |
| --- | --- |
| `read-only` (default) | Inspect, preview and diagnose. Cannot change any project. |
| `operator` | Also record lessons, compile and refresh, from the **Record** tab |
| `disabled` | Hides the mode |

Operator mode can only be turned on in `config.toml` or with
`LOGIC_LOOM_FAILURE_MEMORY_MODE`. The app cannot switch it, because it grants write access
to every listed project.

In operator mode, record, compile and refresh run as background jobs. A request being
accepted does not mean it succeeded: the job's own result says whether the lesson was
recorded. A failed compile rolls the recording back.

## Refresh and the code mirror

Refresh regenerates a project's code mirror. With AKMS 0.4 and later, projects whose
config sets `[toolchain].mirror_provider = "builtin"` (the default for new projects) need
nothing else. Projects that still use the `repo2md` provider need a pinned repo2md
checkout; **Record ▸ Write diagnostics** says what is missing.

## How this repository uses it

Logic-Loom keeps its own lessons in `.failure-memory/`. Contributors record them with the
`failure-memory` CLI; see [Contributing](../developers/index.md#failure-memory-lessons).
