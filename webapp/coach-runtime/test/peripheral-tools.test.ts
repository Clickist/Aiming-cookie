import assert from "node:assert/strict";
import { test } from "node:test";
import { createPeripheralReferenceTool } from "../src/peripheral-tools.ts";

test("peripheral reference tool loads the reference document", async () => {
  const tool = createPeripheralReferenceTool();
  assert.equal(tool.name, "get_peripheral_reference");
  assert.equal(typeof tool.description, "string");
  assert.ok(tool.description.length > 0, "tool must have a description");

  const result = await tool.execute("test-call-id", {});
  assert.equal(result.content.length, 1);
  assert.equal(result.content[0].type, "text");

  const text = result.content[0].text;
  assert.ok(text.length > 100, "reference document should be substantial");
  assert.ok(text.includes("握姿"), "reference must contain grip type content");
  assert.ok(text.includes("EloShapes"), "reference must contain EloShapes field mapping");
  assert.ok(text.includes("63"), "reference must contain the 63g weight ceiling rule");
  assert.ok(text.includes("推荐以候选质量为先"), "reference must contain corrected JD mapping logic");
});

test("peripheral reference tool has no required parameters", () => {
  const tool = createPeripheralReferenceTool();
  // The tool takes no parameters — it always returns the full reference.
  assert.ok(tool.parameters, "tool must have parameter schema");
});
