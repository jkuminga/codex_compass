import { describe, expect, test } from "bun:test";

import { finalizeMemoryCandidate } from "../../src/harness/memory/finalize.ts";
import type {
  FinalizeMemoryClient,
  FinalizeMemoryRequest,
  FinalizeStoredMemory,
} from "../../src/harness/memory/types.ts";

class FakeClient implements FinalizeMemoryClient {
  memories = new Map<string, FinalizeStoredMemory>();
  relationships = new Set<string>();
  writes = 0;
  failRelationshipOnce = false;

  async getMemory(id: string) { return this.memories.get(id) ?? null; }
  async createMemory(memory: FinalizeStoredMemory) { this.writes += 1; this.memories.set(memory.id, memory); }
  async updateMemory(memory: FinalizeStoredMemory) { this.writes += 1; this.memories.set(memory.id, memory); }
  async relationshipExists(from: string, to: string, type: string) {
    return this.relationships.has(`${from}|${to}|${type}`);
  }
  async createRelationship(from: string, to: string, type: string) {
    if (this.failRelationshipOnce) { this.failRelationshipOnce = false; throw new Error("temporary"); }
    this.relationships.add(`${from}|${to}|${type}`);
  }
}

function request(overrides: Partial<FinalizeMemoryRequest> = {}): FinalizeMemoryRequest {
  return {
    candidate_id: "MEMC-1",
    plan_fingerprint: "a".repeat(64),
    mode: "execute",
    storage_plan: {
      decision: "create",
      memory: {
        type: "solution", title: "외래키 활성화", content: "연결마다 PRAGMA foreign_keys를 켠다.",
        summary: "SQLite 외래키 검사를 연결마다 활성화한다.", tags: ["SQLite"],
        importance: 0.8, confidence: 0.9,
      },
      relationships: [],
    },
    ...overrides,
  };
}

describe("Memory Candidate finalization", () => {
  test("create is deterministic and an identical retry skips the node", async () => {
    const client = new FakeClient();
    const first = await finalizeMemoryCandidate(request(), client);
    const retry = await finalizeMemoryCandidate(request(), client);
    expect(first.status).toBe("committed");
    expect(first.node_result).toBe("created");
    expect(retry.node_result).toBe("skipped");
    expect(client.writes).toBe(1);
  });

  test("invalid relationship target performs no writes", async () => {
    const client = new FakeClient();
    const result = await finalizeMemoryCandidate(request({
      storage_plan: {
        ...request().storage_plan,
        relationships: [{ direction: "outgoing", target_memory_id: "missing", type: "SOLVES" }],
      },
    }), client);
    expect(result.status).toBe("validation_error");
    expect(client.writes).toBe(0);
  });

  test("partial relationship write retries without duplicating the node", async () => {
    const client = new FakeClient();
    client.memories.set("problem-1", {
      id: "problem-1", type: "problem", title: "문제", content: "원인", tags: [], importance: 0.5, confidence: 0.8,
    });
    client.failRelationshipOnce = true;
    const planned = request({ storage_plan: {
      ...request().storage_plan,
      relationships: [{ direction: "outgoing", target_memory_id: "problem-1", type: "SOLVES" }],
    }});
    const first = await finalizeMemoryCandidate(planned, client);
    const retry = await finalizeMemoryCandidate(planned, client);
    const third = await finalizeMemoryCandidate(planned, client);
    expect(first.status).toBe("partial");
    expect(retry.status).toBe("committed");
    expect(third.relationships.skipped).toHaveLength(1);
    expect(client.writes).toBe(1);
  });

  test("merge updates an existing target and reject writes nothing", async () => {
    const client = new FakeClient();
    client.memories.set("existing", {
      id: "existing", type: "solution", title: "옛 제목", content: "옛 내용", tags: [], importance: 0.4, confidence: 0.5,
    });
    const merged = await finalizeMemoryCandidate(request({
      storage_plan: { ...request().storage_plan, decision: "merge", target_memory_id: "existing" },
    }), client);
    const rejected = await finalizeMemoryCandidate(request({
      storage_plan: { decision: "reject", relationships: [], reason: "일회성" },
    }), client);
    expect(merged.node_result).toBe("updated");
    expect(rejected.node_result).toBe("rejected");
    expect(client.writes).toBe(1);
  });
});
