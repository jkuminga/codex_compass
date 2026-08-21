#!/usr/bin/env bun

import { findProjectRoot, openMemorySearchClient, resolveMemoryDatabasePath } from "./client.ts";
import { normalizeLimit, recallMemories } from "./recall.ts";
import type { RecallResponse } from "./types.ts";

interface ParsedArguments {
  command: "recall";
  keywords: string[];
  limit: number;
}

class UsageError extends Error {}

function parseArguments(args: string[]): ParsedArguments {
  if (args[0] !== "recall") {
    throw new UsageError('usage: harness-memory recall "keyword1 keyword2" [--limit N]');
  }

  const query = args[1]?.trim() ?? "";
  let limit = normalizeLimit(undefined);

  for (let index = 2; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === "--limit") {
      const rawLimit = args[index + 1];
      if (!rawLimit || !/^\d+$/.test(rawLimit)) {
        throw new UsageError("--limit requires a positive integer");
      }
      limit = normalizeLimit(Number(rawLimit));
      index += 1;
      continue;
    }
    throw new UsageError(`unknown argument: ${argument}`);
  }

  return {
    command: "recall",
    keywords: query.split(/\s+/),
    limit,
  };
}

function writeResponse(response: RecallResponse): void {
  process.stdout.write(`${JSON.stringify(response)}\n`);
}

async function run(): Promise<number> {
  let parsed: ParsedArguments;
  try {
    parsed = parseArguments(process.argv.slice(2));
  } catch (error) {
    const message = error instanceof Error ? error.message : "invalid_arguments";
    writeResponse({ ok: false, memories: [], warnings: [`invalid_arguments:${message}`] });
    return 2;
  }

  if (parsed.keywords.every((keyword) => keyword.trim() === "")) {
    writeResponse({ ok: true, memories: [], warnings: [] });
    return 0;
  }

  let close: (() => Promise<void>) | undefined;
  try {
    const projectRoot = findProjectRoot();
    const client = await openMemorySearchClient(resolveMemoryDatabasePath(projectRoot));
    close = client.close;
    writeResponse(
      await recallMemories(
        { keywords: parsed.keywords, limit: parsed.limit },
        client.searchKeyword,
      ),
    );
  } catch {
    writeResponse({
      ok: false,
      memories: [],
      warnings: ["memory_database_unavailable"],
    });
  } finally {
    if (close) {
      try {
        await close();
      } catch {
        // Recall and its cleanup are non-blocking supporting operations.
      }
    }
  }

  // Recall is supporting context. Operational failure remains non-blocking.
  return 0;
}

process.exitCode = await run();
