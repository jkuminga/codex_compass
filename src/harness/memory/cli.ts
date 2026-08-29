#!/usr/bin/env bun

import {
  findProjectRoot,
  openMemoryInspectClient,
  openMemorySearchClient,
  resolveMemoryDatabasePath,
} from "./client.ts";
import { inspectMemoryCandidate } from "./inspect.ts";
import { normalizeLimit, recallMemories } from "./recall.ts";
import type {
  InspectCandidateRequest,
  InspectCandidateResponse,
  RecallResponse,
} from "./types.ts";

interface ParsedArguments {
  command: "recall";
  keywords: string[];
  limit: number;
}

interface InspectArguments {
  command: "inspect";
}

class UsageError extends Error {}

function parseArguments(args: string[]): ParsedArguments | InspectArguments {
  if (args[0] === "inspect" && args.length === 1) {
    return { command: "inspect" };
  }
  if (args[0] !== "recall") {
    throw new UsageError(
      'usage: harness-memory recall "keyword1 keyword2" [--limit N] | inspect',
    );
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

function writeResponse(response: RecallResponse | InspectCandidateResponse): void {
  process.stdout.write(`${JSON.stringify(response)}\n`);
}

async function runInspect(): Promise<number> {
  let request: InspectCandidateRequest;
  try {
    request = JSON.parse(await Bun.stdin.text()) as InspectCandidateRequest;
    if (!request || typeof request !== "object" || !request.candidate) {
      throw new Error("candidate is required");
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "invalid_json";
    process.stdout.write(
      `${JSON.stringify({ ok: false, error: { code: "invalid_input", message } })}\n`,
    );
    return 2;
  }

  let close: (() => Promise<void>) | undefined;
  try {
    const projectRoot = findProjectRoot();
    const client = await openMemoryInspectClient(
      resolveMemoryDatabasePath(projectRoot),
    );
    close = client.close;
    writeResponse(await inspectMemoryCandidate(request, client.searchKeyword));
  } catch {
    writeResponse(
      await inspectMemoryCandidate(request, async () => {
        throw new Error("memory_database_unavailable");
      }),
    );
  } finally {
    if (close) {
      try {
        await close();
      } catch {
        // Inspection is a read-only supporting operation.
      }
    }
  }
  return 0;
}

async function run(): Promise<number> {
  let parsed: ParsedArguments | InspectArguments;
  try {
    parsed = parseArguments(process.argv.slice(2));
  } catch (error) {
    const message = error instanceof Error ? error.message : "invalid_arguments";
    writeResponse({ ok: false, memories: [], warnings: [`invalid_arguments:${message}`] });
    return 2;
  }

  if (parsed.command === "inspect") return runInspect();

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
