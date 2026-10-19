// Editor layouts as vscode.getEditorLayout returns them. No vscode import, so it's tested under
// node --test.

// Whether editor column 2 sits directly below column 1, the leftmost group. Columns number the
// leaves depth first, so column 1 is the first leaf and column 2 the next; that is below column 1
// only when column 1's parent stacks its groups (orientation 1, top and bottom). Orientation
// alternates at each level down, starting from the layout's own.
function stackedBelowFirst(layout) {
  let parent = layout;
  let orientation = layout.orientation;
  while (parent.groups[0].groups?.length) {
    parent = parent.groups[0];
    orientation = 1 - orientation;
  }
  return orientation === 1 && parent.groups.length >= 2;
}

module.exports = { stackedBelowFirst };
