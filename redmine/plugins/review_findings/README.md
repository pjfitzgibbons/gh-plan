# Review findings

A Redmine 7.0 plugin that stores code review findings, one row per finding per review round of an
issue's branch, and answers per-rule counts of real findings and noise. It adds one table,
`review_findings`, a project module of the same name, a JSON API and a Findings tab. The schema, the allowed
values and what each field holds are [design 0005 §3.1](../../../docs/design/0005-review-findings.md#schema).

## The Findings tab

The project's **Findings** tab, at `/projects/:project_id/review_findings`, lists the findings of
every issue in the project and its sub-projects that the user can see, each open silent finding
first. `?issue_id=<n>` shows one issue's, and says "No review yet." when it has no rows. An open
silent finding is silent, loses something, and has no outcome. It needs View review findings.

An issue's page shows a **Findings** line, `<k> open silent` or `no review yet`, linking to the
tab filtered to the issue. It counts the outcomes the store holds, so a drafted outcome counts as
open until Ship's `pr` sends it.

A finding's text is shown escaped, as text.

## The API

Every API route is JSON only. Redmine never reads the session on a JSON request, so a caller sends the
API key (`X-Redmine-API-Key`) or basic auth, even from a signed-in browser.

| Route | Does | Permission |
|---|---|---|
| `GET /issues/:issue_id/review_findings.json` | The issue's findings, every round, as `{"review_findings": [...]}` | View review findings |
| `PUT /issues/:issue_id/review_findings.json` | Upserts `{"review_findings": [...]}` by `(fingerprint, round)`, in one transaction, and answers `{"created", "updated", "unchanged"}` | Manage review findings |
| `GET /projects/:project_id/review_findings/rules.json` | `{"rules": [{"rule_id", "real", "noise"}]}` over the issues the user can see in the project and its sub-projects | View review findings |
| `GET /projects/:project_id/review_findings/rules/:rule_id.json` | `{"rule_id", "findings", "confirmed"}`: how many findings have the rule, over the same issues | View review findings |
| `PUT /projects/:project_id/review_findings/rules/:rule_id.json` | Renames the rule to `{"new_rule_id": "…"}` on every finding in the project and its sub-projects, in one transaction, and answers `{"renamed", "noted"}` | Manage review findings |

A `PUT` line is one of two kinds:

- **A round line** has `fingerprint`, `round` and any of `commit`, `file`, `line`, `rule_id`,
  `tag`, `severity`, `visibility`, `summary`, `body`, `verified_round` and `pr_url`. A field it
  leaves out is kept. On a confirmed row it sets only `verified_round`.
- **An outcome line** has `"confirm": true`, `fingerprint`, `round`, `outcome`, and any of
  `justification`, `rule_id`, `severity` and `visibility`. It needs a row already there, and it
  sets who confirmed it and when.

Any change to a confirmed row adds a note to the issue's journal, naming the user and each field's
old and new values. A line that changes nothing changes nothing, so sending the same `PUT` twice
saves once.

A value outside the allowed ones, an unknown field, or a `pr_url` that isn't
`https://github.com/<owner>/<repo>/pull/<n>` gets a 422 with `{"errors": [...]}`, each naming its
row, and nothing in the request is saved. An empty string is saved as `NULL`. Two `PUT`s of one
round at once meet at the unique index on `(issue_id, fingerprint, round)`; the loser runs once
more and finds the winner's rows.

`rules.json` counts confirmed rows that have a rule, each fingerprint once, by its latest confirmed
round: `fixed` and `intentional` are real, `wrong` and `doesnt-matter` are noise. It counts only
the projects where the user has View review findings.

A rename (§3.10) adds the same journal note to the issue of each confirmed finding it changes. A
`new_rule_id` that isn't lowercase words and digits joined by hyphens, or is the old id, gets a
422, and so does a rename when a finding with the old id is on an issue the user can't see or
can't manage findings in: it renames all of them or none. `redmine-set rule OLD NEW` sends it.

Deleting an issue leaves its findings' rows in the table.

## Mounting it

Redmine loads plugins from `plugins/` under its root, `/usr/src/redmine` in the official image.
Mount this folder there, read-only. The plugin has a migration, so the container needs
`REDMINE_PLUGINS_MIGRATE=1`, which makes the image's entrypoint run `rake redmine:plugins:migrate`
on start. `compose.yml` does both. *Administration › Plugins* lists it as "Review findings" once
it has loaded.

To remove the table, roll the plugin's migration back before unmounting it:

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
