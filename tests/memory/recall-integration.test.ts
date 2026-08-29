import { afterEach, describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { SQLiteBackend } from "../../vendor/memory-graph/ts/src/backends/sqlite.ts";
import { createMemory } from "../../vendor/memory-graph/ts/src/models.ts";
import {
  openMemoryInspectClient,
  openMemorySearchClient,
} from "../../src/harness/memory/client.ts";
import { inspectMemoryCandidate } from "../../src/harness/memory/inspect.ts";
import { recallMemories } from "../../src/harness/memory/recall.ts";

const temporaryDirectories: string[] = [];

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    rmSync(directory, { recursive: true, force: true });
  }
});

describe("MemoryGraph SQLite integration", () => {
  test("recalls and merges memories stored by the pinned backend", async () => {
    const directory = mkdtempSync(join(tmpdir(), "harness-memory-"));
    temporaryDirectories.push(directory);
    const databasePath = join(directory, "memorygraph.db");
    const backend = new SQLiteBackend(databasePath);
    const originalLog = console.log;
    console.log = () => undefined;

    try {
      await backend.connect();
      await backend.initializeSchema();
      await backend.storeMemory(
        createMemory({
          id: "sqlite-fk",
          type: "solution",
          title: "SQLite 외래키 활성화",
          content: "SQLite 연결 시 외래키 검사를 켠다.",
          summary: "open_database가 PRAGMA foreign_keys = ON을 실행한다.",
          importance: 0.8,
        }),
      );
      await backend.storeMemory(
        createMemory({
          id: "unrelated",
          type: "technology",
          title: "CSS 색상 토큰",
          content: "문서 색상 팔레트 설명",
          summary: "HTML 문서는 공통 색상 토큰을 사용한다.",
          importance: 1,
        }),
      );
    } finally {
      await backend.disconnect();
      console.log = originalLog;
    }

    const client = await openMemorySearchClient(databasePath);
    try {
      const response = await recallMemories(
        { keywords: ["sqlite", "외래키"], limit: 5 },
        client.searchKeyword,
      );

      expect(response.ok).toBe(true);
      expect(response.memories).toHaveLength(1);
      expect(response.memories[0]).toEqual({
        id: "sqlite-fk",
        title: "SQLite 외래키 활성화",
        summary: "open_database가 PRAGMA foreign_keys = ON을 실행한다.",
        matched_keywords: ["sqlite", "외래키"],
      });
    } finally {
      await client.close();
    }
  });

  test("the executable CLI emits one JSON response from the project database", async () => {
    const directory = mkdtempSync(join(tmpdir(), "harness-memory-cli-"));
    temporaryDirectories.push(directory);
    const databasePath = join(directory, "memorygraph.db");
    const backend = new SQLiteBackend(databasePath);
    const originalLog = console.log;
    console.log = () => undefined;

    try {
      await backend.connect();
      await backend.initializeSchema();
      await backend.storeMemory(
        createMemory({
          id: "wrapper-decision",
          type: "general",
          title: "Recall Wrapper 결정",
          content: "MemoryGraph SQLiteBackend를 직접 사용한다.",
          summary: "Recall Wrapper는 SQLiteBackend 검색 결과를 병합한다.",
          importance: 0.9,
        }),
      );
    } finally {
      await backend.disconnect();
      console.log = originalLog;
    }

    const projectRoot = resolve(import.meta.dir, "../..");
    const child = Bun.spawn(
      [join(projectRoot, "bin/harness-memory"), "recall", "wrapper sqlite", "--limit", "3"],
      {
        cwd: projectRoot,
        env: { ...process.env, HARNESS_MEMORY_DB_PATH: databasePath },
        stdout: "pipe",
        stderr: "pipe",
      },
    );
    const stdout = await new Response(child.stdout).text();
    const stderr = await new Response(child.stderr).text();
    const exitCode = await child.exited;

    expect(exitCode).toBe(0);
    expect(stderr).toBe("");
    expect(stdout.trim().split("\n")).toHaveLength(1);
    expect(JSON.parse(stdout)).toEqual({
      ok: true,
      memories: [
        {
          id: "wrapper-decision",
          title: "Recall Wrapper 결정",
          summary: "Recall Wrapper는 SQLiteBackend 검색 결과를 병합한다.",
          matched_keywords: ["wrapper", "sqlite"],
        },
      ],
      warnings: [],
    });
  });

  test("inspects a Candidate through the pinned MemoryGraph backend", async () => {
    const directory = mkdtempSync(join(tmpdir(), "harness-memory-inspect-"));
    temporaryDirectories.push(directory);
    const databasePath = join(directory, "memorygraph.db");
    const backend = new SQLiteBackend(databasePath);
    const originalLog = console.log;
    console.log = () => undefined;

    try {
      await backend.connect();
      await backend.initializeSchema();
      await backend.storeMemory(
        createMemory({
          id: "existing-fk",
          type: "solution",
          title: "SQLite 외래키 활성화",
          content: "연결 직후 PRAGMA foreign_keys를 활성화한다.",
          summary: "SQLite 연결 함수가 외래키 검사를 보장한다.",
          tags: ["sqlite", "외래키"],
          importance: 0.8,
        }),
      );
    } finally {
      await backend.disconnect();
      console.log = originalLog;
    }

    const client = await openMemoryInspectClient(databasePath);
    try {
      const response = await inspectMemoryCandidate(
        {
          candidate: {
            id: "MEMC-1",
            run_id: "RUN-1",
            proposed_type: "solution",
            title: "외래키 검사",
            content: "SQLite 외래키를 활성화한다.",
            keywords: ["sqlite", "외래키"],
            status: "pending",
            memory_ref: null,
            created_at: "2026-08-29T00:00:00Z",
          },
        },
        client.searchKeyword,
      );

      expect(response.ok).toBe(true);
      expect(response.matches[0]?.id).toBe("existing-fk");
      expect(response.matches[0]?.matched_keywords).toEqual(["sqlite", "외래키"]);
    } finally {
      await client.close();
    }
  });

  test("the CLI accepts an inspect packet on stdin", async () => {
    const directory = mkdtempSync(join(tmpdir(), "harness-memory-inspect-cli-"));
    temporaryDirectories.push(directory);
    const databasePath = join(directory, "memorygraph.db");
    const backend = new SQLiteBackend(databasePath);
    const originalLog = console.log;
    console.log = () => undefined;

    try {
      await backend.connect();
      await backend.initializeSchema();
      await backend.storeMemory(
        createMemory({
          id: "inspect-cli-memory",
          type: "technology",
          title: "MemoryGraph SQLite",
          content: "프로젝트 로컬 SQLiteBackend를 사용한다.",
          summary: "MemoryGraph는 프로젝트 로컬 SQLite에 저장한다.",
          importance: 0.7,
        }),
      );
    } finally {
      await backend.disconnect();
      console.log = originalLog;
    }

    const projectRoot = resolve(import.meta.dir, "../..");
    const child = Bun.spawn([join(projectRoot, "bin/harness-memory"), "inspect"], {
      cwd: projectRoot,
      env: { ...process.env, HARNESS_MEMORY_DB_PATH: databasePath },
      stdin: "pipe",
      stdout: "pipe",
      stderr: "pipe",
    });
    child.stdin.write(
      JSON.stringify({
        candidate: {
          id: "MEMC-cli",
          run_id: "RUN-cli",
          proposed_type: "technology",
          title: "로컬 기억 DB",
          content: "MemoryGraph 저장 위치를 확인한다.",
          keywords: ["memorygraph", "sqlite"],
          status: "pending",
          memory_ref: null,
          created_at: "2026-08-29T00:00:00Z",
        },
        limit: 5,
      }),
    );
    child.stdin.end();
    const stdout = await new Response(child.stdout).text();
    const stderr = await new Response(child.stderr).text();
    const exitCode = await child.exited;

    expect(exitCode).toBe(0);
    expect(stderr).toBe("");
    const response = JSON.parse(stdout);
    expect(response.ok).toBe(true);
    expect(response.matches[0]?.id).toBe("inspect-cli-memory");
  });
});
