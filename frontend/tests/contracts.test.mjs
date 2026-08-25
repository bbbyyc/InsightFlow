import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const api = readFileSync(new URL("../lib/api.ts", import.meta.url), "utf8");
const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");

test("SSE client consumes response stream and exposes terminal events", () => {
  assert.match(api, /getReader\(\)/);
  assert.match(api, /event: \"accepted\" \| \"plan\" \| \"search\" \| \"rewrite\" \| \"generate\" \| \"answer_delta\" \| \"complete\" \| \"failed\"/);
  assert.match(page, /streamAgent\(/);
  assert.match(page, /event\.event === \"answer_delta\"/);
  assert.match(page, /abortRef\.current\?\.abort\(\)/);
});

test("product UI uses backend task, evaluation, retrieval and citation contracts", () => {
  for (const token of ["task?.progress", "retryDocument", "listEvaluations", "暂无已验证结果", "retrieval_channels", "channel_ranks", "rrf_rank", "fused_rank", "citation_number"]) {
    assert.ok(page.includes(token), `missing ${token}`);
  }
});

test("UI does not label hidden reasoning or hardcode metric examples", () => {
  assert.doesNotMatch(page, /思维链|chain.of.thought/i);
  assert.doesNotMatch(page, /Recall@5\s*[=:]\s*0\./);
});
