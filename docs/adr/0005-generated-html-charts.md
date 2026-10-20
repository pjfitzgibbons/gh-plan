# ADR 0005: Charts are generated HTML, with no Mermaid and no JS charting library

<a id="status"></a>
## 1. Status

Superseded by [ADR 0011](0011-redmine-is-the-tracker.md): Redmine's own pages are the views
([architecture §9](../architecture.md#views)).

<a id="context"></a>
## 2. Context

The views include a lane board ([`D2`](../requirements.md)), a timeline with baseline bars
([`C7`](../requirements.md)), and tables for the ready queue and roll-ups. The timeline's value is
in showing dependencies.

Mermaid's `gantt` sequences bars with `after` but draws no arrows and has no critical path, so a
dependency changes a bar's position and the reader cannot see why. It also truncates labels at 44
characters and has no tooltips.

<a id="decision"></a>
## 3. Decision

Every chart, graph and table is generated HTML. CSS Grid lays out the lane board and the timeline
bars, inline SVG draws the dependency arrows over the grid, and tables are plain `<table>`
elements. Colour comes from CSS custom properties, with a shape or label alongside it so nothing
depends on hue alone. Templates emit the HTML and CSS does the layout, with no build step.

`plan render` writes the output to `.tracker/out/`, and `plan serve` serves the same templates.
The technique table is in [architecture §11](../architecture.md#views).

<a id="alternatives"></a>
## 4. Alternatives considered

- Mermaid. GitHub renders it for free in a committed `.md` file, but it draws no dependency arrows
  and has no critical path, which is what the timeline is for. It also truncates labels and has no
  tooltips.
- A JS chart library such as Chart.js, D3 or Frappe Gantt. It adds a dependency and a build step
  for layout that CSS Grid already does. D3 would be justified only if the dependency graph needed
  force-directed layout.
- A terminal UI (Textual). Much cheaper, but a board is a two-dimensional spatial view, and the
  chart has to be shareable with people who do not run the tool.
- A static site generator. It is read-only by construction, so the write UI planned for Phase 3
  could not be built on it. `plan render` keeps the useful part of the idea.

<a id="consequences"></a>
## 5. Consequences

- The timeline can show dependency arrows, labels are not truncated, and tooltips work. The lane
  board and the timeline are the same template with different column semantics.
- GitHub shows a committed HTML file as source, so the charts are not viewable on GitHub. They are
  build artifacts in `.tracker/out/`, gitignored, and not committed evidence.
- [`G1`](../requirements.md) is unaffected, because it concerns the tracking data, the item files,
  which stay committed and diffable ([ADR 0001](0001-git-is-the-database.md)). Only the rendering
  needs the tool to view.
- A chart needed by someone with no checkout is shared as a screenshot or a published page.
- HTML rendering arrives in Phase 2; Phase 1 answers *what next* in the terminal
  ([design 0001, *Phasing*](../design/0001-phase-1.md#phasing)).
