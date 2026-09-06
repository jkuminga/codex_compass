-- Harness v2 local state-store schema.
--
-- The repository is the implicit Project. Runtime data lives in state.db;
-- this file is the reproducible source of truth for its structure.

PRAGMA foreign_keys = ON;

BEGIN;

CREATE TABLE IF NOT EXISTS features (
  id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
  title TEXT NOT NULL CHECK (length(trim(title)) > 0),
  goal TEXT NOT NULL CHECK (length(trim(goal)) > 0),
  status TEXT NOT NULL DEFAULT 'planned'
    CHECK (status IN ('planned', 'active', 'paused', 'completed', 'cancelled')),
  priority TEXT NOT NULL DEFAULT 'normal'
    CHECK (priority IN ('urgent', 'high', 'normal', 'low')),
  created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
  updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0)
);

CREATE TABLE IF NOT EXISTS work_items (
  id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
  feature_id TEXT REFERENCES features(id),
  title TEXT NOT NULL CHECK (length(trim(title)) > 0),
  kind TEXT NOT NULL CHECK (kind IN (
    'implementation', 'bug', 'research', 'decision', 'refactor',
    'migration', 'verification', 'maintenance'
  )),
  goal TEXT NOT NULL CHECK (length(trim(goal)) > 0),
  description TEXT,
  is_draft INTEGER NOT NULL DEFAULT 0 CHECK (is_draft IN (0, 1)),
  status TEXT NOT NULL DEFAULT 'backlog'
    CHECK (status IN ('backlog', 'ready', 'in_progress', 'blocked', 'done', 'cancelled')),
  priority TEXT NOT NULL DEFAULT 'normal'
    CHECK (priority IN ('urgent', 'high', 'normal', 'low')),
  next_action TEXT,
  block_reason TEXT,
  created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
  updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0),
  started_at TEXT,
  closed_at TEXT,
  CHECK (
    (status = 'backlog' AND (next_action IS NULL OR length(trim(next_action)) > 0))
    OR (status IN ('ready', 'in_progress', 'blocked')
      AND next_action IS NOT NULL AND length(trim(next_action)) > 0)
    OR (status IN ('done', 'cancelled') AND next_action IS NULL)
  ),
  CHECK (
    (status = 'blocked'
      AND block_reason IS NOT NULL AND length(trim(block_reason)) > 0)
    OR (status <> 'blocked' AND block_reason IS NULL)
  ),
  CHECK (
    (status IN ('done', 'cancelled')
      AND closed_at IS NOT NULL AND length(trim(closed_at)) > 0)
    OR (status NOT IN ('done', 'cancelled') AND closed_at IS NULL)
  )
);

CREATE TABLE IF NOT EXISTS acceptance_criteria (
  id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
  work_item_id TEXT NOT NULL REFERENCES work_items(id),
  description TEXT NOT NULL CHECK (length(trim(description)) > 0),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'passed', 'failed', 'waived')),
  sort_order INTEGER NOT NULL CHECK (sort_order >= 0),
  resolved_at TEXT,
  UNIQUE (work_item_id, sort_order),
  CHECK (
    (status IN ('passed', 'waived')
      AND resolved_at IS NOT NULL AND length(trim(resolved_at)) > 0)
    OR (status IN ('pending', 'failed') AND resolved_at IS NULL)
  )
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
  work_item_id TEXT NOT NULL REFERENCES work_items(id),
  intent TEXT NOT NULL DEFAULT 'legacy run'
    CHECK (length(trim(intent)) > 0),
  recall_query TEXT NOT NULL DEFAULT 'legacy'
    CHECK (length(trim(recall_query)) > 0),
  status TEXT NOT NULL DEFAULT 'running'
    CHECK (status IN ('running', 'succeeded', 'failed', 'interrupted', 'cancelled')),
  started_at TEXT NOT NULL CHECK (length(trim(started_at)) > 0),
  ended_at TEXT,
  summary TEXT,
  termination_reason TEXT,
  trace_ref TEXT,
  CHECK (
    (status = 'running'
      AND ended_at IS NULL AND summary IS NULL AND termination_reason IS NULL)
    OR (status = 'succeeded'
      AND ended_at IS NOT NULL AND length(trim(ended_at)) > 0
      AND summary IS NOT NULL AND length(trim(summary)) > 0
      AND termination_reason IS NULL)
    OR (status IN ('failed', 'interrupted', 'cancelled')
      AND ended_at IS NOT NULL AND length(trim(ended_at)) > 0
      AND summary IS NOT NULL AND length(trim(summary)) > 0
      AND termination_reason IS NOT NULL AND length(trim(termination_reason)) > 0)
  )
);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
  run_id TEXT NOT NULL REFERENCES runs(id),
  kind TEXT NOT NULL CHECK (kind IN (
    'file', 'commit', 'test_run', 'lint_run', 'build_run', 'pull_request',
    'deployment', 'screenshot', 'report', 'other'
  )),
  uri TEXT NOT NULL CHECK (length(trim(uri)) > 0),
  verification_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (verification_status IN ('not_applicable', 'pending', 'passed', 'failed')),
  summary TEXT NOT NULL CHECK (length(trim(summary)) > 0),
  created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
  UNIQUE (run_id, kind, uri),
  CHECK (
    kind NOT IN ('test_run', 'lint_run', 'build_run')
    OR verification_status <> 'not_applicable'
  )
);

