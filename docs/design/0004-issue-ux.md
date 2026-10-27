# Design 0004: Ulysses, the issue page and issue list rebuilt

## Contents

- [1. Context and scope](#context)
- [2. Goals and non-goals](#goals)
- [3. Design](#design)
  - [3.1 Prior art](#prior-art)
  - [3.2 Saving one field](#save)
  - [3.3 Editors by field kind](#editors)
  - [3.4 Field groups](#groups)
  - [3.5 Collapse state](#collapse)
  - [3.6 Placing custom fields](#group-settings)
  - [3.7 The issue list](#list)
  - [3.8 Themes](#theme)
  - [3.9 Failure modes](#failure-modes)
- [4. Alternatives considered](#alternatives)
- [5. Cross-cutting concerns](#cross-cutting)
- [6. Testing](#testing)
- [7. Requirements coverage](#requirements-coverage)
- [8. Work breakdown](#work-breakdown)

<a id="context"></a>
## 1. Context and scope

Redmine is the tracker ([ADR 0011](../adr/0011-redmine-is-the-tracker.md)), and its issue page and
issue list are where an issue is read and changed. Changing one field today means opening the
whole edit form, scrolling to the field, and saving every field at once. The page shows core
fields in two fixed columns and the custom fields split in half after them, and nothing collapses.

This design is for Ulysses, `redmine_ulysses`, a Redmine plugin we write ourselves and publish
from its own repo ([ADR 0012](../adr/0012-issue-ux-plugin.md)). It also covers the plugin's
pluggable themes: the plugin's reference theme, and how gh-plan's Redmine swaps in its own theme,
glasswing, in place of Opale ([ADR 0013](../adr/0013-pluggable-themes.md)).

It runs on Redmine 7.0.2 with SQLite. gh-plan's Redmine, in the container `redmine/` builds, is
its first user.

<a id="goals"></a>
## 2. Goals and non-goals

Goals:

- **Edit in place.** Every field is edited where it's shown, on the issue page and in the list,
  with an editor that suits its kind. A multi-valued field is edited as tags, and a checkbox field
  saves with one click.
- **Every change is journaled.** Each edit is a journal entry with its author and time, exactly as
  an edit through the form would be.
- **Field groups.** Fields sit in groups, placed where a reader looks for them first, each placement
  with its reason.
- **Collapsible groups.** Every group collapses. The state survives a reload and is the same in
  every open tab.
- **Custom fields in groups.** An administrator places any custom field in any group, and a field
  nobody has placed yet has an obvious home.
- **One look, any palette.** The plugin and the page around it take their colours and type from
  one theme, and a site swaps the theme without changing the plugin.

Non-goals:

- Ticking the boxes in an issue's description. #89 turns the Done list into bullets, so the
  description has no checklist to tick.
- The plan board, Next up and the VS Code extension.
- Issues Panel's board and UX Toolkit's checklists.
- Any write to the database outside Redmine's own issue saving.
- Changing the REST API, the export or what `redmine-get` returns.

<a id="design"></a>
## 3. Design

<a id="prior-art"></a>
### 3.1 Prior art

Three plugins were installed and tried in a throwaway Redmine 7.0.2: UX Toolkit, View Customize and
Issues Panel. The others were read from source or from their listings. Each was judged against the
goals: in-place editing (edit), journaled saves (journal), collapsible groups that persist and stay
in step across tabs (collapse), and admin placement of custom fields (place).

Key: ● does it, ◐ partly, ○ no.

| Plugin | Edit | Journal | Collapse | Place | Licence | Redmine 7 | What we take |
|---|---|---|---|---|---|---|---|
| Redmine core 7.0.2 | ◐ list context menu | ● | ◐ `toggleFieldset`, not kept | ○ | GPL-2+ | yes | The base everything goes through |
| UX Toolkit 4.5.2 (tried) | ○ quick status and assign buttons | ● for its buttons | ○ | ○ | BSD-3 | yes | The idea of its fetch with the CSRF token, and its compatibility shims |
| View Customize 3.6.0 (tried) | ○ | n/a | ○ | ○ | GPL-2+ | yes | Nothing: it injects JavaScript stored in the database |
| Issues Panel 1.2.2 (tried) | ◐ drag a card to change status | ● | ○ | ○ | GPL-2 | yes | The idea of refusing a field that `safe_attributes=` dropped |
| redmine_issue_dynamic_edit 0.9.3 | ● issue page | ◐ no `lock_version` | ○ | ○ | MIT | 🔍 listed to 6.0 | The idea of reusing core's form controls as editors |
| redmine_rich_editor 0.18.3 | ◐ subject and description | ◐ merges journals | ○ | ○ | GPL-2 | 🔍 | Nothing: it overwrites a stale edit and rewrites history |
| redmine_custom_fields_groups 1.0.0 | ○ | n/a | ◐ fieldsets, not kept | ● issue custom fields | GPL-3 | 🔍 | The idea of its group model |
| redmine_custom_fields_sections 7.0.0 | ○ | n/a | ○ | ◐ project fields only | MIT | yes | Nothing: not issue fields |
| Redmineflux Inline Editor 6.0.0 | ● page and list | ◐ REST with the API key in the URL | ○ | ○ | none | 4.0–6.0 | Nothing: no licence, weak security |
| RedmineX Inline Edit Issue List | ● list | 🔍 | ◐ subtasks | ○ | commercial | 6.1 | A UX reference only |
| RedmineUP Checklists 4.1.0, Agile 1.7.0 | ○ | ◐ journal is paid | ○ | ○ | GPL-3+ Light, paid PRO | yes | Nothing: their own data model |
| additional_tags 4.6.0 | ◐ tags on the form | ● | ○ | ○ | GPL-2 | yes | A reference for journaling a virtual attribute |
| redmine_inline_edit_issues, edit_inline | ◐ grids | ○ no journal | ○ | ○ | none | no (3.x) | Nothing: unmaintained, unjournaled |
| redmine_tags, redmine_collapse, a_common_libs | ○ | — | ○ | ○ | various | no | Nothing: unmaintained |
| Easy Redmine, RedMica | 🔍 | 🔍 | 🔍 | 🔍 | proprietary, GPL-2 | own fork, tracks core | Nothing: no public code, or no UI of its own |

What the survey shows:

- No plugin does all five. None groups core fields, and none keeps collapse state in step across
  tabs.
- The one that edits fields on the issue page, redmine_issue_dynamic_edit, sends no `lock_version`
  and drops a stale edit without a message (`issue_dynamic_edit.js`, lines 425–427). rich_editor
  sends it, but on a conflict refreshes it and saves again, overwriting the other change.
- Core has open feature requests for field grouping (Feature #35281) and inline list editing
  (Feature #9184), neither scheduled.
- Ideas are written again, not copied, so the plugin owes no notice. If code is ever copied, MIT
  and BSD-3 sources fit the plugin's GPL-2-or-later as they are, with their notices kept. Copying
  GPL-2-only code (Issues Panel, additional_tags) would narrow the plugin to GPL-2, and GPL-3 code
  would make the release GPL-3. The unlicensed plugins can't be copied from at all.

<a id="save"></a>
### 3.2 Saving one field

Each edit saves one field of one issue through the plugin's own controller, `UlyssesFieldsController`.
The server renders every editor and every answer as HTML, and the browser side is Stimulus and
Turbo ([ADR 0012](../adr/0012-issue-ux-plugin.md#decision)).

| Route | Does |
|---|---|
| `GET /ulysses/issues/:id/fields/:field/edit` | Renders the field's editor, with its allowed values and the issue's `lock_version`, into the field's Turbo Frame. A field the user can't edit is a 403 |
| `PATCH /ulysses/issues/:id/fields/:field` | Saves `value` with `lock_version`, and answers with Turbo Streams: the field rendered at rest, the new `lock_version`, and the groups when they change |

`:field` is a core attribute name (`status_id`, `assigned_to_id`, `due_date` …) or `cf_<id>`.

**Why not core's endpoint.** Redmine's `.json` routes skip CSRF and ignore the session, so a
browser can't use them without the user's API key in the page. The HTML route `PATCH /issues/:id`
answers with a redirect, or a 200 page to scrape for errors. The plugin's routes have no `.json`
suffix, so they use the session and check the CSRF token, and each outcome has its own status.

**The checks, in order:**

1. The request carries `X-CSRF-Token` from the page's `csrf-token` meta tag. Rails refuses it
   without one.
2. `Issue.visible.find(params[:id])`: an issue the user can't see is a 404.
3. `authorize` against `issues#update`, as the edit form is.
4. The field must be one the user may change now: in `issue.safe_attribute_names(User.current)`
   for a core field, or in `issue.editable_custom_field_values(User.current)` for a custom field.
   These apply the role's permissions and the workflow's read-only rules for the issue's tracker
   and current status. Otherwise the answer is 403.
5. The value's shape must match the field: an array only for a multi-valued field. An array for
   `Lane` is a 400 (invariant 8).

**The save** is the edit form's path. A core field is set by its name, and a custom field through
`custom_field_values`, as the form sends it:

```ruby
issue.init_journal(User.current, notes)
attrs = { 'lock_version' => lock_version }
if custom_field
  attrs['custom_field_values'] = { custom_field.id.to_s => value }
else
  attrs[attribute] = value
end
issue.safe_attributes = attrs
issue.save
```

`notes` is empty except for a field that needs a note (§3.3). Then the controller compares what it
asked for with what the issue now holds, `issue.send(attribute)` or
`issue.custom_field_value(custom_field)`. Redmine's `safe_attributes=` deletes a key, or a custom
field value, that the user may not set without a word, and a save with nothing changed answers
success. If the requested value was dropped, the answer is 403, not a quiet 200.

**In the browser.** Redmine 7.0.2 runs on Rails 8.1 and loads Stimulus through importmap, with
core's own controllers in `app/javascript/controllers/`; it doesn't load Turbo. The plugin pins
`@hotwired/turbo` in the importmap and sets `Turbo.session.drive = false`, so core's links and
forms behave as they do now and Turbo acts only inside the plugin's frames.

- Each editable field is a `turbo-frame`. Its rest button is a link to the `edit` route, so
  opening the editor is a frame load, and the editor is a form whose `PATCH` answers with
  `turbo-stream` elements.
- Turbo sends the page's CSRF token in `X-CSRF-Token` on every request, and applies a
  `turbo-stream` answer whatever its status, so a 403, 409 or 422 renders its own message on the
  field (🔍 W1 confirms this against the pinned Turbo version).
- A Stimulus controller per job handles what HTML can't: `field` (save on leave, Esc and Cancel,
  disabling while in flight, focus), `tag-combobox`, `group` (collapse) and `grid` (the list's
  roving tabindex). Cancel restores the rest view the controller kept, with no request.

**Answers:**

| Outcome | Status | What the user sees |
|---|---|---|
| Saved | 200 | The new value, a live-region announcement, and a green edge that fades. The page keeps the new `lock_version` for the next save |
| Unchanged (the value was already that) | 200 | The editor closes. No journal entry is written, because Redmine skips an empty one |
| Not editable (role or workflow) | 403 | An error on the field naming it. The field returns to its saved value |
| Stale: changed in another tab or by someone else | 409 | The editor stays open with the user's value, and shows beside it the current value and who changed it and when. Saving again sends the user's value over the other change; Esc keeps the other change. Nothing retries on its own |
| Fails validation | 422 | Redmine's messages on the field, with `aria-invalid`. The editor stays open with the user's value, so they can fix it or press Esc |
| Wrong shape | 400 | Refused, with the field returned to its saved value |
| Connection lost after sending | — | "Couldn't confirm the save. Reload to see whether it was saved." The `field` controller shows it on Turbo's `turbo:fetch-request-error` |

**Stale saves.** The page keeps one `lock_version` per issue: in one element on the issue page,
and on the row in the list. An editor reads it when it sends, not when it opens, and each
successful save streams the new one into that element. Saves for one issue go one at a time: a
save started while another for the same issue is in flight waits for its answer, then sends with
the new `lock_version`. So saving two fields in a row from one tab never conflicts with itself.

When the `lock_version` no longer matches, Rails raises `ActiveRecord::StaleObjectError`; the
controller rescues it, saves nothing and answers 409 with the issue's current value of the field,
its last journal's author and time, and the current `lock_version`. The user's value stays in the
editor, so nothing they typed is lost.

**Journals.** One request changes one field and writes one journal entry, with
`User.current` as its author and the journal's `created_on` as its time. The plugin never merges
or rewrites journal entries.

**Fields that change other fields.** A new status or tracker changes which fields the workflow
lets the user edit, and which it requires. After saving either one, the answer streams each group
rendered again. The `group` controller applies a group's replacement only when no editor in it is
open; a group with an open editor keeps it, and is replaced when that editor closes. So a field
being typed into while the status saves is never thrown away. A status whose workflow makes an
empty field required fails with 422 and names the field; the user fills that field first, or uses
the edit form.

<a id="editors"></a>
### 3.3 Editors by field kind

At rest, an editable value is a button labelled "Edit *Field*, *value*", with a faint pencil. A
value the user can't edit has no button and no pencil. Pressing it opens the editor in place.

| Kind | Editor |
|---|---|
| String, integer, float, link; subject | `input`, numeric `inputmode` for numbers |
| Text; description | `textarea` with core's wiki toolbar. Cmd/Ctrl+Enter saves |
| Single list, user, version, enumeration; status, priority, assignee, category, tracker | `select`, its options rendered by the `edit` route when it opens |
| Multi-valued list, user, version, enumeration | The tag combobox: a tag per value, each with a remove button, and a list of the values not yet chosen |
| Boolean | A checkbox that saves on one click |
| Date; start, due | `input type="date"` |
| Progress bar; % done | `select` of 0 to 100 in steps of 10 |
| Parent task | `input` for an issue number |
| Spent time | Read-only: it is the sum of time entries |
| Attachments, watchers, relations, project | As core shows them, through core's own controls and forms |

The tag combobox follows the WAI-ARIA Authoring Practices combobox pattern: `role="combobox"`
with `aria-expanded`, `aria-controls` and `aria-activedescendant`, a `listbox` of options, the
arrow keys to move, Enter to add, Backspace in the empty input to remove the last tag, and Esc to
close the list.

**Saving and cancelling:**

- Enter saves (Cmd/Ctrl+Enter in a text area). Esc or *Cancel* closes the editor and keeps the
  saved value.
- Leaving the field saves it. When focus moves out of an open editor, by a click elsewhere on the
  page, by Tab, or by opening another field, a changed value is saved and an unchanged one closes.
  Focus stays where the user put it. Esc and *Cancel* are the only ways to throw an edit away.
- Switching to another window or browser tab leaves the editor open, so an edit isn't sent while
  the user is away. The page tells the two apart with `document.hasFocus()` after `focusout`.
- While a save is in flight the editor is disabled, so a second save can't be sent.
- After a save or a cancel made from the editor, focus returns to the field's button.
- Leaving the page while an editor holds a changed value, or while a save is in flight, asks
  first. The `field` controller sets a `beforeunload` handler for as long as either holds, as core
  warns before leaving its edit form with changes.
- **A field that needs a note**, set on the settings page (§3.6), doesn't save on leave. Its
  editor has a required *Why* box, and only Enter or *Save* sends it, with the note as the
  journal's notes. The server refuses such a save without a note (422). gh-plan sets this on its
  baselines, so re-baselining is deliberate and records why (requirement C4).

<a id="groups"></a>
### 3.4 Field groups

The issue's heading keeps what core puts there: the tracker and number, the subject, the author,
the created and updated times, and the private flag. The subject edits in place there; the tracker
and the private flag are shown there and edit in the Status group. Below the heading, every other
field is in one of six groups. The custom fields
here are gh-plan's, as its `setup.rb` places them (§3.6).

| Group | Fields | Opens | Why here |
|---|---|---|---|
| Status | Status, priority, assignee, tracker, private, `Lane`, `Points` | Open | A reader opens the page to see what state the issue is in and who has it. Lane and Points are what the lane board and Next up sort by |
| Schedule | Target version, start date, due date, estimated time, % done, spent time, `Ordinal` | Open | The next question is when it's planned and how far along it is. On a wide screen it sits beside Status, on the same row |
| Description | The description | Open, full width | It's long prose and needs the line length |
| Baseline | `Baseline start`, `Baseline finish` | Closed | Read in a review of the plan, not on every visit. Read-only to everyone but the administrator (invariant 10) |
| Links and history | `Pull requests`, parent task, category, `Old ID` | Closed | Where the work went and where it came from. Looked up, not scanned |
| Other fields | Every custom field nobody has placed | Open, full width, always last | A new field shows up here until an administrator places it |

Relations, subtasks, linked commits, attachments and history stay below the groups, as core
draws them.

The Status group ends with a slot for lines other plugins add. Ulysses's plugin calls its own hook
there, `view_ulysses_status_rows`, with the issue, then sets `@ulysses_status_rows` on the view.
The review findings plugin's Findings line belongs in that slot, since the open silent count is
part of the issue's state. Until Ulysses's plugin is built, the line renders through core's
`view_issues_show_details_bottom` hook, inside `div.attributes` after the fields. The build adds a
listener on the slot to the review findings plugin, and its `view_issues_show_details_bottom`
partial skips once `@ulysses_status_rows` is set, so the line shows once. Either way the line
writes core's attribute markup itself rather than calling `issue_fields_rows`, which Ulysses's
plugin overrides.

**Rendering.** Core's `show.html.erb` is untouched, and draws, in order: the heading
(`render_issue_subject_with_tree`), the attributes (`issue_fields_rows` and
`render_half_width_custom_fields_rows`, inside `div.attributes`), the description, the
attachments, then `render_full_width_custom_fields_rows`. The plugin prepends a module to
`IssuesHelper`:

- `issue_fields_rows` returns every group, the Description group included, and
  `render_half_width_custom_fields_rows` and `render_full_width_custom_fields_rows` return nothing,
  since their fields are in the groups.
- `render_issue_subject_with_tree` wraps the subject in its editor.
- A prepend on `Issue#css_classes` adds `ulysses-grouped` to the issue's `div`, and `base.css`
  hides core's own description block under it (`.ulysses-grouped > .description` and the rule
  before it). The Description group renders the description with the same `quote-reply`
  controller core's block has, so quoting it into a reply still works.

The attachments, relations, subtasks and history follow the groups, as core draws them. With the
setting empty the helpers draw the plugin's default layout (§3.6). If the stored layout can't be
read, they call `super`, `css_classes` adds nothing, and the page looks as core draws it.

- A field the user can't see (a custom field hidden from their role) isn't rendered, as core
  doesn't render it.
- A group with no field the user can see isn't shown.
- A field core disables for the tracker (`disabled_core_fields`) isn't shown.
- Each group is a `section` with a heading. The heading's button has `aria-expanded` and
  `aria-controls` naming the group's body.

<a id="collapse"></a>
### 3.5 Collapse state

- **Where.** One `localStorage` key, `ulysses.collapsed`, holding the IDs of the groups the user
  has collapsed or opened against their default, for every issue. Opening Baseline on one issue
  opens it on the next.
- **Across tabs.** The page listens for the `storage` event, which the browser fires in every other
  tab of the same origin when the key changes, and opens or closes its groups to match. No server
  round trip is involved.
- **Without storage.** Every read and write is wrapped in `try`/`catch`. With storage blocked or
  full, each group starts as its default, and toggling still works for the page.
- **Default.** A group's default (open or closed) comes from the settings (§3.6). The stored state
  overrides it for that browser.
- **Keyboard.** Tab reaches a group's heading button, and Enter or Space opens and closes it.

The state is per browser, not per user. It doesn't follow a user to another browser, and it never
reaches the server, so it isn't data Redmine has to back up.

<a id="group-settings"></a>
### 3.6 Placing custom fields

The groups live in `Setting.plugin_redmine_ulysses`, a serialized hash in Redmine's `settings`
table that needs no migration:

```yaml
groups:
  - { id: status,   name: Status,   open: true,  wide: false, fields: [status_id, priority_id, assigned_to_id, cf_2, cf_1] }
  - { id: schedule, name: Schedule, open: true,  wide: false, fields: [fixed_version_id, start_date, due_date, estimated_hours, done_ratio, spent_hours, cf_6] }
  # … description, baseline, links
notes_required: [cf_3, cf_4]
```

The administrator edits them on the plugin's settings page, *Administration › Plugins › Ulysses ›
Configure*:

- Each group is a card with its name, an *Open/Closed/Open, full width* select, and its fields in
  order. Each field has *Move up* and *Move down* buttons and a group select to move it to another
  group. Nothing depends on drag and drop.
- A *Not in a group* list shows every issue custom field that isn't placed. These are the fields
  the issue page shows under Other fields.
- Groups can be added, renamed, reordered and removed. Removing a group returns its fields to
  *Not in a group*. Other fields can't be removed and is always last.
- Each field has a *Needs a note* checkbox, which puts it in `notes_required` (§3.3).
- Saving drops any key that no longer names a field, such as a deleted custom field, and the
  confirmation names each one dropped.

A new custom field appears under Other fields on every issue until an administrator places it.

With the setting empty, the plugin draws its own default: the core fields in the groups above,
and every custom field under Other fields. gh-plan's `setup.rb` writes the layout above, with its
baselines in `notes_required`, when the setting is empty, looking up each of gh-plan's custom fields by name. A setting already written is
left as it is, so re-running `setup.rb` keeps an administrator's changes.

<a id="list"></a>
### 3.7 The issue list

Every cell of an editable column gets the same rest button and editors as the issue page.

- **Keyboard.** The table is a grid with a roving tabindex. Tab enters it once, the arrow keys move
  between cells, Enter opens a cell's editor and saves, Esc cancels, and Tab leaves the grid.
- **Rendering.** A prepend on `QueriesHelper#column_content` wraps an editable cell's value in its
  rest button. The row carries `data-issue-id` and `data-lock-version`, and the cell `data-field`.
- **Little cost per row.** Which fields a user may edit depends on the issue's project, tracker and
  status, and the role. The list works it out once per distinct project, tracker and status on the
  page, and keeps it for the request, so the cost grows with those combinations, not with the rows.
  An editor's allowed values come from the `edit` route when it opens. A 100-row list adds at most
  10 SQL queries.
- **Context menu.** Editors stop the click from reaching core's row selection and context menu.
- **Sorted or grouped lists.** A saved row stays where it is, marked "moved", rather than jumping
  to its new place. Reloading puts it there.
- **Columns.** Subject, status, priority, assignee, target version, dates, estimate, % done and
  custom field columns edit in place. Spent time, author, created, updated and computed columns
  don't.

<a id="theme"></a>
### 3.8 Themes

The plugin sets the look of the whole page, and a site can swap it for its own with one file
([ADR 0013](../adr/0013-pluggable-themes.md)).

- **The contract.** CSS custom properties named `--uly-*`: colour roles for light and for dark
  (ink, muted and faint text, page, surface and sunk grounds, rules, primary and its fill, accent,
  focus, and ok, warn, danger and info, each with an ink and a soft shade), the header gradient
  (`--uly-header-from`, `--uly-header-to`, `--uly-on-header`), the sans, mono and serif fonts,
  type sizes and leading, spacing, radius, shadow and motion.
- **The reference theme** is the plugin's `tokens.css`, which gives every property a value, drawn
  from *Papilio ulysses*, with its OFL fonts. A design book in the plugin's repo shows it.
- **`base.css`**, in the plugin, maps core's variables onto the contract: `--fonts-main` becomes
  `--uly-font-sans`; the `--oc-gray-*` text, border and ground variables become `--uly-ink`,
  `--uly-muted`, `--uly-rule`, `--uly-surface`, `--uly-page` and `--uly-sunk`; links become
  `--uly-primary`; and core's red and green become `--uly-danger-ink` and `--uly-ok-ink`. It draws
  the header and the top menu as the gradient, and styles the plugin's components. It sets no
  colour of its own.
- **Loading.** On every page, the plugin's `view_layouts_base_html_head` hook links, after core's
  stylesheet, its `tokens.css`, then `base.css`, then the site's theme file if there is one. Whether
  the hook's links come after core's stylesheet in Redmine 7's layout is 🔍 checked in W2.
- **The site's theme file** is `public/ulysses/theme.css` in the Redmine install, the one
  well-known place Ulysses looks. It sets any of the contract's properties, for light and dark,
  and declares its own fonts, kept beside it in `public/ulysses/`. A property it leaves out keeps
  the reference value. Redmine serves `public/` as it is, with nothing compiled and no restart
  (Rails 8.1's `public_file_server` is on by default), so the link carries `?v=` and the file's
  modification time to keep a browser from holding an old copy. No other Redmine plugin sets a
  convention for a site's own file: themes in `themes/` are compiled when Redmine starts, and
  View Customize and redmine_custom_css keep theirs in the database.
- **Redmine's own theme setting** stays on Default. Ulysses isn't a Redmine theme, and another
  theme under it would fight `base.css`.
- **The check.** `bin/rake redmine_ulysses:theme:check[FILE]` fails when a theme file sets a
  property the contract doesn't have, a typo for one, or when, with the reference values filling
  the gaps, a text colour and its ground fall under 4.5:1 in light or in dark.
- **Dark mode** follows `prefers-color-scheme`.

**gh-plan's Redmine** installs a tagged `redmine_ulysses` release, and its theme is glasswing:
`redmine/ulysses/theme.css` and its fonts, which the Dockerfile copies into `public/ulysses/`.
Glasswing's design book sits beside them in `redmine/ulysses/design-book/`.

<a id="failure-modes"></a>
### 3.9 Failure modes

Each of these leaves a trace the user sees; none loses an edit without saying so.

| Failure | What happens |
|---|---|
| Two tabs edit the same issue | The second save is refused with 409 and shows the first change beside the user's value, still in the editor. The user decides which to keep |
| A status save redraws the groups while another field is open | That group keeps its open editor and is redrawn when it closes |
| The user leaves the page with an unsaved edit | The browser asks first (`beforeunload`) |
| The user may not set the value | 403 on the field; the field shows its saved value |
| Redmine drops the key without an error | The controller sees the value unchanged and answers 403, not 200 |
| A validation fails | 422; the editor stays open with the user's value and Redmine's message |
| The connection drops after sending | The field says the save couldn't be confirmed and offers a reload |
| The session has expired | Rails refuses the CSRF token; the field says to sign in again. The edit isn't saved |
| A double press of Enter | The editor is disabled while the save is in flight |
| The settings name a deleted custom field | The field is skipped when rendering, and dropped with a message the next time the settings are saved |
| A Redmine upgrade changes a prepended helper (`IssuesHelper`, `QueriesHelper`, `Issue#css_classes`) | The plugin's tests fail on the upgrade; a layout the plugin can't read falls back to core's page |
| Local storage is blocked | Groups open at their defaults; collapsing still works for the page |

<a id="alternatives"></a>
## 4. Alternatives considered

The plugin-level choices, our own plugin against forking or combining, are in
[ADR 0012](../adr/0012-issue-ux-plugin.md#alternatives), and the theme and widget choices in
[ADR 0013](../adr/0013-pluggable-themes.md#alternatives). Within the design:

- **Saving through core's `PATCH /issues/:id`.** No controller of our own, but each answer is a
  redirect or an HTML page to scrape, and a stale edit comes back as a 200 page with a `.conflict`
  div. Our controller answers with Turbo Streams and a status per outcome.
- **Grouping in the browser.** JavaScript could move `.cf_<id>` nodes into fieldsets after the page
  loads. The page would show core's layout, then jump. Rendering groups on the server avoids both.
- **Shadowing `issues/show.html.erb`.** A copy of core's view in the plugin breaks on each upgrade
  and conflicts with any other plugin that shadows it. Prepending four helpers and hiding core's
  description block changes less.
- **Collapse state in `UserPreference`.** It would follow a user across browsers, at the cost of a
  server round trip per toggle and a write path that isn't journaled. Tabs would still need the
  `storage` event to stay in step.
- **Groups in a migration or a custom-field attribute.** A table of groups
  (redmine_custom_fields_groups' model) or a `format_store` attribute on each custom field would
  work, but core fields can't carry the attribute, and a table needs a migration. One settings hash
  covers core and custom fields alike.
- **Ticking description checkboxes.** Dropped, because #89 turns the Done list into bullets.

<a id="cross-cutting"></a>
## 5. Cross-cutting concerns

| Lens | Answer |
|---|---|
| L1 Security | Each save uses the session and sends the page's CSRF token in `X-CSRF-Token`; the plugin's routes have no `.json` suffix, so Rails checks it. The permission behind each edit is `authorize` on `issues#update`, then `safe_attribute_names` or `editable_custom_field_values` for that user, tracker and status (§3.2). A key `safe_attributes=` drops is refused, never ignored. No API key reaches the page |
| L2 Privacy and data | Every kind of edit, a checkbox click included, goes through `init_journal` with `User.current` and `save`, so each change is one journal entry with its author and time ([`G1`, `G5`](../requirements.md)). Nothing merges or rewrites journals. Collapse state stays in the browser. Tests and examples use invented names |
| L3 Reliability and failure | §3.9. A refused edit shows on the field, and the field returns to its saved value; while a save is in flight the editor is disabled |
| L4 Performance and limits | A save answers in 500 ms or less at the 95th percentile on localhost, and an editor opens in 300 ms or less. A 100-row list adds at most 10 SQL queries, and the plugin's script starts in 100 ms or less on it |
| L5 Observability | The user sees each outcome on the field and hears it from a live region. Redmine's log records each request as it does the form's |
| L6 Compatibility and migration | Redmine 7.0.2 on SQLite, with glasswing as Ulysses's site theme. The REST API and the export are unchanged: the plugin adds no API view, prepends nothing on the API path and adds no custom field. A test compares `/issues/:id.json` keys before and after. Core is unpatched: hooks, the plugin's own views, controller and assets, and `Module#prepend` on `IssuesHelper`, `QueriesHelper` and `Issue#css_classes` |
| L7 Usability and accessibility | Tab reaches a field's button, Enter opens the editor, Enter saves, Esc cancels, and focus returns to the button. A group's heading is a disclosure button, opened with Enter or Space. Errors set `aria-invalid` and `aria-describedby`; a live region announces saves; focus is always visible |
| L8 Testability | §6: Redmine's plugin tests, with system tests in a Chromium container. Each work item's Done names its tests |
| L9 Operations and cost | gh-plan's image installs one more plugin from a tagged public release, with Turbo vendored in it, and copies one theme file and its fonts into `public/ulysses/`. The test image and the Chromium container live in the plugin's repo and run for tests only. No paid component; fonts are OFL |
| L10 Docs impact | This design, ADR 0012 and ADR 0013. Architecture §1, §6.1 (the plugin), §6.3 (the theme) and §9 (the issue page) change in W9, when gh-plan's Redmine runs the plugin, not in this PR. `redmine/README.md` gains the plugin's release and the theme file. The plugin's repo has its own README, install steps, theme guide and design book |
| L11 Invariants | 6 holds: the plugin's JavaScript runs in Redmine's pages, not in the extension, and `vscode/redmine.js` stays the extension's only path to Redmine. 8 holds: `Lane` is single-valued, and an array for it is refused with 400. 10 holds: the baselines are read-only in workflow for every role but the administrator, so `editable_custom_field_values` leaves them out and the page shows them read-only |
| L12 Offline and concurrency | Two tabs, or two people, editing one issue: each save carries `lock_version`, and a stale one is refused with 409, never saved over the other. Collapse state syncs across tabs through the `storage` event. Redmine is on localhost, so there is no offline mode: a lost connection is reported on the field |

<a id="testing"></a>
## 6. Testing

The Redmine image leaves out what the tests need: the test gems, a compiler for them, a test
database and a browser. W1 adds them to the plugin's repo:

- `test/Dockerfile`, from the official Redmine 7.0.2 image, with `build-essential` and
  `bundle config unset without`, then `bundle install`, and the plugin mounted at
  `plugins/redmine_ulysses`;
- a `test:` entry in `database.yml` on SQLite;
- a `selenium/standalone-chromium` container, built for arm64 and amd64, with
  `SELENIUM_REMOTE_URL` and `CAPYBARA_SERVER_HOST` set for Redmine's system test case;
- `bin/test`, which runs `bin/rake redmine:plugins:test NAME=redmine_ulysses` in those containers.

Test fixtures are Redmine's own, plus invented projects, users and custom fields; none come from
gh-plan's data. The plugin's tests, in `test/`:

- **Functional tests** for the controller, one per row of §3.2's answers table: a save that writes
  one journal entry with the user as author; a role without the permission (403); a field the
  workflow makes read-only (403); a hidden custom field (403); a key `safe_attributes=` drops
  (403, no journal); a stale `lock_version` (409, nothing saved); a validation failure (422); a
  field that needs a note, sent without one (422); an array for `Lane` (400); a missing CSRF token
  (refused); an issue the user can't see (404). Each kind is saved once as a core field and once
  as a custom field.
- **Helper tests** for grouping: under a test layout, every core field and a set of invented custom
  fields land in their group; an empty setting draws the plugin's default; an unplaced field lands
  in Other fields; a hidden field and an empty group aren't rendered; a layout that can't be read
  falls back to core's output.
- **System tests** in Chromium: the keyboard path through a field and a group; each editor kind;
  leaving a changed field saves it and leaving an unchanged one closes it; two fields saved back to
  back from one tab both save; a status save leaves another open editor as it is; leaving the page
  with an unsaved edit asks first; a save from a second window is refused as stale and keeps the
  user's value; collapse state survives a reload and reaches a second window; the
  list grid's arrow keys; a 100-row list's query count and script start time.
- **An API test** that compares the keys of `/issues/:id.json` and `/issues.json` with and without
  the plugin.
- **A settings test**: moving a field, adding and removing a group, and dropping a deleted field
  with a message.
- **Theme tests**: the theme check passes on the reference `tokens.css` and on an empty theme
  file, and fails on a file with a property the contract doesn't have and on one with a pair under
  4.5:1. A system test loads a page with and without `public/ulysses/theme.css` and reads a
  property's computed value each way.

In gh-plan, W9 checks the installed release with the plugin's API test against the live image, and
runs the theme check on `redmine/ulysses/theme.css`.

<a id="requirements-coverage"></a>
## 7. Requirements coverage

| # | Requirement | How |
|---|---|---|
| B2 | Estimate visible in list views | Points stays a list column, and edits in place there |
| C3, C4 | Baselines frozen; re-baselining leaves a record | Baselines are read-only to everyone but the administrator. Their edit needs a note and doesn't save on leave (§3.3), so a re-baseline is deliberate, and its journal records who, when and why |
| D4 | Single-valued lane membership | `Lane` edits with a single select, and an array for it is refused |
| G1 | Tracking data is reviewable evidence | Each in-place edit is a journal entry with its author and time |
| G2 | Full export | The export and the REST API are unchanged |
| G3 | No credential in a repo | The page uses the session and CSRF token, never an API key |
| G5 | Change history | As G1 |
| I1, I2, I3 | Free, open source, nothing paid | GPL-2-or-later plugin and theme, OFL fonts, no paid component |

<a id="work-breakdown"></a>
## 8. Work breakdown

Each item is a Redmine issue in lane `web`, related to #91. They aren't subtasks of #91, because
closing a parent is refused while its subtasks are open. W1 to W8 are in a Redmine project
`ulysses` for the plugin's repo, and W9 is in gh-plan's, so Start opens each in the right
checkout (`plan.checkouts`).

| Item | Repo | Builds | After | Points |
|---|---|---|---|---|
| W1 | `redmine_ulysses` | The plugin skeleton, its README and licence, and the test harness (§6). Pins Turbo with Drive off, registers a Stimulus controller from the plugin through core's importmap, and proves a `turbo-stream` answer renders on a 409 | — | 3 |
| W2 | `redmine_ulysses` | The theme contract, `base.css`, the reference `tokens.css` with its palette, fonts and design book, loading the site's theme file, and the theme check (§3.8) | W1 | 5 |
| W3 | `redmine_ulysses` | The `edit` and `PATCH` routes, their checks, notes, and their Turbo Stream answers for the field and `lock_version` (§3.2) | W1 | 5 |
| W4 | `redmine_ulysses` | Issue-page editors for single-valued fields and booleans, saving on leave (§3.3) | W2, W3 | 5 |
| W5 | `redmine_ulysses` | The tag combobox, and the subject and description editors (§3.3) | W4 | 3 |
| W6 | `redmine_ulysses` | Group rendering, the plugin's default layout, the Status group's slot for other plugins' lines with the Findings line moved into it, the groups' redraw after a status or tracker save, and collapse state (§3.2, §3.4, §3.5, §3.6) | W2, W3 | 3 |
| W7 | `redmine_ulysses` | The settings page for placing fields (§3.6) | W6 | 3 |
| W8 | `redmine_ulysses` | Editing in the issue list (§3.7) | W4 | 5 |
| W9 | gh-plan | Installs a Ulysses release; Opale out; glasswing as `redmine/ulysses/theme.css` with its fonts and design book, passing the theme check; `setup.rb` sets the theme to Default and writes gh-plan's layout; `redmine/README.md`, `CLAUDE.md`, and architecture §1, §6.1, §6.3 and §9 | W5, W7, W8 | 5 |
| | | | | **37** |
