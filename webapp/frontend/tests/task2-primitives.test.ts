import assert from "node:assert/strict";
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