CREATE TABLE IF NOT EXISTS criterion_evidence (
  criterion_id TEXT NOT NULL REFERENCES acceptance_criteria(id),
  artifact_id TEXT NOT NULL REFERENCES artifacts(id),
  note TEXT,
  created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
  PRIMARY KEY (criterion_id, artifact_id),
  CHECK (note IS NULL OR length(trim(note)) > 0)
);

CREATE TABLE IF NOT EXISTS state_events (
  id INTEGER PRIMARY KEY,
  entity_type TEXT NOT NULL CHECK (entity_type IN (
    'feature', 'work_item', 'run', 'acceptance_criterion', 'artifact'
  )),
  entity_id TEXT NOT NULL CHECK (length(trim(entity_id)) > 0),
  event_type TEXT NOT NULL CHECK (length(trim(event_type)) > 0),
  from_status TEXT,
  to_status TEXT,
  run_id TEXT REFERENCES runs(id),
  actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
  reason TEXT,
  payload_json TEXT,
  created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
  CHECK (reason IS NULL OR length(trim(reason)) > 0),
  CHECK (
    payload_json IS NULL
    OR CASE WHEN json_valid(payload_json) THEN 1 ELSE 0 END
  )
);

CREATE TABLE IF NOT EXISTS memory_candidates (
  id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
  run_id TEXT NOT NULL REFERENCES runs(id),
  proposed_type TEXT NOT NULL CHECK (proposed_type IN (
    'task', 'code_pattern', 'problem', 'solution', 'project',
    'technology', 'error', 'fix', 'command', 'file_context',
    'workflow', 'general', 'conversation'
  )),
  title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
  content TEXT NOT NULL CHECK (length(trim(content)) > 0),
  keywords_json TEXT NOT NULL DEFAULT '[]' CHECK (
    CASE
      WHEN json_valid(keywords_json) THEN json_type(keywords_json) = 'array'
      ELSE 0
    END
  ),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'promoted', 'rejected')),
  memory_ref TEXT,
  storage_plan_json TEXT CHECK (
    storage_plan_json IS NULL OR json_valid(storage_plan_json)
  ),
  plan_fingerprint TEXT CHECK (
    plan_fingerprint IS NULL OR length(trim(plan_fingerprint)) = 64
  ),
  created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
  CHECK ((storage_plan_json IS NULL) = (plan_fingerprint IS NULL)),
  CHECK (
    (status IN ('pending', 'rejected') AND memory_ref IS NULL)
    OR (status = 'promoted'
      AND memory_ref IS NOT NULL AND length(trim(memory_ref)) > 0)
  )
);

-- WorkItem에 붙는 사용자 메모. 메모는 실행 상태가 아닌 작업 노트이므로
-- state_events에는 기록하지 않으며, 삭제는 확인 후 행을 즉시 제거한다.
CREATE TABLE IF NOT EXISTS work_item_memos (
  id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
  work_item_id TEXT NOT NULL REFERENCES work_items(id),
  title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
  content TEXT NOT NULL CHECK (length(trim(content)) > 0),
  kind TEXT NOT NULL CHECK (kind IN (
    'general', 'decision', 'problem', 'idea', 'question', 'reference'
  )),
  author TEXT NOT NULL DEFAULT 'anon' CHECK (length(trim(author)) > 0),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
  is_pinned INTEGER NOT NULL DEFAULT 0 CHECK (is_pinned IN (0, 1)),
  sort_order INTEGER NOT NULL CHECK (sort_order >= 0),
  created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
  updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0),
  UNIQUE (work_item_id, sort_order)
);

