# ADR 0013: Ulysses themes are pluggable, and its own theme is the reference

## Contents

- [1. Status](#status)
- [2. Context](#context)
- [3. Decision](#decision)
- [4. Alternatives considered](#alternatives)
- [5. Consequences](#consequences)

<a id="status"></a>
## 1. Status

Proposed. Designed in [design 0004, *Themes*](../design/0004-issue-ux.md#theme).

<a id="context"></a>
## 2. Context

The issue page plugin is published as `redmine_ulysses`, in its own repo
([ADR 0012](0012-issue-ux-plugin.md)). Its editors, field groups and messages need colours and type
that match the page around them.

gh-plan's Redmine is to look like glasswing, the palette ms-security also uses. Glasswing is
gh-plan's look, not the plugin's: a site installing Ulysses shouldn't get it by default, and
gh-plan shouldn't need a fork of the plugin to use it.

Redmine runs with the Opale theme 1.7.2, which the Dockerfile downloads and `setup.rb` selects.
Opale ships as one minified stylesheet of 442 KB under the AGPL-3. It defines no variables, so it
can't be recoloured without editing that file.

Redmine 7's own stylesheet builds its colours from Open Color variables (`--oc-gray-*`,
`--oc-blue-*` and the rest), and takes its font from one variable, `--fonts-main`. A stylesheet
loaded after core's that redefines those variables recolours most of Redmine in a few dozen lines.

Redmine 7 compiles a theme in `themes/<name>/` through Propshaft when it starts, and serves
`public/` as it is. No Redmine plugin sets a convention for a site's own stylesheet: View
Customize and redmine_custom_css keep theirs in the database, and PurpleMine and Opale rebuild
from Sass.

Bootstrap 5, Material Web and Web Awesome were weighed as widget libraries for the plugin's
controls. What the controls do is a separate choice, made in
[ADR 0012](0012-issue-ux-plugin.md#decision): Stimulus and Turbo.

<a id="decision"></a>
## 3. Decision

Ulysses defines a theme contract and ships its own theme, the reference, which a site overrides
with one file.

- **The contract** is a set of CSS custom properties, `--uly-*`: colour roles (ink, ground,
  surfaces, rules, primary, accent, focus, and ok, warn, danger and info, each with an ink and a
  soft shade), the header gradient, fonts, type sizes, spacing, radius, shadow and motion. Each
  colour role is defined for light and for dark.
- **The reference theme** is the plugin's `tokens.css`, which gives every property a value. Its
  palette is drawn from *Papilio ulysses*, the blue swallowtail the plugin is named after. It and
  its fonts are chosen in the reference theme's work item, and shown in a design book in the
  plugin's repo.
- **`base.css`** maps core's `--oc-*` and `--fonts-main` onto the contract, draws the header, and
  styles the plugin's own components. It uses only the contract's properties and sets no value of
  its own.
- **The plugin loads all three** on every page, after core's stylesheet: `tokens.css`, `base.css`,
  then the site's theme file if there is one. Ulysses isn't a Redmine theme, and Redmine's own
  theme setting stays on Default.
- **A site's theme is one file** in a well-known place, `public/ulysses/theme.css` in the Redmine
  install, with its fonts beside it. It sets the properties it changes; any it leaves out keep the
  reference value. Redmine serves it as it is, with nothing to compile and no restart, and the
  plugin's link carries the file's modification time so a browser fetches a changed file.
- **A check for themes.** `bin/rake redmine_ulysses:theme:check[FILE]` fails when a theme file sets
  a property the contract doesn't have, or when, with the reference filling the gaps, a text colour
  and its ground fall under 4.5:1 in light or in dark.
- **Fonts** are under the SIL Open Font License and served from WOFF2 files beside the stylesheet
  that declares them, never from a font CDN.
- **No widget library**, that is, no CSS component framework. The plugin uses native `input`,
  `select`, `textarea`, `button` and `dialog`, styled by the contract. The one control HTML lacks,
  a multi-value tag combobox, is written once, to the WAI-ARIA Authoring Practices combobox
  pattern.
- **Status colours** (ok, warn and danger) are saturated enough that green, yellow and red read
  apart, whatever harmony the rest of a palette follows.
- The plugin and the reference theme are licensed GPL-2-or-later, as Redmine is.

gh-plan's Redmine runs `redmine_ulysses`, unforked, with glasswing as its site theme:
`redmine/ulysses/theme.css` and its fonts, which the Dockerfile copies into `public/ulysses/`, with
glasswing's design book beside them in the repo.

<a id="alternatives"></a>
## 4. Alternatives considered

- **Keep Opale and override it.** A second stylesheet would have to beat Opale's selectors one by
  one, with no variables to hook into, and would break on each Opale release.
- **Fork Opale.** Its source is published only as minified CSS, and a fork would carry the AGPL-3
  into the image.
- **A site theme as a Redmine theme**, a folder in `themes/` importing core's stylesheet, then
  `base.css`, then its tokens. Propshaft compiles it when Redmine starts, so a change needs a
  restart, and each theme repeats the imports in the right order. One file the plugin loads does
  the same job.
- **Theme CSS in the plugin's settings**, as redmine_custom_css keeps it. It lives in the database,
  outside version control and the theme check.
- **Glasswing as the plugin's default.** One palette fewer, but every site installing Ulysses would
  get gh-plan's look.
- **Fork the plugin for gh-plan.** Glasswing would sit in the fork, and every release would need
  merging.
- **Bootstrap 5.** Its global reset and its `.btn`, `.table`, `.badge` and `.tabs` classes collide
  with class names Redmine already uses. Scoping it under a plugin class is a fork to keep up to
  date.
- **Material Web.** Google's Material 3 components are in maintenance mode, and their elevation,
  ripples and large corners fight Redmine's page.
- **Web Awesome** (formerly Shoelace). Its shadow DOM avoids the CSS clash, and CSS variables theme
  it. It is still a dependency to vendor and upgrade alongside Redmine, for controls that are
  mostly inputs and selects.

<a id="consequences"></a>
## 5. Consequences

- The contract is the plugin's public interface for themes. Renaming or removing a property is a
  breaking change, called out in the plugin's release notes. A new property gets a value in the
  reference `tokens.css`, so an older site theme keeps working.
- The Dockerfile stops downloading Opale, and the image tag changes from `7.0.2-opale-1.7.2`.
- A Redmine theme other than Default, under Ulysses, fights `base.css`; the plugin's README says
  to leave the theme on Default.
- A Redmine upgrade can rename or drop an `--oc-*` variable. The upgrade steps in
  `redmine/README.md` gain a look at the issue page and the issue list.
- Dark mode follows the browser's `prefers-color-scheme`. Redmine has no per-user theme switch.
- Architecture §6.3's Theme row names Ulysses, with glasswing as gh-plan's site theme.
