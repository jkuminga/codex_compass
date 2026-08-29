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
  created_at: string;
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