CREATE TRIGGER IF NOT EXISTS work_items_validate_status_transition
BEFORE UPDATE OF status ON work_items
WHEN OLD.status <> NEW.status
  AND NOT (
    (OLD.status = 'backlog' AND NEW.status IN ('ready', 'cancelled'))
    OR (OLD.status = 'ready' AND NEW.status IN ('in_progress', 'cancelled'))
    OR (OLD.status = 'in_progress'
      AND NEW.status IN ('ready', 'blocked', 'done', 'cancelled'))
    OR (OLD.status = 'blocked' AND NEW.status IN ('ready', 'cancelled'))
  )
BEGIN
  SELECT RAISE(ABORT, 'invalid work item status transition');
END;

CREATE TRIGGER IF NOT EXISTS work_items_require_finished_run_before_status_change
BEFORE UPDATE OF status ON work_items
WHEN OLD.status <> NEW.status
  AND NEW.status IN ('ready', 'blocked', 'done', 'cancelled')
  AND EXISTS (
    SELECT 1
    FROM runs
    WHERE runs.work_item_id = OLD.id AND runs.status = 'running'
  )
BEGIN
  SELECT RAISE(ABORT, 'running run must finish first');
END;

CREATE TRIGGER IF NOT EXISTS draft_work_items_must_stay_in_backlog
BEFORE UPDATE OF status, is_draft ON work_items
WHEN NEW.is_draft = 1 AND NEW.status <> 'backlog'
BEGIN
  SELECT RAISE(ABORT, 'Draft WorkItem must be refined before it becomes executable');
END;

CREATE TRIGGER IF NOT EXISTS draft_work_items_cannot_have_criteria
BEFORE INSERT ON acceptance_criteria
WHEN EXISTS (
  SELECT 1 FROM work_items
  WHERE id = NEW.work_item_id AND is_draft = 1
)
BEGIN
  SELECT RAISE(ABORT, 'Draft WorkItem cannot have Acceptance Criteria');
END;

CREATE TRIGGER IF NOT EXISTS acceptance_criteria_require_valid_evidence_on_pass
BEFORE UPDATE OF status ON acceptance_criteria
WHEN NEW.status = 'passed' AND OLD.status <> 'passed'
  AND NOT EXISTS (
    SELECT 1
    FROM criterion_evidence AS evidence
    JOIN artifacts AS artifact ON artifact.id = evidence.artifact_id
    JOIN runs AS evidence_run ON evidence_run.id = artifact.run_id
    WHERE evidence.criterion_id = NEW.id
      AND evidence_run.work_item_id = NEW.work_item_id
      AND artifact.verification_status IN ('passed', 'not_applicable')
  )
BEGIN
  SELECT RAISE(ABORT, 'valid evidence required');
END;

CREATE TRIGGER IF NOT EXISTS work_items_require_completed_criteria_on_done
BEFORE UPDATE OF status ON work_items
WHEN NEW.status = 'done' AND OLD.status <> 'done'
  AND (
    NOT EXISTS (
      SELECT 1 FROM acceptance_criteria
      WHERE work_item_id = NEW.id
    )
    OR EXISTS (
      SELECT 1 FROM acceptance_criteria
      WHERE work_item_id = NEW.id AND status NOT IN ('passed', 'waived')
    )
    OR EXISTS (
      SELECT 1
      FROM acceptance_criteria AS criterion
      WHERE criterion.work_item_id = NEW.id
        AND criterion.status = 'passed'
        AND NOT EXISTS (
          SELECT 1
          FROM criterion_evidence AS evidence
          JOIN artifacts AS artifact ON artifact.id = evidence.artifact_id
          JOIN runs AS evidence_run ON evidence_run.id = artifact.run_id
          WHERE evidence.criterion_id = criterion.id
            AND evidence_run.work_item_id = NEW.id
            AND artifact.verification_status IN ('passed', 'not_applicable')
        )
    )
  )
BEGIN
  SELECT RAISE(ABORT, 'work item completion requirements not met');
END;

CREATE TRIGGER IF NOT EXISTS criterion_evidence_validate_work_item_on_insert
BEFORE INSERT ON criterion_evidence
WHEN NOT EXISTS (
  SELECT 1
  FROM acceptance_criteria AS criterion
  JOIN artifacts AS artifact ON artifact.id = NEW.artifact_id
  JOIN runs AS evidence_run ON evidence_run.id = artifact.run_id
  WHERE criterion.id = NEW.criterion_id
    AND criterion.work_item_id = evidence_run.work_item_id
)
BEGIN
  SELECT RAISE(ABORT, 'evidence work item mismatch');
