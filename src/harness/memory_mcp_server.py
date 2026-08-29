"""MCP tools that inspect and finalize State DB Candidates in MemoryGraph.

The server is a thin adapter: State DB lookup stays in ``state_store.py`` and
MemoryGraph search/ranking stays in the TypeScript memory Module.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from mcp.server import MCPServer

from . import state_store


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MEMORY_CLI = PROJECT_ROOT / "bin" / "harness-memory"
InspectRunner = Callable[[dict[str, Any]], Mapping[str, Any]]
FinalizeRunner = Callable[[dict[str, Any]], Mapping[str, Any]]


def _run_memory_inspector(payload: dict[str, Any]) -> Mapping[str, Any]:
    """Send one Candidate packet to the project-local MemoryGraph Module."""

    completed = subprocess.run(
        [str(MEMORY_CLI), "inspect"],
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"memory inspector exited with status {completed.returncode}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError("memory inspector must return exactly one JSON line")
    response = json.loads(lines[0])
    if not isinstance(response, dict):
        raise RuntimeError("memory inspector response must be a JSON object")
    return response


def _run_memory_finalizer(payload: dict[str, Any]) -> Mapping[str, Any]:
    """Send one validated Storage Plan to the project-local graph writer."""

    completed = subprocess.run(
        [str(MEMORY_CLI), "finalize"], cwd=PROJECT_ROOT, env=os.environ.copy(),
        input=json.dumps(payload, ensure_ascii=False), text=True,
        capture_output=True, timeout=30, check=False,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or len(lines) != 1:
        raise RuntimeError("memory finalizer did not return one JSON receipt")
    response = json.loads(lines[0])
    if not isinstance(response, dict):
        raise RuntimeError("memory finalizer response must be a JSON object")
    return response


def _normalize_storage_plan(storage_plan: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize plan fields so equivalent retries have one fingerprint."""

    raw_memory = storage_plan.get("memory")
    memory = None
    if isinstance(raw_memory, Mapping):
        memory = {
            "type": str(raw_memory.get("type", "")).strip().lower(),
            "title": str(raw_memory.get("title", "")).strip(),
            "content": str(raw_memory.get("content", "")).strip(),
            "summary": str(raw_memory["summary"]).strip() if raw_memory.get("summary") else None,
            "tags": sorted({str(tag).strip().lower() for tag in raw_memory.get("tags", []) if str(tag).strip()}),
            "importance": raw_memory.get("importance", 0.5),
            "confidence": raw_memory.get("confidence", 0.8),
        }
    relationships: list[dict[str, Any]] = []
    raw_relationships = storage_plan.get("relationships", [])
    if not isinstance(raw_relationships, list):
        raw_relationships = [{"invalid": raw_relationships}]
    for raw in raw_relationships:
        if not isinstance(raw, Mapping):
            relationships.append({"invalid": raw})
            continue
        relationships.append({
            "direction": str(raw.get("direction", "")).strip().lower(),
            "target_memory_id": str(raw.get("target_memory_id", "")).strip(),
            "type": str(raw.get("type", "")).strip().upper(),
            "strength": raw.get("strength", 0.5),
            "confidence": raw.get("confidence", 0.8),
            "context": str(raw["context"]).strip() if raw.get("context") else None,
        })
    relationships.sort(key=lambda item: (
        str(item.get("direction", "")), str(item.get("target_memory_id", "")), str(item.get("type", "")),
    ))
    target = storage_plan.get("target_memory_id")
    return {
        "decision": str(storage_plan.get("decision", "")).strip().lower(),
        "target_memory_id": str(target).strip() if target else None,
        "memory": memory,
        "relationships": relationships,
        "reason": str(storage_plan["reason"]).strip() if storage_plan.get("reason") else None,
    }


