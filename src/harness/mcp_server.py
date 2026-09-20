"""Codex-facing MCP tools for the Harness v2 state store.

This module is a thin adapter. Tool descriptions and input shapes live here;
all state validation, transactions, and SQL remain in ``state_store.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer

from . import state_store


def create_server(
    database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
) -> MCPServer:
    """Create an MCP server bound to one local Harness state database."""

    server = MCPServer(
        name="harness-state",
        title="Harness State Store",
        version="0.1.0",
        instructions=(
            "Use these tools to read and change Harness project state. "
            "Read context before starting work, record verifiable results, "
            "and finish the active Run before reporting completion."
        ),
    )

    @server.tool(name="get_project_status")
    def get_project_status(
        next_limit: int = 10,
        activity_limit: int = 20,
    ) -> dict[str, Any]:
        """Read overall progress, ready WorkItems, and recent state changes.

        Use at the beginning of a task when no WorkItem has been selected, or
        whenever the current Project direction is unclear. This tool is
        read-only and does not create a Run.
        """

        return {
            "progress": state_store.get_project_progress(database_path=database_path),
            "next_work_items": state_store.list_next_work_items(
                limit=next_limit, database_path=database_path
            ),
            "recent_activity": state_store.get_recent_activity(
                limit=activity_limit, database_path=database_path
            ),
        }

    @server.tool(name="get_work_context")
    def get_work_context(work_item_id: str) -> dict[str, Any]:
        """Read the compact preflight context for one selected WorkItem.

        Call before editing files or starting a Run for the WorkItem. The
        result includes its goal, next action, Acceptance Criteria, Feature,
        active Run, recent Runs, and completion verification.
        """

        return state_store.get_preflight_context(
            work_item_id, database_path=database_path
        )

    @server.tool(name="search_work_items")
    def search_work_items(
        terms: list[str],
        statuses: list[Literal["backlog", "ready", "blocked"]] | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Search open WorkItems when the three prompt candidates do not fit.

        Derive two to five concise terms from the user's requested goal and
        call this tool at most once before creating a new WorkItem. Results
        match title, goal, and next action and are ordered by a small relevance
        score. This tool is read-only.
        """

        return {
            "work_items": state_store.search_work_items(
                terms,
                statuses=statuses
                if statuses is not None
                else ("backlog", "ready", "blocked"),
                limit=limit,
                database_path=database_path,
            )
        }

    @server.tool(name="create_feature")
    def create_feature(
        title: str,
        goal: str,
        priority: Literal["urgent", "high", "normal", "low"] = "normal",
    ) -> dict[str, Any]:
        """Create a Feature, the large capability grouping for WorkItems.

        Use during planning after the capability and goal are understood. Do
        not create a Feature for a single small implementation step.
        """

        return state_store.create_feature(
            title=title,
            goal=goal,
            priority=priority,
            actor="codex",
            database_path=database_path,
        )

    @server.tool(name="update_feature")
    def update_feature(
        feature_id: str,
        title: str | None = None,
        goal: str | None = None,
        priority: Literal["urgent", "high", "normal", "low"] | None = None,
        status: Literal[
            "planned", "active", "paused", "completed", "cancelled"
        ]
        | None = None,
        reason: str = "Feature updated",
    ) -> dict[str, Any]:
        """Revise a Feature's content, priority, or lifecycle state.

        Progress values are not editable because they are calculated from the
        Feature's WorkItems.
        """

        feature = state_store.update_feature(
            feature_id,
            title=title,
            goal=goal,
            priority=priority,
            actor="codex",
            database_path=database_path,
        )
        if status is not None and status != feature["status"]:
            feature = state_store.change_feature_status(
                feature_id,
                status,
                actor="codex",
                reason=reason,
                database_path=database_path,
            )
        return feature

    @server.tool(name="create_work_item")
    def create_work_item(
        title: str,
        kind: Literal[
            "implementation",
            "bug",
            "research",
            "decision",
            "refactor",
            "migration",
            "verification",
            "maintenance",
        ],
        goal: str,
        feature_id: str | None = None,
        priority: Literal["urgent", "high", "normal", "low"] = "normal",
        next_action: str | None = None,
        acceptance_criteria: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create one executable WorkItem and its initial completion criteria.

        New WorkItems start in backlog. Keep criteria outcome-oriented rather
        than prescribing one exact test command.
        """

        return state_store.create_work_item(
            feature_id=feature_id,
            title=title,
            kind=kind,
            goal=goal,
            priority=priority,
            next_action=next_action,
            acceptance_criteria=acceptance_criteria or (),
            actor="codex",
            database_path=database_path,
        )

    @server.tool(name="create_ready_work_item")
    def create_ready_work_item(
        title: str,
        kind: Literal[
            "implementation",
            "bug",
            "research",
            "decision",
            "refactor",
            "migration",
            "verification",
            "maintenance",
        ],
        goal: str,
        next_action: str,
        acceptance_criteria: list[str],
        feature_id: str | None = None,
        priority: Literal["urgent", "high", "normal", "low"] = "normal",
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a complete non-Draft WorkItem ready for a future Run.

        Use this after a user explicitly asks to register or create a complete
        WorkItem from the current conversation. The operation atomically stores
        the executable plan and Acceptance Criteria in ``ready`` state; it does
        not start a Run.
        """

        return state_store.create_ready_work_item(
            title=title,
            kind=kind,
            goal=goal,
            next_action=next_action,
            acceptance_criteria=acceptance_criteria,
            feature_id=feature_id,
            priority=priority,
            description=description,
            actor="codex",
            database_path=database_path,
        )

    @server.tool(name="create_draft_work_item")
    def create_draft_work_item(
        title: str,
        kind: Literal[
            "implementation",
            "bug",
            "research",
            "decision",
            "refactor",
            "migration",
            "verification",
            "maintenance",
        ],
        goal: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a Draft WorkItem from the user's short task memo.

        A Draft remains backlog-only and has no Acceptance Criteria until Codex
        refines it after the user explicitly selects it with ``w/``.
        """

        return state_store.create_draft_work_item(
            title=title,
            kind=kind,
            goal=goal,
            description=description,
            actor="web_console",
            database_path=database_path,
        )

    @server.tool(name="refine_draft_work_item")
    def refine_draft_work_item(
        work_item_id: str,
        priority: Literal["urgent", "high", "normal", "low"],
        next_action: str,
        acceptance_criteria: list[str],
        feature_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist Codex's executable plan for one selected Draft WorkItem.

        This is not a web-console action. Call it only after a user selected a
        Draft through ``w/`` and Codex has decided its next action and outcome
        focused Acceptance Criteria. On success it makes the WorkItem ready.
        """

        return state_store.refine_draft_work_item(
            work_item_id,
            priority=priority,
            next_action=next_action,
            acceptance_criteria=acceptance_criteria,
            feature_id=feature_id,
            actor="codex",
            database_path=database_path,
        )

    @server.tool(name="revise_work_item")
    def revise_work_item(
        work_item_id: str,
        title: str | None = None,
        kind: Literal[
            "implementation",
            "bug",
            "research",
            "decision",
            "refactor",
            "migration",
            "verification",
            "maintenance",
        ]
        | None = None,
        goal: str | None = None,
        priority: Literal["urgent", "high", "normal", "low"] | None = None,
        next_action: str | None = None,
    ) -> dict[str, Any]:
        """Revise the mutable planning fields of a non-terminal WorkItem.

        Omitted values stay unchanged. Terminal WorkItems preserve their final
        historical meaning and cannot be revised.
        """

        arguments: dict[str, Any] = {
            "title": title,
            "kind": kind,
            "goal": goal,
            "priority": priority,
            "actor": "codex",
            "database_path": database_path,
        }
        if next_action is not None:
            arguments["next_action"] = next_action
        return state_store.revise_work_item(work_item_id, **arguments)

    @server.tool(name="manage_criterion")
    def manage_criterion(
        action: Literal["add", "revise", "waive"],
        work_item_id: str | None = None,
        criterion_id: str | None = None,
        description: str | None = None,
        reason: str | None = None,
        sort_order: int | None = None,
    ) -> dict[str, Any]:
        """Add, revise, or explicitly waive one Acceptance Criterion.

        add needs work_item_id and description; revise needs criterion_id and
        description; waive needs criterion_id and a concrete reason. Evidence
        judgments belong to verify_criterion instead.
        """

        if action == "add":
            if work_item_id is None or description is None:
                raise state_store.ConflictError(
                    "add requires work_item_id and description"
                )
            return state_store.add_criterion(
                work_item_id,
                description,
                sort_order=sort_order,
                actor="codex",
                database_path=database_path,
            )
        if action == "revise":
            if criterion_id is None or description is None:
                raise state_store.ConflictError(
                    "revise requires criterion_id and description"
                )
            return state_store.revise_criterion(
                criterion_id,
                description,
                actor="codex",
                database_path=database_path,
            )
        if criterion_id is None or reason is None:
            raise state_store.ConflictError(
                "waive requires criterion_id and reason"
            )
        return state_store.waive_criterion(
            criterion_id,
            actor="codex",
            reason=reason,
            database_path=database_path,
        )

    @server.tool(name="change_work_item_state")
    def change_work_item_state(
        work_item_id: str,
        status: Literal["ready", "blocked", "cancelled"],
        reason: str,
        next_action: str | None = None,
        block_reason: str | None = None,
    ) -> dict[str, Any]:
        """Prepare, block, or cancel a WorkItem outside Run finalization.

        A ready or blocked WorkItem requires a concrete next action. A blocked
        WorkItem also requires block_reason. Active Runs must be finalized by
        finish_work instead of this tool.
        """

        return state_store.change_work_item_status(
            work_item_id,
            status,
            actor="codex",
            reason=reason,
            next_action=next_action,
            block_reason=block_reason,
            database_path=database_path,
        )

    @server.tool(name="close_work_item")
    def close_work_item(work_item_id: str, reason: str) -> dict[str, Any]:
        """Close one ready WorkItem after an explicit user completion request.

        This tool does not create or finish a Run. Use the exact WorkItem ID
        selected by the user through ``w/`` or supplied by the web console.
        The state store rechecks the status, active Run, Acceptance Criteria,
        and Evidence before changing the WorkItem to done.
        """

        return state_store.close_work_item(
            work_item_id,
            actor="codex",
            reason=reason,
            database_path=database_path,
        )

    @server.tool(name="start_work")
    def start_work(
        work_item_id: str,
        intent: str,
        recall_query: str,
        trace_ref: str | None = None,
    ) -> dict[str, Any]:
        """Start one ready WorkItem and create its single active Run.

        Call only after get_work_context has confirmed the goal, next action,
        and Acceptance Criteria. ``intent`` is a short natural-language summary
        of this Run. ``recall_query`` is two to five space-separated search
        keywords chosen by Codex; the Hook uses it without inferring keywords.
        A WorkItem cannot have two active Runs.
        """

        return state_store.start_run(
            work_item_id,
            intent=intent,
            recall_query=recall_query,
            trace_ref=trace_ref,
            actor="codex",
            database_path=database_path,
        )

    @server.tool(name="record_artifact")
    def record_artifact(
        run_id: str,
        kind: Literal[
            "file",
            "commit",
            "test_run",
            "lint_run",
            "build_run",
            "pull_request",
            "deployment",
            "screenshot",
            "report",
            "other",
        ],
        uri: str,
        verification_status: Literal[
            "not_applicable", "pending", "passed", "failed"
        ],
        summary: str,
    ) -> dict[str, Any]:
        """Register a file, test, build, commit, or other verifiable Run result.

        Store only a short summary and URI; keep full output in the referenced
        file, Trace, Git, or CI system. Repeated test, lint, and build executions
        use ``command:<command-family>:<YYYYMMDDTHHMMSSZ>`` so each attempt has
        its own Artifact. Add a 6-12 character lowercase suffix only when two
        executions start in the same second.
        """

        return state_store.create_artifact(
            run_id,
            kind=kind,
            uri=uri,
            verification_status=verification_status,
            summary=summary,
            actor="codex",
            database_path=database_path,
        )

    @server.tool(name="resolve_artifact")
    def resolve_artifact(
        artifact_id: str,
        verification_status: Literal["passed", "failed"],
        summary: str,
    ) -> dict[str, Any]:
        """Finalize one pending Artifact as passed or failed.

        Final Artifact verification cannot be rewritten later.
        """

        return state_store.resolve_artifact(
            artifact_id,
            verification_status,
            summary=summary,
            actor="codex",
            database_path=database_path,
        )

    @server.tool(name="verify_criterion")
    def verify_criterion(
        criterion_id: str,
        artifact_id: str,
        result: Literal["passed", "failed"],
        note: str | None = None,
        reason: str = "Verification result recorded",
    ) -> dict[str, Any]:
        """Link an Artifact as Evidence and judge one Acceptance Criterion.

        A passed judgment succeeds only when the Artifact is valid Evidence
        from the same WorkItem. Use failed when the result does not satisfy the
        Criterion; later valid Evidence may still pass it.
        """

        evidence = state_store.link_evidence(
            criterion_id,
            artifact_id,
            note=note,
            actor="codex",
            database_path=database_path,
        )
        if result == "passed":
            criterion = state_store.pass_criterion(
                criterion_id,
                actor="codex",
                reason=reason,
                database_path=database_path,
            )
        else:
            criterion = state_store.fail_criterion(
                criterion_id,
                actor="codex",
                reason=reason,
                database_path=database_path,
            )
        return {"evidence": evidence, "criterion": criterion}

    @server.tool(name="get_postflight_status")
    def get_postflight_status(work_item_id: str) -> dict[str, Any]:
        """Read the completion checklist before finishing the active Run.

        Inspect unresolved Acceptance Criteria, Artifacts, and pending Memory
        Candidates. Resolve meaningful omissions before calling finish_work.
        """

        return state_store.get_postflight_status(
            work_item_id, database_path=database_path
        )

    @server.tool(name="finish_work")
    def finish_work(
        run_id: str,
        outcome: Literal[
            "progressed",
            "retry_needed",
            "blocked",
            "interrupted",
            "cancelled",
        ],
        summary: str,
        reason: str,
        completion_recommended: bool = False,
        next_action: str | None = None,
        block_reason: str | None = None,
        termination_reason: str | None = None,
    ) -> dict[str, Any]:
        """Atomically finish a Run and move its WorkItem to the matching state.

        progressed means this Run succeeded and the WorkItem returns to ready.
        Set completion_recommended when its stored completion proof is valid;
        the state store then supplies the fixed user-completion prompt. Otherwise
        provide a concrete next_action. retry_needed means failed/ready; blocked
        means interrupted/blocked; interrupted means interrupted/ready; cancelled
        closes both. Non-successful Runs need a termination reason.
        """

        mapping = {
            "progressed": ("succeeded", "ready"),
            "retry_needed": ("failed", "ready"),
            "blocked": ("interrupted", "blocked"),
            "interrupted": ("interrupted", "ready"),
            "cancelled": ("cancelled", "cancelled"),
        }
        run_status, work_item_status = mapping[outcome]
        resolved_termination_reason = (
            None if run_status == "succeeded" else termination_reason or reason
        )
        return state_store.finish_run(
            run_id,
            run_status=run_status,
            work_item_status=work_item_status,
            summary=summary,
            actor="codex",
            reason=reason,
            termination_reason=resolved_termination_reason,
            next_action=next_action,
            block_reason=block_reason,
            completion_recommended=completion_recommended,
            database_path=database_path,
        )

    @server.tool(name="recover_abandoned_work")
    def recover_abandoned_work(
        work_item_id: str,
        expected_run_id: str,
        user_confirmation: Literal["confirmed"],
    ) -> dict[str, Any]:
        """Recover a WorkItem only after the user confirms its session ended.

        When the requested WorkItem is owned by another session, first explain
        the conflict and ask whether that session is gone and this session
        should recover the work. Call this tool only after an explicit yes.
        Pass the active Run ID that was shown with the conflict; recovery fails
        if that Run changed, so a live or newly resumed session is not replaced.
        After recovery, call start_work separately to create this session's Run.
        """

        if user_confirmation != "confirmed":
            raise state_store.ConflictError("explicit user confirmation is required")
        return state_store.recover_abandoned_work(
            work_item_id,
            expected_run_id=expected_run_id,
            actor="codex",
            reason="사용자가 중단된 다른 세션의 작업 복구를 확인함",
            database_path=database_path,
        )

    @server.tool(name="create_memory_candidate")
    def create_memory_candidate(
        run_id: str,
        proposed_type: Literal[
            "task",
            "code_pattern",
            "problem",
            "solution",
            "project",
            "technology",
            "error",
            "fix",
            "command",
            "file_context",
            "workflow",
            "general",
            "conversation",
        ],
        title: str,
        content: str,
        keywords: list[str],
    ) -> dict[str, Any]:
        """Immediately capture one potentially durable Memory Candidate.

        Use during an active Run when a reusable decision, problem and fix,
        technique, command, or project fact is discovered. Do not store routine
        progress, guesses, secrets, or one-off details.
        """

        return state_store.create_candidate(
            run_id,
            proposed_type=proposed_type,
            title=title,
            content=content,
            keywords=keywords,
            database_path=database_path,
        )

    @server.tool(name="list_memory_candidates")
    def list_memory_candidates(run_id: str | None = None) -> dict[str, Any]:
        """List pending Memory Candidates, optionally limited to one Run.

        Use during memory finalization before promoting or rejecting candidates.
        """

        return {
            "candidates": state_store.list_pending_candidates(
                run_id=run_id, database_path=database_path
            )
        }

    return server


mcp = create_server(os.environ.get("HARNESS_STATE_DB", state_store.DEFAULT_DATABASE_PATH))


def main() -> None:
    """Run the local MCP server over stdio for Codex."""

    state_store.initialize_database(
        os.environ.get("HARNESS_STATE_DB", state_store.DEFAULT_DATABASE_PATH)
    )
    mcp.run()


if __name__ == "__main__":
    main()
