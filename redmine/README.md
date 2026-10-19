# Redmine

The tracker of [ADR 0011](../docs/adr/0011-redmine-is-the-tracker.md): Redmine 7.0 in Docker on
localhost, on SQLite, with the [lane board](plugins/lane_board/README.md) loaded. Run every
command here from the root of the gh-plan checkout.

| File | Holds |
|---|---|
| `compose.yml` | The service: the built image, the port, the volumes and the mounts |
| `Dockerfile` | The image: the pinned Redmine with the pinned Opale theme, its assets compiled |
| `setup.rb` | The projects, fields, status, workflow, settings, repositories and versions |
| `import.rb` | The one-time import of each repo's `.tracker/` items |
| `backfill.rb` | The one-time backfill of each imported issue's commits, pull requests and status history |
| `plugins/lane_board/` | The lane board plugin, mounted read-only |

## Starting and stopping

```
docker compose -f redmine/compose.yml up -d     # start; first start creates the database
docker compose -f redmine/compose.yml stop      # stop, keeping the container
docker compose -f redmine/compose.yml down      # stop and remove the container; data stays
docker compose -f redmine/compose.yml logs -f   # follow the log
```

Redmine is at <http://127.0.0.1:3010>, bound to localhost only. `REDMINE_PORT` picks another
port. The first sign-in is `admin` / `admin`, and Redmine makes you change the password at once.
Nothing in the repo holds the new password or any API key.

Each tracked repo's `.git` is mounted read-only, from `$HOME/programming/gh-plan/.git` and
`$HOME/programming/tempotag/.git`. `GH_PLAN_GIT` and `TEMPOTAG_GIT` point them elsewhere. Mount a
checkout's `.git` directory, not a worktree: a worktree's `.git` is a file naming a path on the
Mac, which the container can't follow.

## Where the data lives

Two named volumes of the `gh-plan-redmine` compose project:

| Volume | Mounted at | Holds |
|---|---|---|
| `gh-plan-redmine_db` | `/usr/src/redmine/sqlite` | `redmine.db`, the whole database |
| `gh-plan-redmine_files` | `/usr/src/redmine/files` | Attachments |

`down` keeps them; `down -v` deletes them, and with them every issue. Backup and export are
pl-pakiz's.

## Running the setup

After the first start, and after any change to `setup.rb`:

```
docker compose -f redmine/compose.yml exec -u redmine redmine bin/rails runner /setup/setup.rb
```

It prints each thing it makes or changes, then a count, or `nothing to change` when the Redmine
already matches. Re-running it is always safe: it runs in one transaction, makes only what is
missing, and leaves alone what it doesn't own, such as a version's status or description once
made. It needs no API key, because it works on the database from inside the container.

What it sets up:

- A private parent project `plan`, with sub-projects `gh-plan` and `tempotag`, each with the
  lane board, Gantt and repository modules.
- The default trackers, statuses, roles and workflows, on an empty database.
- An `Icebox` status, open, with the transitions of New in each workflow, so it opens no route a
  role lacks. The lane board leaves it out.
- The issue custom fields `Points` (1, 2, 3, 5, 8, 13), `Lane` (cli, engine, github, infra, model,
  web, core), and the dates `Baseline start` and `Baseline finish`; and for the import, `Old ID`
  (searchable) and `Ordinal`, a whole number in `ready.yml`'s order; and `Pull requests`, each
  merged PR as a Markdown link, which `backfill.rb` fills.
- The baselines read-only, by workflow, for every role. A `Baseline editor` role, with no members,
  keeps them editable by the administrator, who holds every role in workflow checks.
- **View lane board** for every role.
- The REST API, cross-project relations and cross-project commit references switched on.
- `refs` as the commit keyword that links an issue, and `fixes` as the one that closes it.
- Each repo registered as its project's Git repository, at `/repos/<project>.git`.
- One version per milestone in `plan`, shared with its tree.
- The Opale theme, and the `Pull requests` column in the default issue list.
- A public query `Next up` in `plan`: open issues, Icebox left out, in Ordinal order.
- The home page: links to `plan` and to `Next up`.