END;

CREATE TRIGGER IF NOT EXISTS criterion_evidence_validate_work_item_on_update
BEFORE UPDATE OF criterion_id, artifact_id ON criterion_evidence
WHEN NOT EXISTS (
  SELECT 1
  FROM acceptance_criteria AS criterion
  JOIN artifacts AS artifact ON artifact.id = NEW.artifact_id
  JOIN runs AS evidence_run ON evidence_run.id = artifact.run_id
  WHERE criterion.id = NEW.criterion_id
    AND criterion.work_item_id = evidence_run.work_item_id
)
BEGIN
  SELECT RAISE(ABORT, 'evidence work item mismatch');
END;

CREATE TRIGGER IF NOT EXISTS artifacts_prevent_final_verification_rewrite
BEFORE UPDATE OF verification_status ON artifacts
WHEN OLD.verification_status <> NEW.verification_status
  AND OLD.verification_status <> 'pending'
BEGIN
  SELECT RAISE(ABORT, 'artifact verification is final');
END;

CREATE TRIGGER IF NOT EXISTS artifacts_validate_pending_transition
BEFORE UPDATE OF verification_status ON artifacts
WHEN OLD.verification_status = 'pending'
  AND NEW.verification_status NOT IN ('passed', 'failed')
BEGIN
  SELECT RAISE(ABORT, 'pending artifact must resolve to passed or failed');
END;

CREATE TRIGGER IF NOT EXISTS acceptance_criteria_lock_description_after_evidence
BEFORE UPDATE OF description ON acceptance_criteria
WHEN OLD.description <> NEW.description
  AND EXISTS (
    SELECT 1 FROM criterion_evidence
    WHERE criterion_id = OLD.id
  )
BEGIN
  SELECT RAISE(ABORT, 'criterion description is locked');
END;

