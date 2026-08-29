/** A small MemoryGraph record used internally for recall ranking. */
export interface RecallSourceMemory {
  id: string;
  title: string;
  summary: string;
  importance: number;
  updatedAt: string;
}

/** A compact memory injected into the Codex context. */
export interface RecalledMemory {
  id: string;
  title: string;
  summary: string;
  matched_keywords: string[];
}

/** Input accepted by the wrapper's core recall function. */
export interface RecallRequest {
  keywords: string[];
  limit?: number;
}

/** Stable JSON contract returned by the recall wrapper. */
export interface RecallResponse {
  ok: boolean;
  memories: RecalledMemory[];
  warnings: string[];
}

/** Search function boundary between recall policy and MemoryGraph. */
export type KeywordSearcher = (
  keyword: string,
  limit: number,
) => Promise<RecallSourceMemory[]>;

/** Pending State DB record inspected before long-term-memory finalization. */
export interface MemoryCandidateSnapshot {
  id: string;
  run_id: string;
  proposed_type: string;
  title: string;
  content: string;
  keywords: string[];
  status: string;
  memory_ref?: string | null;
  storage_plan?: FinalizeStoragePlan | null;
  plan_fingerprint?: string | null;
  created_at: string;
}

export type FinalizeDecision = "create" | "merge" | "reject";
export type RelationshipDirection = "outgoing" | "incoming";

/** One MemoryGraph node proposed by the Memory Finalize Skill. */
export interface FinalizeMemoryNode {
  type: string;
  title: string;
  content: string;
  summary?: string | null;
  tags?: string[];
  importance?: number;
  confidence?: number;
}

/** One directed edge proposed between the finalized node and an existing node. */
export interface FinalizeRelationshipPlan {
  direction: RelationshipDirection;
  target_memory_id: string;
  type: string;
  strength?: number;
  confidence?: number;
  context?: string | null;
}

/** Canonical write plan persisted in the State DB before graph mutation. */
export interface FinalizeStoragePlan {
  decision: FinalizeDecision;
  target_memory_id?: string | null;
  memory?: FinalizeMemoryNode | null;
  relationships: FinalizeRelationshipPlan[];
  reason?: string | null;
}

export interface FinalizeMemoryRequest {
  candidate_id: string;
  plan_fingerprint: string;
  storage_plan: FinalizeStoragePlan;
  mode: "validate" | "execute";
}

export interface FinalizeRelationshipResult {
  from_memory_id: string;
  to_memory_id: string;
  type: string;
}

/** Stable receipt returned by the MemoryGraph writer. */
export interface FinalizeMemoryResponse {
  ok: boolean;
  status: "validated" | "committed" | "partial" | "validation_error" | "conflict";
  candidate_id: string;
  decision: FinalizeDecision;
  memory_id?: string | null;
  memory_ref?: string | null;
  node_result?: "created" | "updated" | "skipped" | "rejected";
  relationships: {
    created: FinalizeRelationshipResult[];
    skipped: FinalizeRelationshipResult[];
    failed: FinalizeRelationshipResult[];
  };
  warnings: string[];
  error?: { code: string; message: string };
}

export interface FinalizeStoredMemory extends FinalizeMemoryNode {
  id: string;
  context?: Record<string, unknown> | null;
}

/** Testable storage seam used by the finalization policy. */
export interface FinalizeMemoryClient {
  getMemory(id: string): Promise<FinalizeStoredMemory | null>;
  createMemory(memory: FinalizeStoredMemory): Promise<void>;
  updateMemory(memory: FinalizeStoredMemory): Promise<void>;
  relationshipExists(fromId: string, toId: string, type: string): Promise<boolean>;
  createRelationship(
    fromId: string,
    toId: string,
    type: string,
    properties: { strength: number; confidence: number; context?: string | null },
  ): Promise<void>;
}

/** Rich but bounded MemoryGraph record used while comparing one Candidate. */
export interface InspectSourceMemory {
  id: string;
  type: string;
  title: string;
  content: string;
  summary: string;
  tags: string[];
  importance: number;
  confidence: number;
  updatedAt: string;
}

/** A compact existing memory returned to the Memory Finalize Skill. */
export interface CandidateMemoryMatch {
  id: string;
  type: string;
  title: string;
  summary: string;
  content_preview: string;
  content_truncated: boolean;
  tags: string[];
  importance: number;
  confidence: number;
  updated_at: string;
  matched_keywords: string[];
}

/** Input accepted by the read-only Candidate inspection Module. */
export interface InspectCandidateRequest {
  candidate: MemoryCandidateSnapshot;
  extra_terms?: string[];
  limit?: number;
}

export type InspectSearchStatus =
  | "not_started"
  | "complete"
  | "partial"
  | "failed";

export interface InspectSearchSummary {
  terms: string[];
  status: InspectSearchStatus;
  attempted: number;
  succeeded: number;
}

export interface InspectCandidateError {
  code: "candidate_not_pending" | "invalid_candidate_keywords";
  message: string;
}

/** Stable JSON contract returned by inspect_memory_candidate. */
export interface InspectCandidateResponse {
  ok: boolean;
  candidate: MemoryCandidateSnapshot;
  search: InspectSearchSummary;
  matches: CandidateMemoryMatch[];
  warnings: string[];
  error?: InspectCandidateError;
}

/** Search seam between Candidate inspection policy and MemoryGraph. */
export type InspectKeywordSearcher = (
  keyword: string,
  limit: number,
) => Promise<InspectSourceMemory[]>;