`compose.yml` mounts `setup.rb`, `import.rb` and `backfill.rb` as single files. An editor that
saves by replacing the file leaves the container holding the old one, or none, so recreate the
container (`up -d --force-recreate`) after editing one.

## Importing the items

`import.rb` imports every item in gh-plan's and tempotag's `.tracker/` once
([ADR 0011](../docs/adr/0011-redmine-is-the-tracker.md), *Consequences*), keeping each body, with
the item's ID in the `Old ID` field. It reads gh-plan at `origin/main` and tempotag from its index,
through the `.git` mounts. tempotag's titles and bodies are in its GitHub issues, and the container
has no `gh`, so they come in on stdin:

```
gh issue list -R pjfitzgibbons/tempotag --state all --limit 100 \
  --json number,title,body,closedAt > .tmp/tempotag-issues.json
docker compose -f redmine/compose.yml exec -T -u redmine redmine \
  bin/rails runner /setup/import.rb --dry-run < .tmp/tempotag-issues.json
```

`--dry-run` makes everything, prints it and rolls it back. Without it the import writes, in one
transaction, so a failed run leaves nothing. `--check` writes nothing: it compares each item with
its issue, field by field, prints each difference and exits 1 if there is one. Re-running is safe:
an issue is found by its Old ID and left as it is, and only a missing issue, parent or relation is
made.

A Done item's finish is the first commit on `origin/main` whose copy of it reads `status: Done`,
its squash-merge, or for tempotag the issue's `closedAt`. It becomes the issue's `closed_on` and
due date. `started` becomes the start date. Both dates are days in America/Los_Angeles.

## Backfilling the history

The import makes no journals. `backfill.rb` adds, once, what git and GitHub hold of each issue's
past. It links each commit to the issue whose Old ID it names. It adds a note for each merged
pull request that names the issue, and links them all in its `Pull requests` field. It records a
status change for each commit that changed the item's status. Every journal is peter's, dated by
its evidence, with a note naming that evidence. The pull requests and tempotag's issue dates come
in on stdin; the top of `backfill.rb` has the command that gathers them. `--dry-run` does the
whole backfill, prints it and rolls it back. Re-running makes nothing: a journal whose note the
issue already has is skipped.

Redmine's commit keyword scan is off while the backfill fetches the old commits. Their `#<n>`
numbers GitHub issues, and "Closes #3" would otherwise close Redmine's #3. Commits fetched later
are scanned as usual.

## Upgrading

1. Read the Redmine changelog for every release between the two tags.
2. Change `FROM` in the `Dockerfile` to the new tag, and `image:` in `compose.yml` to match. Pin an
   exact tag such as `7.0.3`, never `7.0`. A new Opale release changes the `ADD` line's URL and
   checksum (`shasum -a 256` of the zip).
3. Back up the `db` volume.
4. `docker compose -f redmine/compose.yml up -d --build`. The image migrates the database on start.
5. Check that *Administration › Plugins* still lists the lane board, and its tab still loads.
6. Re-run the setup, which should print `nothing to change`.

Anything that recreates the container signs everyone out: an upgrade, `down` then `up`, or a
change to `compose.yml`. The session secret is generated inside the container, so a new
container makes a new one.

## Commits and closing

Redmine reads a repository's new commits only when someone opens its Repository tab, so a
`fixes #<n>` commit closes the issue at that fetch, not when it is committed. The issue's
`closed_on` is the fetch time; the commit's time is the changeset's `committed_on`.
Opening the tab fetches now. Fetching after each merge is pl-sijil's, in the icebox.

GitHub makes a PR's title the subject of its squash commit, so the title carries the keyword: a
PR that finishes issue `<n>` ends its title `(fixes #<n>)`, and one that does part of it ends
`(refs #<n>)`. A `refs` commit leaves the issue open, to be closed by hand.
