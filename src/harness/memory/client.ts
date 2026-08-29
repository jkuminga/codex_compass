import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

import { SQLiteBackend } from "../../../vendor/memory-graph/ts/src/backends/sqlite.ts";
import { SearchQuerySchema } from "../../../vendor/memory-graph/ts/src/models.ts";

import type {
  InspectKeywordSearcher,
  InspectSourceMemory,
  KeywordSearcher,
  RecallSourceMemory,
} from "./types.ts";

export const MEMORY_DATABASE_RELATIVE_PATH = ".harness/memorygraph.db";

/** Find the enclosing Git repository without invoking a shell command. */
export function findProjectRoot(startDirectory = process.cwd()): string {
  let current = resolve(startDirectory);

  while (true) {
    if (existsSync(join(current, ".git"))) return current;
    const parent = dirname(current);
    if (parent === current) {
      throw new Error("project_root_not_found");
    }
    current = parent;
  }
}

/** Resolve the project-local MemoryGraph SQLite file. */
export function resolveMemoryDatabasePath(projectRoot: string): string {
  const override = process.env.HARNESS_MEMORY_DB_PATH?.trim();
  return override || join(projectRoot, MEMORY_DATABASE_RELATIVE_PATH);
}

function toIsoString(value: string | Date): string {
  return value instanceof Date ? value.toISOString() : value;
}

function compactSummary(summary: string | null | undefined, content: string): string {
  const value = summary?.trim() || content.trim();
  return value.length <= 500 ? value : `${value.slice(0, 497)}...`;
}

async function withoutInfoLogs<T>(operation: () => Promise<T>): Promise<T> {
  const originalLog = console.log;
  console.log = () => undefined;
  try {
    return await operation();
  } finally {
    console.log = originalLog;
  }
}

async function openMemoryBackend(databasePath: string): Promise<SQLiteBackend> {
  const backend = new SQLiteBackend(databasePath);
  try {
    await withoutInfoLogs(async () => {
      await backend.connect();
      await backend.initializeSchema();
    });
    return backend;
  } catch (error) {
    try {
      await withoutInfoLogs(() => backend.disconnect());
    } catch {
      // Keep the original connection or schema error.
    }
    throw error;
  }
}

/**
 * Open one MemoryGraph SQLite connection and expose only the keyword-search
 * function required by the wrapper. The returned close function must run.
 */
export async function openMemorySearchClient(databasePath: string): Promise<{
  searchKeyword: KeywordSearcher;
  close: () => Promise<void>;
}> {
  const backend = await openMemoryBackend(databasePath);

  const searchKeyword: KeywordSearcher = async (keyword, limit) => {
    const query = SearchQuerySchema.parse({
      query: keyword,
      limit,
      include_relationships: false,
    });
    const memories = await backend.searchMemories(query);

    return memories
      .filter((memory) => Boolean(memory.id))
      .map<RecallSourceMemory>((memory) => ({
        id: memory.id as string,
        title: memory.title,
        summary: compactSummary(memory.summary, memory.content),
        importance: memory.importance,
        updatedAt: toIsoString(memory.updated_at),
      }));
  };

  return {
    searchKeyword,
    close: () => withoutInfoLogs(() => backend.disconnect()),
  };
}

/** Open the richer read-only search boundary used to inspect one Candidate. */
export async function openMemoryInspectClient(databasePath: string): Promise<{
  searchKeyword: InspectKeywordSearcher;
  close: () => Promise<void>;
}> {
  const backend = await openMemoryBackend(databasePath);

  const searchKeyword: InspectKeywordSearcher = async (keyword, limit) => {
    const query = SearchQuerySchema.parse({
      query: keyword,
      limit,
      include_relationships: false,
    });
    const memories = await backend.searchMemories(query);

    return memories
      .filter((memory) => Boolean(memory.id))
      .map<InspectSourceMemory>((memory) => ({
        id: memory.id as string,
        type: memory.type,
        title: memory.title,
        content: memory.content,
        summary: compactSummary(memory.summary, memory.content),
        tags: memory.tags,
        importance: memory.importance,
        confidence: memory.confidence,
        updatedAt: toIsoString(memory.updated_at),
      }));
  };

  return {
    searchKeyword,
    close: () => withoutInfoLogs(() => backend.disconnect()),
  };
}
