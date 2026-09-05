"""Reconcile stale Run and Runtime Binding state before a ``w/`` picker run."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import runtime_binding, state_store


@dataclass
class RecoveryReport:
    """Small, serializable result returned to the UserPromptSubmit adapter.

    ``database_ok`` and ``can_continue`` stay internal to the adapter.  The
    packet sent to Codex contains only the compact recovery status and active
    WorkItem summaries.
    """

    status: str = "none"
    recovered_run_id: str | None = None
    binding_deleted: bool = False
    active_work_items: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    database_ok: bool = True
    can_continue: bool = True

    def as_packet(self) -> dict[str, Any]:
        """Return the public recovery fields used in the selection packet."""

        return {
            "status": self.status,
            "run_id": self.recovered_run_id,
            "binding_deleted": self.binding_deleted,
            "database_ok": self.database_ok,
            "active_work_items": self.active_work_items,
            "warnings": list(dict.fromkeys(self.warnings)),
        }


def _warning_for_health(health: dict[str, Any]) -> str:
    """Summarize failed health checks without exposing database internals."""

    reasons: list[str] = []
    if health.get("integrity") != ["ok"]:
        reasons.append("integrity")
    if not health.get("foreign_keys_enabled", False):
        reasons.append("foreign_keys")
    if health.get("foreign_key_violations"):
        reasons.append("foreign_key_violations")
    if health.get("missing_tables"):
        reasons.append("missing_tables")
    if health.get("missing_views"):
        reasons.append("missing_views")
    return "database_unhealthy" + (":" + ",".join(reasons) if reasons else "")


def _compact_active_work_item(
    run: dict[str, Any],
    *,
    owner: dict[str, str] | None,
    current_session_id: str,
) -> dict[str, Any]:
    """Keep only the fields Codex needs to understand an active conflict."""

    owner_session_id = owner.get("session_id") if owner else None
    if owner_session_id == current_session_id:
        ownership = "current_session"
    elif owner_session_id is None:
        ownership = "unknown"
    else:
        ownership = "other_session"
    return {
        "run_id": run["id"],
        "work_item_id": run["work_item_id"],
        "title": run.get("work_item_title"),
        "goal": run.get("work_item_goal"),
        "next_action": run.get("work_item_next_action"),
        "priority": run.get("work_item_priority"),
        "owner_session_id": owner_session_id,
        "ownership": ownership,
    }


def _active_work_items(
    running_runs: list[dict[str, Any]],
    *,
    current_session_id: str,
    bindings_directory: str | Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Attach safe ownership labels to each still-running Run."""

    active: list[dict[str, Any]] = []
    warnings: list[str] = []
    for run in running_runs:
        try:
            owner = runtime_binding.find_run_owner(
                run["id"], bindings_directory=bindings_directory
            )
        except runtime_binding.RuntimeBindingError as error:
            warnings.append(f"run_owner_lookup_failed:{run['id']}:{type(error).__name__}")
            owner = None
        active.append(
            _compact_active_work_item(
                run,
                owner=owner,
                current_session_id=current_session_id,
            )
        )
    return active, warnings


