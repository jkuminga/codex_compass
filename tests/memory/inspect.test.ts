import { describe, expect, test } from "bun:test";

import {
  MAX_CONTENT_PREVIEW_LENGTH,
  MAX_INSPECT_TERMS,
  inspectMemoryCandidate,
} from "../../src/harness/memory/inspect.ts";
import type {
  InspectSourceMemory,
  MemoryCandidateSnapshot,
} from "../../src/harness/memory/types.ts";

function candidate(
  overrides: Partial<MemoryCandidateSnapshot> = {},
): MemoryCandidateSnapshot {
  return {
    id: "MEMC-1",
    run_id: "RUN-1",
    proposed_type: "solution",
    title: "SQLite 외래키 검사",
    content: "모든 연결에서 외래키 검사를 활성화한다.",
    keywords: ["SQLite", "외래키"],
    status: "pending",
    memory_ref: null,
    created_at: "2026-08-29T00:00:00Z",
    ...overrides,
  };
}

function memory(
  id: string,
  importance: number,
  updatedAt: string,
  content = `Content ${id}`,
): InspectSourceMemory {
  return {
    id,
    type: "solution",
    title: `Memory ${id}`,
    content,
    summary: `Summary ${id}`,
    tags: ["sqlite"],
    importance,
    confidence: 0.8,
    updatedAt,
  };
}

describe("inspectMemoryCandidate", () => {
  test("uses Candidate keywords first and merges duplicate Memory IDs", async () => {
    const seen: string[] = [];
    const response = await inspectMemoryCandidate(
      {
        candidate: candidate(),
        extra_terms: ["PRAGMA", "sqlite"],
        limit: 3,
      },
      async (term) => {
        seen.push(term);
        if (term === "sqlite") {
          return [
            memory("shared", 0.4, "2026-01-01T00:00:00Z"),
            memory("important", 0.9, "2026-01-01T00:00:00Z"),
          ];
        }
        if (term === "외래키") {
          return [memory("shared", 0.4, "2026-01-01T00:00:00Z")];
        }
        return [memory("recent", 0.9, "2026-08-01T00:00:00Z")];
      },
    );

    expect(seen).toEqual(["sqlite", "외래키", "pragma"]);
    expect(response.ok).toBe(true);
    expect(response.matches.map(({ id }) => id)).toEqual([
      "shared",
      "recent",
      "important",
    ]);
    expect(response.matches[0]?.matched_keywords).toEqual(["sqlite", "외래키"]);
  });

  test("bounds search terms and result content", async () => {
    const response = await inspectMemoryCandidate(
      {
        candidate: candidate({ keywords: ["1", "2", "3", "4", "5", "6"] }),
      },
      async () => [memory("large", 0.5, "2026-01-01T00:00:00Z", "x".repeat(900))],
    );

    expect(response.search.terms).toHaveLength(MAX_INSPECT_TERMS);
    expect(response.warnings).toContain("search_terms_truncated");
    expect(response.matches[0]?.content_preview).toHaveLength(
      MAX_CONTENT_PREVIEW_LENGTH,
    );
    expect(response.matches[0]?.content_truncated).toBe(true);
  });

  test("rejects a non-pending Candidate without searching", async () => {
    let calls = 0;
    const response = await inspectMemoryCandidate(
      { candidate: candidate({ status: "promoted" }) },
      async () => {
        calls += 1;
        return [];
      },
    );

    expect(calls).toBe(0);
    expect(response.ok).toBe(false);
    expect(response.error?.code).toBe("candidate_not_pending");
    expect(response.search.status).toBe("not_started");
  });

  test("returns partial matches when one keyword search fails", async () => {
    const response = await inspectMemoryCandidate(
      { candidate: candidate() },
      async (term) => {
        if (term === "외래키") throw new Error("busy");
        return [memory("kept", 0.5, "2026-01-01T00:00:00Z")];
      },
    );

    expect(response.ok).toBe(false);
    expect(response.search.status).toBe("partial");
    expect(response.matches[0]?.id).toBe("kept");
    expect(response.warnings).toEqual(["keyword_search_failed:외래키"]);
  });

  test("reports MemoryGraph failure when every keyword search fails", async () => {
    const response = await inspectMemoryCandidate(
      { candidate: candidate() },
      async () => {
        throw new Error("offline");
      },
    );

    expect(response.ok).toBe(false);
    expect(response.search.status).toBe("failed");
    expect(response.matches).toEqual([]);
    expect(response.warnings).toEqual(["memory_database_unavailable"]);
  });
});