def _plan_fingerprint(storage_plan: Mapping[str, Any]) -> str:
    """Hash write-affecting fields; reason remains explanatory only."""

    write_plan = {key: storage_plan.get(key) for key in (
        "decision", "target_memory_id", "memory", "relationships"
    )}
    encoded = json.dumps(write_plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _finalize_error(
    candidate_id: str, code: str, message: str, *, status: str | None = None
) -> dict[str, Any]:
    """Return a stable finalization failure packet."""

    return {
        "ok": False,
        "status": status or ("conflict" if "conflict" in code else "validation_error"),
        "candidate_id": candidate_id,
        "relationships": {"created": [], "skipped": [], "failed": []},
        "warnings": [],
        "error": {"code": code, "message": message},
    }


def _adapter_error(
    *,
    code: str,
    message: str,
    candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a stable failure packet instead of leaking adapter exceptions."""

    return {
        "ok": False,
        "candidate": dict(candidate) if candidate is not None else None,
        "search": {
            "terms": [],
            "status": "not_started",
            "attempted": 0,
            "succeeded": 0,
        },
        "matches": [],
        "warnings": [],
        "error": {"code": code, "message": message},
    }


def create_server(
    database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
    *,
    inspect_runner: InspectRunner = _run_memory_inspector,
    finalize_runner: FinalizeRunner = _run_memory_finalizer,
) -> MCPServer:
    """Create the Memory MCP Adapter bound to one State DB."""

    server = MCPServer(
        name="harness-memory",
        title="Harness Long-Term Memory",
        version="0.1.0",
        instructions=(
            "Inspect a pending Candidate, then use finalize_memory_candidate "
            "to coordinate its State DB lifecycle and MemoryGraph writes."
        ),
    )

    @server.tool(name="inspect_memory_candidate")
    def inspect_memory_candidate(
        candidate_id: str,
        extra_terms: list[str] | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Read one pending Candidate and nearby existing memories.

        ``candidate_id`` identifies the State DB Candidate. Its own keywords
        are always the primary search terms. ``extra_terms`` optionally adds
        a few comparison words, and ``limit`` bounds returned existing-memory
        matches. This tool never changes Candidate or MemoryGraph state.
        """

        try:
            candidate = state_store.get_memory_candidate(
                candidate_id, database_path=database_path
            )
        except state_store.NotFoundError:
            return _adapter_error(
                code="candidate_not_found",
                message="The requested Memory Candidate does not exist.",
            )
        except state_store.ConflictError as error:
            return _adapter_error(
                code="invalid_candidate_id",
                message=str(error),
            )
        except state_store.StateStoreError:
            return _adapter_error(
                code="state_store_unavailable",
                message="The State DB could not provide the Memory Candidate.",
            )

        if candidate["status"] != "pending":
            return _adapter_error(
                code="candidate_not_pending",
                message="Only a pending Memory Candidate can be inspected.",
                candidate=candidate,
            )

        payload = {
            "candidate": candidate,
            "extra_terms": extra_terms or [],
            "limit": limit,
        }
        try:
            response = dict(inspect_runner(payload))
            required_fields = {"ok", "candidate", "search", "matches", "warnings"}
            if not required_fields.issubset(response):
                raise RuntimeError("memory inspector response is incomplete")
            return response
        except Exception:
            return _adapter_error(
                code="memory_inspector_unavailable",
                message="The project-local MemoryGraph inspector is unavailable.",
                candidate=candidate,
            )

    @server.tool(name="finalize_memory_candidate")
    def finalize_memory_candidate(
        candidate_id: str,
        storage_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Finalize one Candidate with a durable, retry-safe Storage Plan.

        The first call supplies ``storage_plan``. A retry may supply only
        ``candidate_id`` and will reuse the exact plan stored in State DB.
        """

        try:
            candidate = state_store.get_memory_candidate(candidate_id, database_path=database_path)
        except state_store.NotFoundError:
            return _finalize_error(candidate_id, "candidate_not_found", "Memory Candidate does not exist.")
        except state_store.StateStoreError:
            return _finalize_error(candidate_id, "state_store_unavailable", "State DB is unavailable.")

        if storage_plan is None:
            normalized = candidate.get("storage_plan")
            if normalized is None:
                return _finalize_error(candidate_id, "storage_plan_required", "The first finalize call requires a Storage Plan.")
        elif isinstance(storage_plan, Mapping):
            try:
                normalized = _normalize_storage_plan(storage_plan)
            except Exception:
                return _finalize_error(candidate_id, "invalid_storage_plan", "Storage Plan fields are malformed.")
        else:
            return _finalize_error(candidate_id, "invalid_storage_plan", "Storage Plan must be a JSON object.")

        fingerprint = _plan_fingerprint(normalized)
        if candidate.get("plan_fingerprint") not in (None, fingerprint):
            return _finalize_error(candidate_id, "plan_conflict", "Candidate already has a different Storage Plan.")

        if candidate["status"] in {"promoted", "rejected"}:
            return {
                "ok": True, "status": "committed", "candidate_id": candidate_id,
                "decision": normalized["decision"], "memory_ref": candidate.get("memory_ref"),
                "node_result": "rejected" if candidate["status"] == "rejected" else "skipped",
                "relationships": {"created": [], "skipped": [], "failed": []},
                "warnings": ["already_finalized"],
            }

        payload = {
            "candidate_id": candidate_id, "plan_fingerprint": fingerprint,
            "storage_plan": normalized, "mode": "validate",
        }
        try:
            validation = dict(finalize_runner(dict(payload)))
        except Exception:
            return _finalize_error(candidate_id, "memory_writer_unavailable", "MemoryGraph writer is unavailable.")
        if not validation.get("ok"):
            return validation

        try:
            state_store.reserve_candidate_finalize_plan(
                candidate_id, storage_plan=normalized, plan_fingerprint=fingerprint,
                database_path=database_path,
            )
        except state_store.ConflictError as error:
            return _finalize_error(candidate_id, "plan_conflict", str(error))

        if normalized["decision"] == "reject":
            state_store.reject_candidate(candidate_id, database_path=database_path)
            return {**validation, "ok": True, "status": "committed", "node_result": "rejected", "memory_ref": None}

        payload["mode"] = "execute"
        try:
            receipt = dict(finalize_runner(dict(payload)))
        except Exception:
            return _finalize_error(
                candidate_id, "memory_writer_unavailable",
                "The saved plan remains pending for retry.", status="partial",
            )
        if receipt.get("ok") and receipt.get("status") == "committed":
            memory_ref = receipt.get("memory_ref")
            if not isinstance(memory_ref, str) or not memory_ref.strip():
                return _finalize_error(candidate_id, "invalid_writer_receipt", "Committed receipt has no memory_ref.")
            state_store.promote_candidate(candidate_id, memory_ref=memory_ref, database_path=database_path)
        return receipt

    return server


mcp = create_server(
    os.environ.get("HARNESS_STATE_DB", state_store.DEFAULT_DATABASE_PATH)
)


def main() -> None:
    """Run the local Memory MCP Adapter over stdio for Codex."""

    mcp.run()


if __name__ == "__main__":
    main()
