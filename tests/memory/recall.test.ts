import { describe, expect, test } from "bun:test";

import {
  normalizeKeywords,
  normalizeLimit,
  recallMemories,
} from "../../src/harness/memory/recall.ts";
import type { KeywordSearcher, RecallSourceMemory } from "../../src/harness/memory/types.ts";

function memory(
  id: string,
  importance: number,
  updatedAt: string,
): RecallSourceMemory {
  return {
    id,
    title: `Memory ${id}`,
    summary: `Summary ${id}`,
    importance,
    updatedAt,
  };
}

describe("keyword normalization", () => {
  test("removes blanks and case-insensitive duplicates", () => {
    expect(normalizeKeywords([" SQLite ", "", "sqlite", "외래키"])).toEqual([
      "sqlite",
      "외래키",
    ]);
  });

  test("clamps the context packet limit", () => {
    expect(normalizeLimit(undefined)).toBe(5);
    expect(normalizeLimit(0)).toBe(1);
    expect(normalizeLimit(99)).toBe(10);
  });
});

describe("recallMemories", () => {
  test("returns an empty success without searching empty input", async () => {
    let calls = 0;
    const searcher: KeywordSearcher = async () => {
      calls += 1;
      return [];
    };

    expect(await recallMemories({ keywords: ["  "] }, searcher)).toEqual({
      ok: true,
      memories: [],
      warnings: [],
    });
    expect(calls).toBe(0);
  });

  test("merges IDs and ranks match count before importance and recency", async () => {
    const results = new Map<string, RecallSourceMemory[]>([
      [
        "sqlite",
        [
          memory("shared", 0.4, "2026-01-01T00:00:00Z"),
          memory("important", 0.9, "2026-01-01T00:00:00Z"),
        ],
      ],
      [
        "외래키",
        [
          memory("shared", 0.4, "2026-01-01T00:00:00Z"),
          memory("recent", 0.9, "2026-08-01T00:00:00Z"),
        ],
      ],
    ]);

    const response = await recallMemories(
      { keywords: ["sqlite", "외래키"], limit: 3 },
      async (keyword) => results.get(keyword) ?? [],
    );

    expect(response.ok).toBe(true);
    expect(response.memories.map(({ id }) => id)).toEqual([
      "shared",
      "recent",
      "important",
    ]);
    expect(response.memories[0]?.matched_keywords).toEqual(["sqlite", "외래키"]);
  });

  test("keeps successful results when one keyword fails", async () => {
    const response = await recallMemories(
      { keywords: ["ok", "broken"] },
      async (keyword) => {
        if (keyword === "broken") throw new Error("database busy");
        return [memory("kept", 0.5, "2026-01-01T00:00:00Z")];
      },
    );

    expect(response.ok).toBe(true);
    expect(response.memories[0]?.id).toBe("kept");
    expect(response.warnings).toEqual(["keyword_search_failed:broken"]);
  });

  test("returns a database warning when every search fails", async () => {
    const response = await recallMemories(
      { keywords: ["one", "two"] },
      async () => {
        throw new Error("offline");
      },
    );

    expect(response).toEqual({
      ok: false,
      memories: [],
      warnings: ["memory_database_unavailable"],
    });
  });
});
