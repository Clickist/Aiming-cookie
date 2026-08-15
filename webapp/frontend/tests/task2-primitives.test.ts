import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import {
  Badge,
  Button,
  Dialog,
  Drawer,
  Empty,
  ErrorState,
  Field,
  IconButton,
  Loading,
  Notice,
  Panel,
  Sheet,
  Status,
  Tabs,
  Toast,
} from "../ui/primitives";

test("Task 2 exports only the minimum shared primitive set", () => {
  for (const primitive of [
    Button,
    IconButton,
    Badge,
    Status,
    Notice,
    Field,
    Panel,
    Tabs,
    Drawer,
    Sheet,
    Toast,
    Loading,
    Empty,
    ErrorState,
    Dialog,
  ]) {
    assert.equal(typeof primitive, "function");
  }
});

test("dialog and toast preserve their public API while animating presence", () => {
  const source = readFileSync(resolve(import.meta.dirname, "../ui/primitives.tsx"), "utf8");

  assert.match(source, /export function Dialog\(\{ open, onClose, title, children, footer \}: DialogProps\)/);
  assert.match(source, /const presence = useAnimatedPresence\(open, 180\)/);
  assert.match(source, /className="ac-dialog-backdrop" data-state=\{presence\.state\}/);
  assert.match(source, /inert=\{!open \|\| undefined\}/);
  assert.match(source, /export function Toast[\s\S]+const presence = useAnimatedPresence\(open, 200\)[\s\S]+window\.setTimeout\(requestClose, 5_000\)[\s\S]+data-state=\{presence\.state\}/);
});
