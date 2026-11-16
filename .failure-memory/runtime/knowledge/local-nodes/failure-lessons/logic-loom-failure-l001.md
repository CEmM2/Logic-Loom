---
id: "logic-loom-failure-l001"
title: "L001: JobRegistry ThreadPoolExecutor worker threads block interpreter shutdown"
domain: "logic-loom"
subdomain: "failure-memory"
tags: ["area-backend","found-by-ci","line-hint-jobregistry-threadpoolexecutor-worker-threads-block-inter-d1e7149aaefb","module-services-failure-memory-jobs","package-logic-loom-api","symbol-jobregistry"]
status: "established"
confidence: 0.99
source: "human"
confidence_floor: 0.95
edges: []
load_with: []
context_size: "small"
reading_priority: "full"
content_ref: ".failure-memory/runtime/knowledge/local-nodes/failure-lessons/logic-loom-failure-l001.md"
akms_schema: "v2"
---
<!-- GENERATED: edit .failure-memory/lessons.json -->
<!-- source_sha256: 46e78324619550d7a12f47c4bb8cf83dc62286dbc07bd373b1e4515bd3b2490d -->

# L001: JobRegistry ThreadPoolExecutor worker threads block interpreter shutdown

## Identity

- Registry ID: L001

## Location

- Area: backend
- Package: logic_loom_api
- Module: services.failure_memory_jobs
- File: services/logic_loom_api/src/logic_loom_api/services/failure_memory_jobs.py
- Symbol: JobRegistry
- Line hint: JobRegistry ThreadPoolExecutor worker threads block interpreter shutdown

## Discovery

- Type: ci
- Trigger: backend E2E workflow, windows-latest runner, pull_request event; exit code 1 recorded after the full test run reported success

## Failure

### Symptom

Backend E2E on windows-latest exits 1 even though all 819 tests pass. pytest's own summary is fully green; the failure only shows up as the process's final exit code, with a KeyboardInterrupt raised inside threading.py:355, which is Condition.wait().

### Root cause

JobRegistry creates a ThreadPoolExecutor to run failure-memory operator-mode jobs in the background. Its worker threads are not daemons, so they park in Condition.wait() waiting for the next task. concurrent.futures registers its own atexit hook that joins every live worker thread at interpreter shutdown, and non-daemon threads keep the process alive until that join returns. On a full Windows suite run this was measured directly: 11 such worker threads are still alive when the test session ends, so the interpreter cannot exit cleanly even though every test already passed.

### Fix

Not resolved as of v0.2.0; shipped as a known issue. Two changes landed in this release and neither one fixed it, which matters more than the fact that a fix is still pending: (1) making ThreadPoolExecutor construction lazy (only created on first job submission) — this only helps a JobRegistry that never submits a job, and the real backend E2E suite submits jobs, so it does not touch the actual failure; (2) registering an atexit hook that calls executor.shutdown() for each pool — a single process that submits one job and exits does shut down cleanly in isolation, but the full Windows backend E2E run still hangs, because many JobRegistry instances are constructed across the suite (one per test/fixture) and their worker threads accumulate faster than the shutdown hooks retire them. Product behaviour is unaffected in normal use; this is a CI exit-code defect only, and the job runs solely on pull requests, so it had never run against master before this release.

### Prevention

A background worker whose lifetime should not outlive its owner must either (a) run as a daemon thread, or (b) be shut down deterministically by an owner with a real lifecycle — an explicit close()/shutdown() called on every code path that constructs one, including test fixtures and teardown, not a best-effort atexit hook layered on top of a non-daemon pool. Separately: CI must treat 'all tests passed' and 'the process exited cleanly' as two different claims — a green pytest summary does not guarantee a clean process exit — and any job that only runs on pull_request should also run against the default branch, since this exact defect could otherwise ship silently because it had never once run on master.

## Timeline

- Found: 2026-08-17 (exact)
- Fixed: 2026-08-17 (approximate)

## References

- Commit: a2ac8f33d77fef3c614610fb42f6796dfebbe74f
- Issue:
- PR: 140
- Plan:
