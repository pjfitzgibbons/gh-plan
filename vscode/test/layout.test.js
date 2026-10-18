// Which layouts already have a group directly below the leftmost one.
const test = require("node:test");
const assert = require("node:assert/strict");
const { stackedBelowFirst } = require("../layout");

test("one group, or groups side by side, have nothing below the leftmost", () => {
  assert.equal(stackedBelowFirst({ orientation: 0, groups: [{}] }), false);
  assert.equal(stackedBelowFirst({ orientation: 0, groups: [{}, {}] }), false);
});

test("a left column split top and bottom has column 2 below column 1", () => {
  assert.equal(stackedBelowFirst({ orientation: 0, groups: [{ groups: [{}, {}] }, {}] }), true);
  assert.equal(stackedBelowFirst({ orientation: 1, groups: [{}, {}] }), true);
});

test("two groups side by side above a full-width one: column 2 is top right, not below", () => {
  assert.equal(stackedBelowFirst({ orientation: 1, groups: [{ groups: [{}, {}] }, {}] }), false);
});