CREATE TRIGGER IF NOT EXISTS state_events_prevent_update
BEFORE UPDATE ON state_events
BEGIN
  SELECT RAISE(ABORT, 'state events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS state_events_prevent_delete
BEFORE DELETE ON state_events
BEGIN
  SELECT RAISE(ABORT, 'state events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS memory_candidates_validate_keywords_on_insert
BEFORE INSERT ON memory_candidates
WHEN CASE
  WHEN json_valid(NEW.keywords_json) AND json_type(NEW.keywords_json) = 'array'
  THEN EXISTS (
    SELECT 1 FROM json_each(NEW.keywords_json)
    WHERE json_each.type <> 'text'
  )
  ELSE 0
END
BEGIN
  SELECT RAISE(ABORT, 'memory candidate keywords must be strings');
END;

CREATE TRIGGER IF NOT EXISTS memory_candidates_validate_keywords_on_update
BEFORE UPDATE OF keywords_json ON memory_candidates
WHEN CASE
  WHEN json_valid(NEW.keywords_json) AND json_type(NEW.keywords_json) = 'array'
  THEN EXISTS (
    SELECT 1 FROM json_each(NEW.keywords_json)
    WHERE json_each.type <> 'text'
  )
  ELSE 0
END
BEGIN
  SELECT RAISE(ABORT, 'memory candidate keywords must be strings');
END;

CREATE TRIGGER IF NOT EXISTS runs_prevent_finished_status_change
BEFORE UPDATE OF status ON runs
WHEN OLD.status <> 'running' AND OLD.status <> NEW.status
BEGIN
  SELECT RAISE(ABORT, 'finished run status is immutable');
END;

CREATE INDEX IF NOT EXISTS features_status_idx
  ON features(status);
CREATE INDEX IF NOT EXISTS work_items_status_priority_idx
  ON work_items(status, priority);
CREATE INDEX IF NOT EXISTS work_items_feature_status_idx
  ON work_items(feature_id, status);
CREATE INDEX IF NOT EXISTS acceptance_criteria_work_item_status_idx
  ON acceptance_criteria(work_item_id, status);
CREATE INDEX IF NOT EXISTS runs_work_item_started_at_idx
  ON runs(work_item_id, started_at);
CREATE UNIQUE INDEX IF NOT EXISTS runs_one_running_per_work_item_idx
  ON runs(work_item_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS artifacts_run_kind_idx
  ON artifacts(run_id, kind);
CREATE INDEX IF NOT EXISTS memory_candidates_run_status_created_at_idx
  ON memory_candidates(run_id, status, created_at);
CREATE INDEX IF NOT EXISTS state_events_entity_created_at_idx
  ON state_events(entity_type, entity_id, created_at);
CREATE INDEX IF NOT EXISTS work_item_memos_work_item_order_idx
  ON work_item_memos(work_item_id, is_pinned DESC, sort_order, id);
CREATE INDEX IF NOT EXISTS work_item_memos_work_item_filters_idx
  ON work_item_memos(work_item_id, status, kind);

CREATE VIEW IF NOT EXISTS feature_progress AS
SELECT
  feature.id AS feature_id,
  feature.title,
  feature.status,
  COUNT(work_item.id) AS total_work_items,
  SUM(CASE WHEN work_item.status = 'backlog' THEN 1 ELSE 0 END) AS backlog_count,
  SUM(CASE WHEN work_item.status = 'ready' THEN 1 ELSE 0 END) AS ready_count,
  SUM(CASE WHEN work_item.status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress_count,
  SUM(CASE WHEN work_item.status = 'blocked' THEN 1 ELSE 0 END) AS blocked_count,
  SUM(CASE WHEN work_item.status = 'done' THEN 1 ELSE 0 END) AS done_count,
  SUM(CASE WHEN work_item.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_count,
  COALESCE(
    ROUND(
      100.0 * SUM(CASE WHEN work_item.status = 'done' THEN 1 ELSE 0 END)
      / NULLIF(SUM(CASE WHEN work_item.status <> 'cancelled' THEN 1 ELSE 0 END), 0),
      1
    ),
    0.0
  ) AS completion_percent
FROM features AS feature
LEFT JOIN work_items AS work_item ON work_item.feature_id = feature.id
GROUP BY feature.id;

CREATE VIEW IF NOT EXISTS project_progress AS
SELECT
  COUNT(*) AS total_work_items,
  SUM(CASE WHEN status = 'backlog' THEN 1 ELSE 0 END) AS backlog_count,
  SUM(CASE WHEN status = 'ready' THEN 1 ELSE 0 END) AS ready_count,
  SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress_count,
  SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked_count,
  SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done_count,
  SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_count,
  COALESCE(
    ROUND(
      100.0 * SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END)
      / NULLIF(SUM(CASE WHEN status <> 'cancelled' THEN 1 ELSE 0 END), 0),
      1
    ),
    0.0
  ) AS completion_percent
FROM work_items;

CREATE VIEW IF NOT EXISTS work_item_verification AS
SELECT
  work_item.id AS work_item_id,
  COUNT(criterion.id) AS total_criteria,
  SUM(CASE WHEN criterion.status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
  SUM(CASE WHEN criterion.status = 'passed' THEN 1 ELSE 0 END) AS passed_count,
  SUM(CASE WHEN criterion.status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
  SUM(CASE WHEN criterion.status = 'waived' THEN 1 ELSE 0 END) AS waived_count,
  SUM(
    CASE
      WHEN criterion.status = 'passed' AND NOT EXISTS (
        SELECT 1
        FROM criterion_evidence AS evidence
        JOIN artifacts AS artifact ON artifact.id = evidence.artifact_id
        JOIN runs AS evidence_run ON evidence_run.id = artifact.run_id
        WHERE evidence.criterion_id = criterion.id
          AND evidence_run.work_item_id = work_item.id
          AND artifact.verification_status IN ('passed', 'not_applicable')
      ) THEN 1
      ELSE 0
    END
  ) AS missing_evidence_count
FROM work_items AS work_item
LEFT JOIN acceptance_criteria AS criterion ON criterion.work_item_id = work_item.id
GROUP BY work_item.id;

CREATE VIEW IF NOT EXISTS next_work_items AS
SELECT
  work_item.*,
  CASE work_item.priority
    WHEN 'urgent' THEN 1
    WHEN 'high' THEN 2
    WHEN 'normal' THEN 3
    WHEN 'low' THEN 4
  END AS priority_rank
FROM work_items AS work_item
WHERE work_item.status = 'ready'
ORDER BY priority_rank, work_item.created_at, work_item.id;

CREATE VIEW IF NOT EXISTS recent_activity AS
SELECT *
FROM state_events
ORDER BY created_at DESC, id DESC;

COMMIT;
