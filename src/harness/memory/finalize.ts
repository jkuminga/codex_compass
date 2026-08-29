import {
  ALL_MEMORY_TYPES,
  ALL_RELATIONSHIP_TYPES,
} from "../../../vendor/memory-graph/ts/src/models.ts";

import type {
  FinalizeMemoryClient,
  FinalizeMemoryRequest,
  FinalizeMemoryResponse,
  FinalizeRelationshipPlan,
  FinalizeRelationshipResult,
  FinalizeStoredMemory,
} from "./types.ts";

function emptyRelationships() {
  return { created: [], skipped: [], failed: [] } as FinalizeMemoryResponse["relationships"];
}

function failure(
  request: FinalizeMemoryRequest,
  status: "validation_error" | "conflict",
  code: string,
  message: string,
): FinalizeMemoryResponse {
  return {
    ok: false,
    status,
    candidate_id: request.candidate_id,
    decision: request.storage_plan.decision,
    relationships: emptyRelationships(),
    warnings: [],
    error: { code, message },
  };
}

function inUnitInterval(value: number | undefined): boolean {
  return value === undefined || (Number.isFinite(value) && value >= 0 && value <= 1);
}

function normalizedNode(request: FinalizeMemoryRequest, memoryId: string): FinalizeStoredMemory {
  const memory = request.storage_plan.memory!;
  return {
    id: memoryId,
    type: memory.type,
    title: memory.title.trim(),
    content: memory.content.trim(),
    summary: memory.summary?.trim() || null,
    tags: [...new Set((memory.tags ?? []).map((tag) => tag.trim().toLowerCase()).filter(Boolean))].sort(),
    importance: memory.importance ?? 0.5,
    confidence: memory.confidence ?? 0.8,
    context: {
      additional_metadata: {
        harness_candidate_id: request.candidate_id,
        harness_plan_fingerprint: request.plan_fingerprint,
      },
    },
  };
}

function sameNode(existing: FinalizeStoredMemory, desired: FinalizeStoredMemory): boolean {
  return existing.type === desired.type
    && existing.title.trim() === desired.title
    && existing.content.trim() === desired.content
    && (existing.summary?.trim() || null) === (desired.summary || null)
    && JSON.stringify([...(existing.tags ?? [])].sort()) === JSON.stringify(desired.tags)
    && existing.importance === desired.importance
    && existing.confidence === desired.confidence;
}

function endpoints(memoryId: string, relation: FinalizeRelationshipPlan): FinalizeRelationshipResult {
  return relation.direction === "incoming"
    ? { from_memory_id: relation.target_memory_id, to_memory_id: memoryId, type: relation.type }
    : { from_memory_id: memoryId, to_memory_id: relation.target_memory_id, type: relation.type };
}

