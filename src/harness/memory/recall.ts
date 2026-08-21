import type {
  KeywordSearcher,
  RecallRequest,
  RecallResponse,
  RecallSourceMemory,
} from "./types.ts";

export const DEFAULT_RECALL_LIMIT = 5;
export const MAX_RECALL_LIMIT = 10;

interface RankedMemory extends RecallSourceMemory {
  matchedKeywords: string[];
}

/**
 * Normalize user-facing keywords for deterministic search and de-duplication.
 */
export function normalizeKeywords(keywords: string[]): string[] {
  const normalized: string[] = [];
  const seen = new Set<string>();

  for (const rawKeyword of keywords) {
    const keyword = rawKeyword.trim().toLocaleLowerCase("ko-KR");
    if (!keyword || seen.has(keyword)) continue;
    seen.add(keyword);
    normalized.push(keyword);
  }

  return normalized;
}

/** Clamp the requested result count to the small context-packet range. */
export function normalizeLimit(limit?: number): number {
  if (limit === undefined || !Number.isFinite(limit)) return DEFAULT_RECALL_LIMIT;
  return Math.min(MAX_RECALL_LIMIT, Math.max(1, Math.trunc(limit)));
}

function timestampValue(value: string): number {
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function compareRankedMemories(left: RankedMemory, right: RankedMemory): number {
  return (
    right.matchedKeywords.length - left.matchedKeywords.length ||
    right.importance - left.importance ||
    timestampValue(right.updatedAt) - timestampValue(left.updatedAt) ||
    left.id.localeCompare(right.id)
  );
}

/**
 * Search once per keyword, merge duplicate Memory IDs, and return a compact
 * deterministic context packet. A failed keyword never discards successful
 * results from the other keywords.
 */
export async function recallMemories(
  request: RecallRequest,
  searchKeyword: KeywordSearcher,
): Promise<RecallResponse> {
  const keywords = normalizeKeywords(request.keywords);
  const limit = normalizeLimit(request.limit);
  if (keywords.length === 0) {
    return { ok: true, memories: [], warnings: [] };
  }

  const merged = new Map<string, RankedMemory>();
  const warnings: string[] = [];
  let successfulSearches = 0;

  for (const keyword of keywords) {
    try {
      const memories = await searchKeyword(keyword, limit);
      successfulSearches += 1;

      for (const memory of memories) {
        const existing = merged.get(memory.id);
        if (existing) {
          if (!existing.matchedKeywords.includes(keyword)) {
            existing.matchedKeywords.push(keyword);
          }
          continue;
        }
        merged.set(memory.id, { ...memory, matchedKeywords: [keyword] });
      }
    } catch {
      warnings.push(`keyword_search_failed:${keyword}`);
    }
  }

  const memories = [...merged.values()]
    .sort(compareRankedMemories)
    .slice(0, limit)
    .map((memory) => ({
      id: memory.id,
      title: memory.title,
      summary: memory.summary,
      matched_keywords: memory.matchedKeywords,
    }));

  return {
    ok: successfulSearches > 0,
    memories,
    warnings:
      successfulSearches > 0 ? warnings : ["memory_database_unavailable"],
  };
}
