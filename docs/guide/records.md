# Records mode

Records mode is a read-only browser for
[akms-records](https://cemm2.github.io/AKMS/reference/records/index.html) stores:
structured, schema-checked records that agents and people keep about a project. It needs
the `records` extra and at least one configured store.

!!! note "Experimental"
    akms-records is experimental upstream, so this mode may change between releases.

| Tab | What it does |
| --- | --- |
| Overview | Each configured store with its scope, record and schema counts, and folder |
| Browse | Search records and filter them by schema, lifecycle, review state and tag |
| Schemas | The record schemas a store defines |
| Graph | Records and the links between them, on their own canvas (a promoted record shows the AKMS node it became) |

## Configure stores

Stores are never discovered by scanning. List each one with an id and an akms-records
scope (`project:`, `agent:` or `shared:` plus a name):

```toml
[[records_stores]]
id = "my-project"
scope = "project:my-project"
repo = "/path/to/my-project"     # project scopes: records live in <repo>/.knowledge/records

[[records_stores]]
id = "notes"
scope = "shared:notes"
# home = "/path/to/records-home" # agent/shared scopes: default AKMS_RECORDS_HOME or ~/.knowledge/records
```

Until a store is listed, the Records tab shows this example with a **Copy snippet**
button and the path of your `config.toml`. Restart Logic-Loom after editing the file.

Logic-Loom opens stores read-only and never builds or changes their search index. The
graph uses only the standard record fields, not the schema-specific payload.