/** Validate the entire plan before any MemoryGraph mutation. */
export async function validateFinalizeRequest(
  request: FinalizeMemoryRequest,
  client: FinalizeMemoryClient,
): Promise<FinalizeMemoryResponse> {
  const plan = request.storage_plan;
  if (!request.candidate_id.trim() || !/^[0-9a-f]{64}$/.test(request.plan_fingerprint)) {
    return failure(request, "validation_error", "invalid_identity", "candidate_id and SHA-256 fingerprint are required");
  }
  if (!(["create", "merge", "reject"] as string[]).includes(plan.decision)) {
    return failure(request, "validation_error", "invalid_decision", "decision must be create, merge, or reject");
  }
  if (!(request.mode === "validate" || request.mode === "execute") || !Array.isArray(plan.relationships)) {
    return failure(request, "validation_error", "invalid_plan_shape", "mode and relationships are invalid");
  }
  if (plan.decision === "reject") {
    if (plan.memory || plan.relationships.length > 0 || plan.target_memory_id) {
      return failure(request, "validation_error", "invalid_reject_plan", "reject cannot write a node or relationship");
    }
  } else {
    const memory = plan.memory;
    if (!memory || typeof memory.title !== "string" || typeof memory.content !== "string"
      || !memory.title.trim() || !memory.content.trim() || !ALL_MEMORY_TYPES.includes(memory.type)
      || (memory.tags !== undefined && !Array.isArray(memory.tags))) {
      return failure(request, "validation_error", "invalid_memory", "a valid memory type, title, and content are required");
    }
    if (!inUnitInterval(memory.importance) || !inUnitInterval(memory.confidence)) {
      return failure(request, "validation_error", "invalid_memory_score", "memory scores must be between 0 and 1");
    }
  }
  if (plan.decision === "merge") {
    if (!plan.target_memory_id?.trim() || !(await client.getMemory(plan.target_memory_id))) {
      return failure(request, "validation_error", "merge_target_not_found", "merge target memory does not exist");
    }
  }
  const memoryId = plan.decision === "merge" ? plan.target_memory_id! : `candidate:${request.candidate_id}`;
  const relationKeys = new Set<string>();
  for (const relation of plan.relationships) {
    if (!(relation.direction === "outgoing" || relation.direction === "incoming")
      || !relation.target_memory_id?.trim()
      || !ALL_RELATIONSHIP_TYPES.includes(relation.type)
      || !inUnitInterval(relation.strength)
      || !inUnitInterval(relation.confidence)) {
      return failure(request, "validation_error", "invalid_relationship", "relationship fields are invalid");
    }
    const edge = endpoints(memoryId, relation);
    if (edge.from_memory_id === edge.to_memory_id) {
      return failure(request, "validation_error", "self_relationship", "self relationships are not allowed");
    }
    const key = `${edge.from_memory_id}\u0000${edge.to_memory_id}\u0000${edge.type}`;
    if (relationKeys.has(key)) {
      return failure(request, "validation_error", "duplicate_relationship", "the plan contains a duplicate relationship");
    }
    relationKeys.add(key);
    if (!(await client.getMemory(relation.target_memory_id))) {
      return failure(request, "validation_error", "relationship_target_not_found", "relationship target memory does not exist");
    }
  }
  return {
    ok: true, status: "validated", candidate_id: request.candidate_id,
    decision: plan.decision, memory_id: plan.decision === "reject" ? null : memoryId,
    relationships: emptyRelationships(), warnings: [],
  };
}

/** Execute one already validated Storage Plan idempotently. */
export async function finalizeMemoryCandidate(
  request: FinalizeMemoryRequest,
  client: FinalizeMemoryClient,
): Promise<FinalizeMemoryResponse> {
  const validation = await validateFinalizeRequest(request, client);
  if (!validation.ok || request.mode === "validate") return validation;
  const plan = request.storage_plan;
  if (plan.decision === "reject") {
    return { ...validation, status: "committed", node_result: "rejected", memory_ref: null };
  }
  const memoryId = validation.memory_id!;
  const desired = normalizedNode(request, memoryId);
  const existing = await client.getMemory(memoryId);
  let nodeResult: "created" | "updated" | "skipped";
  if (plan.decision === "create") {
    if (existing && !sameNode(existing, desired)) {
      return failure(request, "conflict", "memory_id_conflict", "deterministic memory id already has different content");
    }
    if (existing) nodeResult = "skipped";
    else { await client.createMemory(desired); nodeResult = "created"; }
  } else {
    if (existing && sameNode(existing, desired)) nodeResult = "skipped";
    else {
      const metadata = existing?.context?.additional_metadata as Record<string, unknown> | undefined;
      if (metadata?.harness_candidate_id === request.candidate_id
        && metadata?.harness_plan_fingerprint === request.plan_fingerprint) {
        return failure(request, "conflict", "merge_target_changed", "merge target changed after this plan wrote it");
      }
      await client.updateMemory(desired);
      nodeResult = "updated";
    }
  }

  const results = emptyRelationships();
  for (const relation of plan.relationships) {
    const edge = endpoints(memoryId, relation);
    try {
      if (await client.relationshipExists(edge.from_memory_id, edge.to_memory_id, edge.type)) {
        results.skipped.push(edge);
      } else {
        await client.createRelationship(edge.from_memory_id, edge.to_memory_id, edge.type, {
          strength: relation.strength ?? 0.5,
          confidence: relation.confidence ?? 0.8,
          context: relation.context,
        });
        results.created.push(edge);
      }
    } catch {
      results.failed.push(edge);
    }
  }
  const partial = results.failed.length > 0;
  return {
    ok: !partial, status: partial ? "partial" : "committed",
    candidate_id: request.candidate_id, decision: plan.decision,
    memory_id: memoryId, memory_ref: `memory://${memoryId}`, node_result: nodeResult,
    relationships: results, warnings: [],
    ...(partial ? { error: { code: "relationship_write_failed", message: "one or more relationships were not stored" } } : {}),
  };
}
