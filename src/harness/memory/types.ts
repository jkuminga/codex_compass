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