def reconcile_current_session(
    *,
    session_id: str,
    current_turn_id: str,
    database_path: str | Path = state_store.DEFAULT_DATABASE_PATH,
    bindings_directory: str | Path = runtime_binding.DEFAULT_BINDINGS_DIRECTORY,
) -> RecoveryReport:
    """Safely reconcile this session before opening the ``w/`` picker.

    Only a Binding owned by ``session_id`` can be changed.  A Binding from a
    previous Turn is stale only when its Run is still ``running`` and its
    WorkItem is ``in_progress``.  Runs owned by another session are reported,
    never taken over automatically.
    """

    try:
        health = state_store.check_database_health(database_path)
    except Exception as error:
        return RecoveryReport(
            status="error",
            warnings=[f"database_health_check_failed:{type(error).__name__}"],
            database_ok=False,
            can_continue=False,
        )
    if not health.get("ok", False):
        warning = _warning_for_health(health)
        return RecoveryReport(
            status="error",
            warnings=[warning],
            database_ok=False,
            can_continue=False,
        )

    report = RecoveryReport()
    try:
        running_runs = state_store.list_running_runs(database_path=database_path)
    except Exception as error:
        return RecoveryReport(
            status="error",
            warnings=[f"running_run_lookup_failed:{type(error).__name__}"],
            database_ok=True,
            can_continue=False,
        )
    running_by_id = {run["id"]: run for run in running_runs}
    try:
        binding = runtime_binding.load_binding(
            session_id, bindings_directory=bindings_directory
        )
    except runtime_binding.RuntimeBindingError as error:
        return RecoveryReport(
            status="error",
            warnings=[f"current_binding_read_failed:{type(error).__name__}"],
            database_ok=True,
            can_continue=False,
        )
    if binding is not None:
        run = running_by_id.get(binding["run_id"])
        if run is None:
            try:
                    run = state_store.get_run(
                        binding["run_id"], database_path=database_path
                    )
            except state_store.NotFoundError:
                # The Run was already removed; the pointer is no longer useful.
                # Compare-and-set deletion still prevents deleting a replacement.
                try:
                    report.binding_deleted = runtime_binding.delete_binding(
                        session_id,
                        expected_run_id=binding["run_id"],
                        bindings_directory=bindings_directory,
                    )
                except runtime_binding.RuntimeBindingError as error:
                    report.status = "warning"
                    report.can_continue = False
                    report.warnings.append(
                        f"missing_run_binding_cleanup_failed:{type(error).__name__}"
                    )
                run = None
        if run is None:
            pass
        elif run["work_item_id"] != binding["work_item_id"]:
            report.status = "error"
            report.can_continue = False
            report.warnings.append("binding_work_item_mismatch")
        else:
            try:
                work_item = state_store.get_work_item(
                    binding["work_item_id"], database_path=database_path
                )
            except Exception as error:
                report.status = "error"
                report.can_continue = False
                report.warnings.append(
                    f"binding_work_item_lookup_failed:{type(error).__name__}"
                )
                work_item = None
            if work_item is None:
                pass
            else:
                is_active = (
                    run["status"] == "running"
                    and work_item["status"] == "in_progress"
                )
                if is_active and binding["turn_id"] != current_turn_id:
                    try:
                        state_store.recover_stale_run(
                            binding["work_item_id"],
                            expected_run_id=binding["run_id"],
                            actor="user_prompt_submit_hook",
                            database_path=database_path,
                        )
                    except state_store.StateStoreError as error:
                        report.status = "warning"
                        report.can_continue = False
                        report.warnings.append(
                            f"stale_run_recovery_failed:{type(error).__name__}"
                        )
                    else:
                        report.status = "recovered"
                        report.recovered_run_id = binding["run_id"]
                        try:
                            report.binding_deleted = runtime_binding.delete_binding(
                                session_id,
                                expected_run_id=binding["run_id"],
                                bindings_directory=bindings_directory,
                            )
                        except runtime_binding.RuntimeBindingError as error:
                            report.warnings.append(
                                f"stale_binding_cleanup_failed:{type(error).__name__}"
                            )
                elif is_active:
                    report.status = "already_active"
                else:
                    # A finished/missing Run is a pointer cleanup only.  The
                    # missing Run case is handled above so a stale pointer
                    # cannot keep a session blocked forever.
                    try:
                        report.binding_deleted = runtime_binding.delete_binding(
                            session_id,
                            expected_run_id=binding["run_id"],
                            bindings_directory=bindings_directory,
                        )
                    except runtime_binding.RuntimeBindingError as error:
                        report.status = "warning"
                        report.can_continue = False
                        report.warnings.append(
                            f"finished_binding_cleanup_failed:{type(error).__name__}"
                        )

    try:
        running_runs = state_store.list_running_runs(database_path=database_path)
    except Exception as error:
        report.status = "error"
        report.can_continue = False
        report.warnings.append(f"running_run_refresh_failed:{type(error).__name__}")
        return report
    report.active_work_items, owner_warnings = _active_work_items(
        running_runs,
        current_session_id=session_id,
        bindings_directory=bindings_directory,
    )
    report.warnings.extend(owner_warnings)
    if any(item["ownership"] == "other_session" for item in report.active_work_items):
        if report.status == "none":
            report.status = "warning"
        report.warnings.append("other_session_running")
    if any(item["ownership"] == "unknown" for item in report.active_work_items):
        if report.status == "none":
            report.status = "warning"
        report.warnings.append("run_owner_unknown")
    return report
