# Review findings

A Redmine 7.0 plugin that keeps an issue's code review as a ledger: one `review_rounds` row per
reviewed commit, and one `review_findings` row per event on a finding, the reviewer raising it or
checking its fix, or a person's answer. Rows are only added. It adds the two tables, a project
module named `review_findings`, a JSON API and a Findings tab. The schema, the allowed values and
what each field holds are [design 0005 §3.1](../../../docs/design/0005-review-findings.md#schema).

A finding's key is `file:lines:rule`, such as `lib/sync.rb:12-14:ignored-file-overwrite`, and its
status is its key's latest row, by round, an answer after the review of its round: `open`,
`fixed, unchecked` (answered `fixed`, not yet checked by a later round), `fixed` (checked),
`intentional`, `wrong` or `doesnt-matter`. A finding is *open to fix* when it's open and silent
and loses something, or its rule is `crash`.

## The Findings tab

The project's **Findings** tab, at `/projects/:project_id/review_findings`, has a line per finding
of every issue in the project and its sub-projects that the user can see, from its latest row, each
open finding to fix first. `?issue_id=<n>` shows one issue's, and says "No review yet." when it has
no rows. It needs View review findings.

An issue's page shows a **Findings** line, `<k> open to fix` or `no review yet`, linking to the
tab filtered to the issue. It counts the answers the ledger holds, so a drafted outcome counts as
open until Ship's `pr` sends it.

A finding's text is shown escaped, as text.

## The API

Every API route is JSON only. Redmine never reads the session on a JSON request, so a caller sends the
API key (`X-Redmine-API-Key`) or basic auth, even from a signed-in browser.

| Route | Does | Permission |
|---|---|---|
| `GET /issues/:issue_id/review_findings.json` | `{"last_round", "review_rounds", "review_findings", "findings", "open_to_fix", "fixed_unchecked", "rules"}`: the issue's rounds and rows, one `findings` entry per key with its status and latest row, and per rule the number of keys | View review findings |
| `POST /issues/:issue_id/review_findings.json` | Adds `{"review_round": {...}}` and `{"review_findings": [...]}` in one transaction, and answers `{"created", "skipped", "round"}` | Manage review findings |
| `GET /projects/:project_id/review_findings/rules.json` | `{"rules": [{"rule_id", "real", "noise"}]}` over the issues the user can see in the project and its sub-projects | View review findings |
| `GET /projects/:project_id/review_findings/rules/:rule_id.json` | `{"rule_id", "findings", "confirmed"}`: how many keys have the rule, and how many have an answer, over the same issues | View review findings |
| `PUT /projects/:project_id/review_findings/rules/:rule_id.json` | Renames the rule to `{"new_rule_id": "…"}` on every row in the project and its sub-projects, in one transaction, and answers `{"renamed", "noted"}` | Manage review findings |

A `review_round` has `round`, `commit` and `ci` (`success`, `failure` or `none`), and any of
`ci_summary`, `repo` (`owner/repo`), `pr_number`, `base`, `effort`, `review_md`, `summary`,
`controls` and `new_rules`. Its `round` is the issue's `last_round` + 1, whatever its `ci`; a
commit is reviewed once. A `POST` may leave out `review_findings`, as a clean round does.

A `review_findings` row has `round`, `source`, `file`, `line_start`, `line_end`, `rule_id`,
`outcome` and `severity`, and any of `tag`, `visibility`, `summary`, `body` and `justification`:

- **From `review`**, `new` raises a finding, or reopens one whose fix didn't hold; `fixed` checks a
  fix, and needs an earlier round to have raised the key. Round 0 is a missed finding (§3.8).
- **From `outcomes`**, a person's answer: `fixed`, `intentional`, `wrong` or `doesnt-matter`, with a
  `justification` for any but `fixed`. It needs a round up to its own to have raised the key, and
  it records the sender as `confirmed_by_id`.

A row in a round other than 0 needs that round recorded, before or in the same `POST`.

A row or round identical to one already there is skipped, so the same `POST` twice adds nothing. One
whose event, `(file, line_start, line_end, rule_id, round, source)`, or whose round, is already
there with other values gets a 422; a later answer goes in a later round. So does a value outside
the allowed ones, an unknown field, or a round that isn't the next one. A 422 answers
`{"errors": [...]}`, each naming its row, and nothing in the request is saved. An empty string is
saved as `NULL`. Two `POST`s of one round at once meet at a unique index; the loser runs once more
and skips the winner's rows.

`rules.json` counts each key once, by its latest answer: `fixed` and `intentional` are real, `wrong`
and `doesnt-matter` are noise. It counts only the projects where the user has View review findings.

A rename (§3.10) is the one change to rows already there: a rule's id is a label, not an event. It
adds a journal note to each issue it touches. A `new_rule_id` that isn't lowercase words and digits
joined by hyphens, or is the old id, gets a 422, and so does a rename when a row with the old id is
on an issue the user can't see or can't manage findings in, or when a renamed row's key is already
another row's: it renames all of them or none. `redmine-set rule OLD NEW` sends it.

Deleting an issue leaves its rows in the tables.

## Mounting it

Redmine loads plugins from `plugins/` under its root, `/usr/src/redmine` in the official image.
Mount this folder there, read-only. The plugin has a migration, so the container needs
`REDMINE_PLUGINS_MIGRATE=1`, which makes the image's entrypoint run `rake redmine:plugins:migrate`
on start. `compose.yml` does both. *Administration › Plugins* lists it as "Review findings" once
it has loaded.

To remove the tables, roll the plugin's migrations back before unmounting it:

```
docker compose -f redmine/compose.yml exec -u redmine redmine \
  bin/rake redmine:plugins:migrate NAME=review_findings VERSION=0
```

## Turning it on

`setup.rb` does both steps for gh-plan's projects.

1. In each project, *Settings › Modules*: tick **Review findings**.
2. In *Administration › Roles and permissions*: give each role that should read findings **View
   review findings**, and each that should record them **Manage review findings**.

## Tests

From the root of the gh-plan checkout:

```
redmine/test_plugin review_findings
```

It builds the `Dockerfile`'s `test` stage as `gh-plan-redmine:test`, with Redmine's test gems and a
test database, and runs it with this folder mounted read-only. In that container it migrates the
plugin, rolls it back to `VERSION=0` and checks that no table is left, migrates it again, then runs
`rake redmine:plugins:test NAME=review_findings`. The live Redmine and its image are left alone.
The fixtures are invented rows on Redmine's own fixture issues.
