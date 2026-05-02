# Catalog Packs and Readiness Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add declarative catalog pack import plus a readiness harness that proves registered toolsets can boot and list tools without eager activation.

**Status:** Implemented and verified in this session; the checklist below preserves the original execution plan.

**Architecture:** Introduce pack/readiness models in `toolbox.models`, service methods in `toolbox.service`, MCP tool wrappers in `toolbox.server`, and focused tests in `tests/test_service.py`. Catalog import reuses `ToolsetRecord`; readiness uses active runtime probes or temporary `TransportManager.open_runtime` calls.

**Tech Stack:** Python 3.12, Pydantic, pytest, pytest-asyncio, MCP FastMCP.

---

### Task 1: Catalog Pack Models and Validation

**Files:**
- Modify: `toolbox/models.py`
- Modify: `toolbox/service.py`
- Test: `tests/test_service.py`

- [ ] **Step 1: Write failing tests for pack validation**

Add tests that create a valid pack file, a duplicate-namespace pack, and an unsupported-version pack. Assert `validate_catalog_pack(path)` returns `valid=True` for the first and structured errors for the other two.

- [ ] **Step 2: Run tests to verify red**

Run: `pytest tests/test_service.py -k "catalog_pack_validation" -q`

Expected: tests fail because `validate_catalog_pack` does not exist.

- [ ] **Step 3: Add minimal models and validation method**

Add `CatalogPackToolset`, `CatalogPack`, `CatalogPackValidationResult`, and helper validation in `ToolboxService`.

- [ ] **Step 4: Run tests to verify green**

Run: `pytest tests/test_service.py -k "catalog_pack_validation" -q`

Expected: tests pass.

### Task 2: Catalog Pack Import

**Files:**
- Modify: `toolbox/models.py`
- Modify: `toolbox/service.py`
- Modify: `toolbox/server.py`
- Test: `tests/test_service.py`

- [ ] **Step 1: Write failing tests for dry-run, create, and update**

Assert `import_catalog_pack(path, dry_run=True)` reports would-register without writing state, `dry_run=False` writes records, and `update_existing=True` updates metadata while preserving redacted public output.

- [ ] **Step 2: Run tests to verify red**

Run: `pytest tests/test_service.py -k "catalog_pack_import" -q`

Expected: tests fail because import support is missing.

- [ ] **Step 3: Implement import**

Normalize each pack toolset through `ToolsetRecord`, require valid namespaces, reject duplicate pack namespaces, skip existing records when `update_existing=False`, and append register audit events for successful create/update operations.

- [ ] **Step 4: Expose MCP tool**

Add `validate_catalog_pack` and `import_catalog_pack` wrappers in `create_server`.

- [ ] **Step 5: Run tests to verify green**

Run: `pytest tests/test_service.py -k "catalog_pack_import" -q`

Expected: tests pass.

### Task 3: Required Env Metadata

**Files:**
- Modify: `toolbox/models.py`
- Modify: `toolbox/service.py`
- Test: `tests/test_service.py`

- [ ] **Step 1: Write failing tests for required env visibility**

Assert imported `required_env` names appear in registration/status/readiness metadata and env values never appear.

- [ ] **Step 2: Run tests to verify red**

Run: `pytest tests/test_service.py -k "required_env" -q`

Expected: tests fail because records do not expose required env metadata.

- [ ] **Step 3: Add required env support**

Add `required_env: list[str]` to `ToolsetRecord`, normalize it with existing string de-dupe rules, include it in public registration/list/status outputs, and include missing env names in readiness.

- [ ] **Step 4: Run tests to verify green**

Run: `pytest tests/test_service.py -k "required_env" -q`

Expected: tests pass.

### Task 4: Readiness Harness

**Files:**
- Modify: `toolbox/models.py`
- Modify: `toolbox/service.py`
- Modify: `toolbox/server.py`
- Test: `tests/test_service.py`

- [ ] **Step 1: Write failing tests for inactive and active readiness**

Assert inactive readiness can temporarily boot the fake toolset without adding loaded scopes, active readiness probes the mounted runtime, and `refresh_cache=True` stores an observed schema snapshot.

- [ ] **Step 2: Run tests to verify red**

Run: `pytest tests/test_service.py -k "readiness" -q`

Expected: tests fail because `check_toolset_readiness` is missing.

- [ ] **Step 3: Implement readiness models and service**

Return per-namespace status with metadata status, env status, cache status, boot status, observed schema hash, observed tool count, warnings, errors, and recommended next action.

- [ ] **Step 4: Expose MCP tool**

Add `check_toolset_readiness` to `create_server`.

- [ ] **Step 5: Run tests to verify green**

Run: `pytest tests/test_service.py -k "readiness" -q`

Expected: tests pass.

### Task 5: Documentation and Verification

**Files:**
- Modify: `README.md`
- Modify: `HANDOFF.md`

- [ ] **Step 1: Document the agent-first workflow**

Add a compact README section for catalog packs and readiness checks, including a minimal manifest example and the recommended agent flow.

- [ ] **Step 2: Run focused tests**

Run: `pytest tests/test_service.py -k "catalog_pack or readiness or required_env" -q`

Expected: all selected tests pass.

- [ ] **Step 3: Run full suite**

Run: `pytest -q`

Expected: full suite passes.

- [ ] **Step 4: Run git hygiene checks**

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 5: Commit**

Run:

```bash
git add docs/superpowers/specs/2026-05-01-catalog-packs-readiness-harness-design.md docs/superpowers/plans/2026-05-01-catalog-packs-readiness-harness.md toolbox tests README.md HANDOFF.md
git commit -m "feat: add catalog packs and readiness checks"
```
