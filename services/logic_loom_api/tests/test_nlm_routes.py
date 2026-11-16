"""Unit tests for NLM route argv builders."""

from pathlib import Path

from logic_loom_api.routes.nlm import (
    build_generate_argv,
    build_nlm_batch_argv,
    build_notebook_argv,
    build_validate_argv,
)

AKMS = Path("/fake/AKMS")
VAULT = Path("/fake/vault")
BBT = Path("/fake/vault/zsumbib.json")


class TestBuildNotebookArgv:
    def test_basic(self):
        argv = build_notebook_argv(AKMS, VAULT, BBT, "R1_B1")
        assert argv[:3] == ["uv", "run", "python"]
        assert str(AKMS / "prepare_notebook.py") in argv
        assert "--vault" in argv
        assert str(VAULT) == argv[argv.index("--vault") + 1]
        assert "--collection" in argv
        assert "R1_B1" == argv[argv.index("--collection") + 1]
        assert "--bbt-json" in argv
        assert "--dry-run" not in argv

    def test_max_papers(self):
        argv = build_notebook_argv(AKMS, VAULT, BBT, "R1_B1", max_papers=10)
        assert argv[argv.index("--max-papers") + 1] == "10"

    def test_dry_run(self):
        argv = build_notebook_argv(AKMS, VAULT, BBT, "R1_B1", dry_run=True)
        assert "--dry-run" in argv


class TestBuildGenerateArgv:
    def test_basic(self):
        argv = build_generate_argv(AKMS, "R1_B1")
        assert argv[:3] == ["uv", "run", "python"]
        assert "--input" in argv
        assert "--output" in argv
        assert "--model" in argv
        assert "--status" in argv
        assert "new" == argv[argv.index("--status") + 1]

    def test_with_notebook(self):
        argv = build_generate_argv(AKMS, "R1_B1", notebook_id="abc-123")
        assert "--notebooklm-id" in argv
        assert "abc-123" == argv[argv.index("--notebooklm-id") + 1]

    def test_with_mcp_url(self):
        argv = build_generate_argv(AKMS, "R1_B1", nlm_mcp_url="http://localhost:3000/sse")
        assert "--notebooklm-url" in argv
        assert "http://localhost:3000/sse" == argv[argv.index("--notebooklm-url") + 1]

    def test_dry_run(self):
        argv = build_generate_argv(AKMS, "R1_B1", dry_run=True)
        assert "--dry-run" in argv

    def test_resume(self):
        argv = build_generate_argv(AKMS, "R1_B1", resume_from=5)
        assert "--resume-from" in argv
        assert "5" == argv[argv.index("--resume-from") + 1]


class TestBuildValidateArgv:
    def test_basic(self):
        argv = build_validate_argv(AKMS, batch_id="R1_B1")
        assert argv[:3] == ["uv", "run", "python"]
        assert "--batch" in argv
        assert "R1_B1" == argv[argv.index("--batch") + 1]
        assert "--concurrency" in argv
        assert "--timeout" in argv

    def test_single_node(self):
        argv = build_validate_argv(AKMS, node_id="FEM_001")
        assert "--node" in argv
        assert "FEM_001" == argv[argv.index("--node") + 1]

    def test_dry_run(self):
        argv = build_validate_argv(AKMS, dry_run=True)
        assert "--dry-run" in argv

    def test_concurrency(self):
        argv = build_validate_argv(AKMS, concurrency=4, timeout=120)
        assert "4" == argv[argv.index("--concurrency") + 1]
        assert "120" == argv[argv.index("--timeout") + 1]


class TestBuildNlmBatchArgv:
    def test_basic(self):
        argv = build_nlm_batch_argv(
            AKMS,
            plan_path="plans/R7_B2.json",
            out_dir="outputs/R7_B2",
            prompt_file="prompts/node.md",
            timeout=540,
        )
        assert argv[:4] == ["uv", "run", "python", "-m"]
        assert "akms_nodes_gen.nlm_batch" in argv
        assert "--plan" in argv
        assert "plans/R7_B2.json" == argv[argv.index("--plan") + 1]
        assert "--out-dir" in argv
        assert "--prompt-file" in argv
        assert "--timeout" in argv
        assert "540" == argv[argv.index("--timeout") + 1]
        assert "--output-format" in argv
        assert "yaml" == argv[argv.index("--output-format") + 1]

    def test_with_notebook_and_batch(self):
        argv = build_nlm_batch_argv(
            AKMS,
            plan_path="p.json",
            out_dir="out",
            prompt_file="pr.md",
            timeout=300,
            batch_id="R7_B2",
            notebook_id="nb-123",
        )
        assert "--batch-id" in argv
        assert "R7_B2" == argv[argv.index("--batch-id") + 1]
        assert "--notebook-id" in argv
        assert "nb-123" == argv[argv.index("--notebook-id") + 1]

    def test_source_ids_joined(self):
        argv = build_nlm_batch_argv(
            AKMS,
            plan_path="p.json",
            out_dir="out",
            prompt_file="pr.md",
            timeout=300,
            source_ids=["src_a", "src_b"],
        )
        assert "--source-ids" in argv
        assert "src_a,src_b" == argv[argv.index("--source-ids") + 1]

    def test_dry_run_and_force(self):
        argv = build_nlm_batch_argv(
            AKMS,
            plan_path="p.json",
            out_dir="out",
            prompt_file="pr.md",
            timeout=300,
            dry_run=True,
            force=True,
        )
        assert "--dry-run" in argv
        assert "--force" in argv

    def test_json_format(self):
        argv = build_nlm_batch_argv(
            AKMS,
            plan_path="p.json",
            out_dir="out",
            prompt_file="pr.md",
            timeout=300,
            output_format="json",
        )
        assert "json" == argv[argv.index("--output-format") + 1]
