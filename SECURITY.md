# Security policy

## Supported versions

Logic-Loom is research software published as a preview. Only the most recent
release receives security fixes.

## Reporting a vulnerability

Please report vulnerabilities **privately** via GitHub's security advisories
("Report a vulnerability" on the repository's Security tab), or by email to
<shmuliko@technion.ac.il> if you would rather not use GitHub. Do not open a
public issue for a suspected vulnerability.

You can expect an acknowledgment within a week. This is a best-effort research
project; there is no bug bounty.

## Threat model notes

- Logic-Loom is a single-user desktop app. The Python sidecar listens on
  `127.0.0.1` on a port chosen at launch, and its CORS policy admits only the
  app's own Tauri and localhost origins. It has no authentication of its own, so
  it must not be exposed on a network interface.
- Network access is opt-in per feature. The Assistant's literature discovery
  queries OpenAlex and Semantic Scholar, the Zotero pipeline talks to your Zotero
  library, and LLM work goes to the provider or agent CLI (such as Claude Code)
  you select. None of them runs until you use that feature.
- API keys are read from the environment or from your own configuration file
  (`~/.config/logic-loom/config.toml`), which never leaves your machine. Keep
  that file out of shared folders and out of bug reports.
- Knowledge nodes, records and vault notes are Markdown, YAML and JSON read from
  disk. Treat a vault, a workspace or a records store from an untrusted source
  as untrusted input: its text can reach an LLM prompt.
- Failure Memory projects and records stores are opened only when listed
  explicitly in the configuration; nothing is discovered by scanning the disk.
- The release binaries are not notarized or code-signed. Verify a download
  against the `SHA256SUMS` file published with each release.
