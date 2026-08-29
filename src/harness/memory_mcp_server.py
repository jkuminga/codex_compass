"""Read-only MCP tools that compare State DB Candidates with MemoryGraph.

The server is a thin adapter: State DB lookup stays in ``state_store.py`` and
MemoryGraph search/ranking stays in the TypeScript memory Module.
"""

from __future__ import annotations

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
) -> MCPServer:
    """Create the Memory MCP Adapter bound to one State DB."""

    server = MCPServer(
        name="harness-memory",
        title="Harness Long-Term Memory",
        version="0.1.0",
        instructions=(
            "Use inspect_memory_candidate to read a pending Candidate and "
            "compare it with existing MemoryGraph records before finalization."
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

    return server


mcp = create_server(
    os.environ.get("HARNESS_STATE_DB", state_store.DEFAULT_DATABASE_PATH)
)


def main() -> None:
    """Run the local Memory MCP Adapter over stdio for Codex."""

    mcp.run()


if __name__ == "__main__":
    main()
