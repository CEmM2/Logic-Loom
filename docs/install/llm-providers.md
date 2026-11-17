# LLM providers

Logic-Loom has one LLM setting that drives all three things that need a model: AKMS
**Learn**, the **zsum** pipeline and AKMS **node generation**. Set it once under
**AKMS ▸ Settings**, or as `llm_provider` in `config.toml`.

| `llm_provider` | What it uses |
| --- | --- |
| `akms` | API keys through LiteLLM (`anthropic_api_key`, `openai_api_key`, `google_api_key`) |
| `claude-cli` | The Claude Code CLI on your machine |
| `codex-cli` | The Codex CLI |
| `nlm` | NotebookLM through the `nlm` CLI |
| a custom label | A CLI you register under `learn_llm_custom_clis` |
| unset or `auto` | Picks `nlm` or an API key automatically; CLI agents need an explicit choice |

API keys are stored in `config.toml` and never sent back to the app window: a saved key
shows as a row of dots in Settings. Type over it to replace it, or empty the field to
remove it.

## Per-feature overrides

To use a different provider for one feature, set an override. Each falls back to
`llm_provider` when unset.

```toml
llm_provider = "claude-cli"
zsum_llm_provider = "akms"        # zsum uses an API key instead
# nodegen_llm_provider = "nlm"
```

Older config files that set `learn_llm_provider` keep working: it is read as the global
provider when `llm_provider` is unset.

## Custom CLIs

Register any command-line model as a provider. `{prompt}` is replaced with the prompt as
one argument (no shell); without it, the prompt is piped to stdin.

```toml
[learn_llm_custom_clis]
local-llama = "llm -m llama3 {prompt}"
```

## Local models

**Home ▸ Models** can install and configure a local Ollama model. From the bundled
zotero-summarizer CLI, `zsum setup-model --size small|medium|large|xlarge` does the same
in one command.

Logic-Loom never stores provider sign-ins. Each CLI uses its own login.
