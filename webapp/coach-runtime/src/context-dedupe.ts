import { createHash } from "node:crypto";

export type CoachContextDedupeDescriptor = {
  kind: string;
  analysis_ref: string;
  target_ref: string;
  start_ms: number | null;
  end_ms: number | null;
  comparison_analysis_ref: string | null;
};

function serializePythonFloat(value: number | null): string {
  if (value === null) return "null";
  if (!Number.isFinite(value)) {
    throw new RangeError("Coach context time bounds must be finite");
  }
  if (Object.is(value, -0)) return "-0.0";

  const absolute = Math.abs(value);
  let serialized = value.toString();
  if (absolute !== 0 && (absolute < 1e-4 || absolute >= 1e16)) {
    serialized = value.toExponential();
  } else if (Number.isInteger(value)) {
    return `${serialized}.0`;
  }
  return serialized.replace(/e([+-]?)(\d+)$/, (_match, sign: string, digits: string) => {
    const normalizedSign = sign || "+";
    return `e${normalizedSign}${digits.padStart(2, "0")}`;
  });
}

export function canonicalizeCoachContextDescriptor(
  descriptor: CoachContextDedupeDescriptor,
): string {
  return "{"
    + `"analysis_ref":${JSON.stringify(descriptor.analysis_ref)},`
    + `"comparison_analysis_ref":${JSON.stringify(descriptor.comparison_analysis_ref)},`
    + `"end_ms":${serializePythonFloat(descriptor.end_ms)},`
    + `"kind":${JSON.stringify(descriptor.kind)},`
    + `"start_ms":${serializePythonFloat(descriptor.start_ms)},`
    + `"target_ref":${JSON.stringify(descriptor.target_ref)}`
    + "}";
}

export function hashCoachContextDescriptor(
  descriptor: CoachContextDedupeDescriptor,
): string {
  return createHash("sha256")
    .update(canonicalizeCoachContextDescriptor(descriptor), "utf8")
    .digest("hex");
}
