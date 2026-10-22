# ADR 0012: Ulysses, our own plugin for the issue page and issue list

## Contents

- [1. Status](#status)
- [2. Context](#context)
- [3. Decision](#decision)
- [4. Alternatives considered](#alternatives)
- [5. Consequences](#consequences)

<a id="status"></a>
## 1. Status

Proposed. Designed in [design 0004](../design/0004-issue-ux.md).

<a id="context"></a>
## 2. Context

Redmine is the tracker ([ADR 0011](0011-redmine-is-the-tracker.md)), and its issue page and issue
list are where an issue is read and changed. Changing one field means opening the whole edit form,
scrolling to the field, and saving every field at once. The issue page shows core fields in two
fixed columns and custom fields split in half after them, in no order a reader chose. Nothing on
the page can be collapsed.

Wanted: every field edited where it is shown, on the page and in the list; each change journaled
as an edit through the form is; fields in groups placed where a reader looks for them; groups that
collapse, with the state kept across reloads and tabs; and an administrator placing any custom
field in a group.

Three plugins were tried in a throwaway Redmine 7.0.2: UX Toolkit 4.5.2, View Customize 3.6.0 and
Issues Panel 1.2.2. About twenty more were read, old and unmaintained ones included
([design 0004, *Prior art*](../design/0004-issue-ux.md#prior-art)). None does all five. None groups
core fields, none keeps collapse state in step across tabs, and the one that edits fields on the
issue page, redmine_issue_dynamic_edit, sends no `lock_version` and drops a stale edit without a
message (`issue_dynamic_edit.js`, lines 425–427, in 0.9.3). Core has open feature requests for
field grouping (Feature #35281) and inline list editing (Feature #9184), neither scheduled.

Redmine 7.0.2 runs on Rails 8.1. It loads Stimulus through importmap, and its newer interface is
Stimulus controllers (`app/javascript/controllers/`), beside the jQuery of the older pages. It
doesn't load Turbo.

<a id="decision"></a>
## 3. Decision

We build our own plugin, Ulysses, and publish it. Its plugin id, folder and repo are
`redmine_ulysses` (`github.com/pjfitzgibbons/redmine_ulysses`), as Redmine expects a plugin's
folder to be named after its id. Its themes are pluggable ([ADR 0013](0013-pluggable-themes.md)).

- It is licensed GPL-2-or-later, as Redmine is. Code copied from another plugin keeps its
  licence and copyright notice. MIT and BSD-3 sources fit as they are; GPL-2-only code would
  narrow the plugin to GPL-2, and GPL-3 code would make the release GPL-3, so copying either is a
  decision of its own.
- No third-party plugin is installed with it. Ideas are taken and written again: dynamic_edit's
  use of core's own form controls, Issues Panel's refusal of a field that `safe_attributes`
  dropped (`app/models/issue_card.rb`, lines 2–7, in 1.2.2), and redmine_custom_fields_groups'
  group model.
- Every save goes through `Issue#safe_attributes=` and `save` with `init_journal`, the same path
  as the edit form, with the issue's `lock_version`. Core is never patched: the plugin uses hooks,
  its own views and controller, and `Module#prepend`.
- The browser side is Stimulus and Turbo. The server renders each editor and each answer as HTML:
  an editable field is a Turbo Frame, and a save answers with Turbo Streams. Stimulus is core's
  copy. The plugin pins `@hotwired/turbo` in the importmap with Turbo Drive off, so only the
  plugin's frames use it and core's pages navigate as before. Stimulus controllers do what HTML
  can't: save on leave, the tag combobox, collapse and the list grid's keyboard.
- Collapse state is kept per browser in `localStorage`. Field groups are set on the plugin's
  settings page, in `Setting.plugin_redmine_ulysses`.
- gh-plan's Redmine installs a tagged release: the Dockerfile fetches that tag from the public
  repo, so the build needs no credential. Working on the plugin, a compose override mounts the
  local checkout in its place. gh-plan carries no copy of the plugin's code.

<a id="alternatives"></a>
## 4. Alternatives considered

- **Fork redmine_issue_dynamic_edit** (MIT). It already edits most fields on the issue page by
  copying core's form controls. We would carry the fork, fix its stale-edit drop and its missing
  `lock_version`, and still write list editing, groups and collapse ourselves, which is most of
  the work.
- **Combine**: install dynamic_edit as it is for the issue page, and our own plugin for the rest.
  That is two plugins to upgrade, and dynamic_edit's silent loss of a stale edit stays.
- **View Customize with JavaScript of our own.** It injects stored JavaScript into pages. The code
  would live in the database, outside version control and tests, and every save would still need
  a server side that checks permissions.
- **UX Toolkit.** Its quick status and assign buttons are journaled, but it has no field editing,
  and its checklists live in a table of their own with no journal entry.
- **HTMX** for the saves. It suits HTML answers, but it would be a second front-end model in
  Redmine's pages, and the combobox, the grid and save on leave would still need Stimulus or
  plain JavaScript.
- **Vue 3 or Alpine.js.** Vue needs a build step or its template compiler in the browser, and its
  templates would duplicate the ERB views. Alpine overlaps with Stimulus, which core already
  loads.
- **Plain JavaScript and a JSON controller.** No dependency, but the requests, the status
  handling and the DOM updates are written by hand, and the markup is built twice, in ERB and in
  JavaScript.
- **Wait for core.** Feature #35281 is a candidate for the next major release, with no date, and
  covers grouping only.

<a id="consequences"></a>
## 5. Consequences

- The plugin has a repo, a release and its own tests, apart from gh-plan. A Redmine upgrade in
  gh-plan waits for a Ulysses release that passes on the new version, and `redmine/README.md`'s
  upgrade steps say so.
- Work on the plugin is tracked in a Redmine project of its own, `ulysses`, which
  `plan.checkouts` maps to the plugin's checkout so Start makes its branches there.
- Its tests need what the Redmine image leaves out: the test gems, a test database and a browser.
  A test image and a Chromium container are added to the plugin's repo for them ([design 0004,
  *Testing*](../design/0004-issue-ux.md#testing)).
- If core ships field grouping or inline editing, the plugin's part that overlaps is retired in
  favour of core's.
- Group layout lives in Redmine's `settings` table, which the nightly database backup copies. The
  JSON export and the REST API are unchanged.
- Turbo is a dependency the plugin carries and upgrades. Stimulus upgrades with Redmine, and a
  Redmine upgrade that changes it shows in the plugin's system tests.
- Collapse state does not follow Peter from one browser to another.
