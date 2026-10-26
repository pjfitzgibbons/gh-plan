// node --test vscode/test: the settings' defaults in package.json.
const test = require("node:test");
const assert = require("node:assert/strict");
const { contributes } = require("../package.json");

test("plan and gh-plan's default markers differ in more than case", () => {
  const markers = contributes.configuration.properties["plan.projectMarkers"].default;
  assert.equal(markers.plan, "Pl");
  assert.equal(markers["gh-plan"], "Gh");
  assert.notEqual(markers.plan.toLowerCase(), markers["gh-plan"].toLowerCase());
});
