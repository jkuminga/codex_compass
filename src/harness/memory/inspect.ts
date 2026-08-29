import { normalizeKeywords, normalizeLimit } from "./recall.ts";
import type {
  CandidateMemoryMatch,
  InspectCandidateRequest,
  InspectCandidateResponse,
  InspectKeywordSearcher,
  InspectSourceMemory,
} from "./types.ts";

export const MAX_INSPECT_TERMS = 5;
export const MAX_CONTENT_PREVIEW_LENGTH = 800;

interface RankedMatch extends InspectSourceMemory {
  matchedKeywords: string[];
}

function timestampValue(value: string): number {
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function compareMatches(left: RankedMatch, right: RankedMatch): number {
  return (
    right.matchedKeywords.length - left.matchedKeywords.length ||
    right.importance - left.importance ||
    timestampValue(right.updatedAt) - timestampValue(left.updatedAt) ||
    left.id.localeCompare(right.id)
  );
}

function contentPreview(content: string): {
  value: string;
  truncated: boolean;
} {
  const value = content.trim();
  if (value.length <= MAX_CONTENT_PREVIEW_LENGTH) {
    return { value, truncated: false };
  }
  return {
    value: `${value.slice(0, MAX_CONTENT_PREVIEW_LENGTH - 3)}...`,
    truncated: true,
  };
}

function toCandidateMatch(memory: RankedMatch): CandidateMemoryMatch {
  const preview = contentPreview(memory.content);
  return {
    id: memory.id,
    type: memory.type,
    title: memory.title,
    summary: memory.summary,
    content_preview: preview.value,
    content_truncated: preview.truncated,
    tags: memory.tags,
    importance: memory.importance,
    confidence: memory.confidence,
    updated_at: memory.updatedAt,
    matched_keywords: memory.matchedKeywords,
  };
}

/**
 * Inspect one pending Candidate against existing MemoryGraph records.
 *
 * The Candidate owns the primary search terms. Callers may add a few terms,
 * but never need to construct the start_work recall query again. This Module
 * only reads through the supplied search seam and returns a bounded result.
 */
export async function inspectMemoryCandidate(
  request: InspectCandidateRequest,
  searchKeyword: InspectKeywordSearcher,
): Promise<InspectCandidateResponse> {
  const candidate = request.candidate;
  if (candidate.status !== "pending") {
    return {
      ok: false,
      candidate,
      search: { terms: [], status: "not_started", attempted: 0, succeeded: 0 },
      matches: [],
      warnings: [],
      error: {
        code: "candidate_not_pending",
        message: "Only a pending Memory Candidate can be inspected for finalization.",
      },
    };
  }

  const allTerms = normalizeKeywords([
    ...candidate.keywords,
    ...(request.extra_terms ?? []),
  ]);
  const terms = allTerms.slice(0, MAX_INSPECT_TERMS);
  const warnings =
    allTerms.length > MAX_INSPECT_TERMS ? ["search_terms_truncated"] : [];
  if (terms.length === 0) {
    return {
      ok: false,
      candidate,
      search: { terms: [], status: "not_started", attempted: 0, succeeded: 0 },
      matches: [],
      warnings,
      error: {
        code: "invalid_candidate_keywords",
        message: "At least one Candidate keyword is required for inspection.",
      },
    };
  }

  const limit = normalizeLimit(request.limit);
  const merged = new Map<string, RankedMatch>();
  let succeeded = 0;

  for (const term of terms) {
    try {
      const memories = await searchKeyword(term, limit);
      succeeded += 1;
      for (const memory of memories) {
        const existing = merged.get(memory.id);
        if (existing) {
          if (!existing.matchedKeywords.includes(term)) {
            existing.matchedKeywords.push(term);
          }
          continue;
        }
        merged.set(memory.id, { ...memory, matchedKeywords: [term] });
      }
    } catch {
      warnings.push(`keyword_search_failed:${term}`);
    }
  }

  const status =
    succeeded === terms.length ? "complete" : succeeded > 0 ? "partial" : "failed";
  if (status === "failed") {
    warnings.splice(0, warnings.length, "memory_database_unavailable");
  }

  return {
    ok: status === "complete",
    candidate,
    search: {
      terms,
      status,
      attempted: terms.length,
      succeeded,
    },
    matches: [...merged.values()]
      .sort(compareMatches)
      .slice(0, limit)
      .map(toCandidateMatch),
    warnings,
  };
}
