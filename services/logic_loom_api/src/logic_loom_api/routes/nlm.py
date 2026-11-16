"""/api/nlm/* — NotebookLM integration: notebook creation, node generation, validation.

Each operation runs as a subprocess through the shared RunRegistry,
reusing the same ring-buffer + SSE streaming pattern as zsum runs.
The AKMS scripts are invoked from ``settings.akms_repo``, the optional AKMS
source checkout; without one every launch route answers 412.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..config import Settings
from ..deps import get_settings
from ..runs.registry import get_registry

logger = logging.getLogger(__name__)

router = APIRouter()

NlmTask = Literal["notebook", "generate", "generate-nlm", "validate"]


# ── Request models ────────────────────────────────────────────────────────────


class NotebookRequest(BaseModel):
    batch_id: str
    collection: str | None = None
    max_papers: int = 50
    dry_run: bool = False


class GenerateRequest(BaseModel):
    batch_id: str
    notebook_id: str | None = None
    model: str = "claude-sonnet-4-20250514"
    batch_size: int = 4
    max_tokens: int = 16000
    dry_run: bool = False
    resume_from: int | None = None
    nlm_mcp_url: str | None = None


class NlmBatchRequest(BaseModel):
    batch_id: str
    plan_path: str
    prompt_file: str
    out_dir: str | None = None
    template_file: str | None = None
    notebook_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    timeout: float = 540
    output_format: Literal["yaml", "json"] = "yaml"
    profile: str | None = None
    max_retries: int = 2
    force: bool = False
    dry_run: bool = False


class ValidateRequest(BaseModel):
    batch_id: str
    node_id: str | None = None
    concurrency: int = 1
    timeout: int = 240
    dry_run: bool = False


# ── Helpers ───────────────────────────────────────────────────────────────────


def _resolve_akms(settings: Settings) -> Path:
    """The AKMS checkout these developer-script routes run from.

    ``/api/nlm/*`` invokes AKMS's in-repo scripts with ``uv run``, so unlike
    the rest of the app it genuinely needs the optional ``akms_repo`` source
    checkout. Without one it answers 412 (as before), never a 500.
    """
    repo = settings.akms_repo_dir()
    if repo is None:
        where = f": {settings.akms_repo}" if settings.akms_repo is not None else " (unset)"
        raise HTTPException(
            412,
            detail=(
                f"akms_repo not found{where}. The /api/nlm developer scripts run "
                "from an AKMS source checkout; set akms_repo to use them."
            ),
        )
    return repo


def _resolve_bbt_json(settings: Settings) -> Path:
    if settings.vault_path:
        candidate = settings.vault_path / "zsumbib.json"
        if candidate.exists():
            return candidate
    akms = settings.akms_repo_dir()
    if akms is not None:
        fallback = akms / "Data" / "zsumbib.json"
        if fallback.exists():
            return fallback
    raise HTTPException(
        412, detail="Cannot find zsumbib.json — set vault_path or check AKMS/Data/",
    )


def _resolve_vault(settings: Settings) -> Path:
    if not settings.vault_path:
        raise HTTPException(412, detail="vault_path not configured")
    vp = settings.vault_path.expanduser()
    if not vp.is_dir():
        raise HTTPException(412, detail=f"vault_path not found: {vp}")
    return vp


def build_notebook_argv(
    akms_repo: Path,
    vault: Path,
    bbt_json: Path,
    collection: str,
    *,
    max_papers: int = 50,
    dry_run: bool = False,
) -> list[str]:
    script = akms_repo / "prepare_notebook.py"
    argv = [
        "uv", "run", "python", str(script),
        "--vault", str(vault),
        "--collection", collection,
        "--bbt-json", str(bbt_json),
        "--max-papers", str(max_papers),
    ]
    if dry_run:
        argv.append("--dry-run")
    return argv


def build_generate_argv(
    akms_repo: Path,
    batch_id: str,
    *,
    notebook_id: str | None = None,
    nlm_mcp_url: str | None = None,
    model: str = "claude-sonnet-4-20250514",
    batch_size: int = 4,
    max_tokens: int = 16000,
    dry_run: bool = False,
    resume_from: int | None = None,
) -> list[str]:
    inputs_dir = akms_repo / "Packages" / "AKMS_nodes_gen" / "inputs"
    output_dir = akms_repo / "Packages" / "AKMS_nodes_gen" / "generated_nodes"
    script = akms_repo / "Packages" / "AKMS_nodes_gen" / "src" / "akms_nodes_gen" / "generate_nodes_pipeline.py"
    argv = [
        "uv", "run", "python", str(script),
        "--input", str(inputs_dir),
        "--output", str(output_dir),
        "--model", model,
        "--batch-size", str(batch_size),
        "--max-tokens", str(max_tokens),
        "--status", "new",
    ]
    if notebook_id:
        argv.extend(["--notebooklm-id", notebook_id])
    if nlm_mcp_url:
        argv.extend(["--notebooklm-url", nlm_mcp_url])
    if dry_run:
        argv.append("--dry-run")
    if resume_from is not None:
        argv.extend(["--resume-from", str(resume_from)])
    return argv


def build_nlm_batch_argv(
    akms_repo: Path,
    *,
    plan_path: str,
    out_dir: str,
    prompt_file: str,
    timeout: float,
    output_format: str = "yaml",
    batch_id: str | None = None,
    notebook_id: str | None = None,
    template_file: str | None = None,
    source_ids: list[str] | None = None,
    profile: str | None = None,
    max_retries: int = 2,
    force: bool = False,
    dry_run: bool = False,
) -> list[str]:
    argv = [
        "uv", "run", "python", "-m", "akms_nodes_gen.nlm_batch",
        "--plan", plan_path,
        "--out-dir", out_dir,
        "--prompt-file", prompt_file,
        "--timeout", str(timeout),
        "--output-format", output_format,
        "--max-retries", str(max_retries),
    ]
    if batch_id:
        argv.extend(["--batch-id", batch_id])
    if notebook_id:
        argv.extend(["--notebook-id", notebook_id])
    if template_file:
        argv.extend(["--template-file", template_file])
    if source_ids:
        argv.extend(["--source-ids", ",".join(source_ids)])
    if profile:
        argv.extend(["--profile", profile])
    if force:
        argv.append("--force")
    if dry_run:
        argv.append("--dry-run")
    return argv


def build_validate_argv(
    akms_repo: Path,
    *,
    batch_id: str | None = None,
    node_id: str | None = None,
    concurrency: int = 1,
    timeout: int = 240,
    dry_run: bool = False,
) -> list[str]:
    script = akms_repo / "Packages" / "AKMS_nodes_gen" / "validate_nodes.py"
    argv = [
        "uv", "run", "python", str(script),
        "--concurrency", str(concurrency),
        "--timeout", str(timeout),
    ]
    if batch_id:
        argv.extend(["--batch", batch_id])
    if node_id:
        argv.extend(["--node", node_id])
    if dry_run:
        argv.append("--dry-run")
    return argv


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/notebook")
async def create_notebook(
    req: NotebookRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    akms = _resolve_akms(settings)
    vault = _resolve_vault(settings)
    bbt_json = _resolve_bbt_json(settings)

    collection = req.collection or req.batch_id
    argv = build_notebook_argv(
        akms, vault, bbt_json, collection,
        max_papers=req.max_papers, dry_run=req.dry_run,
    )
    rec = await get_registry().submit(
        f"nlm:notebook:{req.batch_id}",
        cmd_override=argv,
        cwd_override=akms,
    )
    return rec.model_dump()


@router.post("/generate")
async def generate_nodes(
    req: GenerateRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    akms = _resolve_akms(settings)

    nlm_url = req.nlm_mcp_url or settings.nlm_mcp_url
    argv = build_generate_argv(
        akms, req.batch_id,
        notebook_id=req.notebook_id,
        nlm_mcp_url=nlm_url,
        model=req.model,
        batch_size=req.batch_size,
        max_tokens=req.max_tokens,
        dry_run=req.dry_run,
        resume_from=req.resume_from,
    )
    rec = await get_registry().submit(
        f"nlm:generate:{req.batch_id}",
        cmd_override=argv,
        cwd_override=akms,
    )
    return rec.model_dump()


@router.post("/nlm-batch")
async def nlm_batch(
    req: NlmBatchRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    akms = _resolve_akms(settings)

    out_dir = req.out_dir or str(
        akms / "Sources_Evals" / "NLM" / "Outputs" / req.batch_id
    )
    argv = build_nlm_batch_argv(
        akms,
        plan_path=req.plan_path,
        out_dir=out_dir,
        prompt_file=req.prompt_file,
        timeout=req.timeout,
        output_format=req.output_format,
        batch_id=req.batch_id,
        notebook_id=req.notebook_id,
        template_file=req.template_file,
        source_ids=req.source_ids,
        profile=req.profile,
        max_retries=req.max_retries,
        force=req.force,
        dry_run=req.dry_run,
    )
    rec = await get_registry().submit(
        f"nlm:nlm-batch:{req.batch_id}",
        cmd_override=argv,
        cwd_override=akms,
    )
    return rec.model_dump()


@router.post("/validate")
async def validate_nodes(
    req: ValidateRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    akms = _resolve_akms(settings)

    argv = build_validate_argv(
        akms,
        batch_id=req.batch_id,
        node_id=req.node_id,
        concurrency=req.concurrency,
        timeout=req.timeout,
        dry_run=req.dry_run,
    )
    rec = await get_registry().submit(
        f"nlm:validate:{req.batch_id}",
        cmd_override=argv,
        cwd_override=akms,
    )
    return rec.model_dump()


@router.get("/runs")
def list_runs() -> dict[str, Any]:
    all_runs = get_registry().list(limit=200)
    nlm_runs = [r for r in all_runs if r.stage.startswith("nlm:")]
    return {"runs": [r.model_dump() for r in nlm_runs]}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    rec = get_registry().get(run_id)
    if rec is None:
        raise HTTPException(404, detail=f"run {run_id} not found")
    return rec.model_dump()


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, str]:
    ok = get_registry().cancel(run_id)
    if not ok:
        raise HTTPException(409, detail="run not running or already terminated")
    return {"status": "cancelling"}


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> EventSourceResponse:
    reg = get_registry()
    if reg.get(run_id) is None:
        raise HTTPException(404, detail=f"run {run_id} not found")

    async def gen():
        async for evt in reg.stream(run_id):
            yield {"event": "log", "data": json.dumps(evt.model_dump())}
        final = reg.get(run_id)
        yield {"event": "done", "data": json.dumps(final.model_dump() if final else {})}

    return EventSourceResponse(gen())
