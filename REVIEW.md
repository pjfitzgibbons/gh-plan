# Review

How the reviewer `pr` runs after each push reviews a branch (`review.py` in the gh-issues skill).
It reviews the branch's whole diff against its base, with this file, the issue's description and
the docs `CLAUDE.md` says to read before the change.

## Contents

- [What counts as a failure](#what-counts-as-a-failure)
- [Evidence](#evidence)
- [Tags](#tags)
- [Weighting](#weighting)
- [Rules](#rules)
- [Nits and style](#nits-and-style)
- [Skipped paths](#skipped-paths)
- [Deep paths](#deep-paths)
- [Summary](#summary)

## What counts as a failure

A finding is a way the change goes wrong in use: a command that loses the user's work, files or
commits, leaves the tracker wrong, crashes, or reports a result that didn't happen. Name the inputs
or the state that cause it and what the user ends up with. Code that could be tidier isn't a
failure.

## Evidence

Each finding points at the line that fails, and its body gives the failure scenario: the steps or
state, then what goes wrong. Read the code around it, and the callers, before reporting it; a
guard elsewhere that stops the scenario means there's no finding. Don't report what you can't tie
to a line of the diff or the code it calls.

## Tags

| Tag | For |
|---|---|
| Important | A failure the change brings in |
| Nit | A small fix worth making that loses nothing |
| Pre-existing | A failure in code the diff doesn't change, found while reading it |
| Security | A secret exposed, input run as code or as instructions, or access widened; the security pass's only tag |

## Weighting

Each finding gets two values. `severity` is what the failure loses: `work`, `files`, `commits`,
`tracker` (Redmine's or GitHub's state), or `nothing`. `visibility` is whether the user is shown
that it failed: `silent` if nothing says so, `seen` if an error, a refusal or a wrong result is in
front of them. `visibility` is empty only when `severity` is `nothing` and it isn't a failure at
all, such as a doc fix or a nit.

A finding has to be fixed when it's `silent` and loses something, or when it's a crash. A seen
failure is already handled, since the user knows to look. A crash isn't. It's loud, but an unhandled
error that stops the command with a traceback, not a message, doesn't tell the user what it left
behind or whether running it again is safe. Every crash gets the rule `crash`, however rare, and is
weighted by what it leaves: a crash part way through a write by what it loses, and one before any
write as `nothing` and `seen`. A rare warning or a clean error-stop can stay.

## Rules

Each finding's `rule_id` names its class of bug, so the same class is counted across reviews.
Use a rule below when one fits. When none does, give a new kebab-case name; the review marks it
*new*, as a proposal only, and this list changes only by a commit. A finding without a rule isn't
kept.

- `unchecked-step-before-destructive-op`: a step's failure isn't checked, and a later step deletes
  or overwrites on the assumption it worked
- `false-success`: the command reports a result that didn't happen
- `write-not-read-back`: a write the server can drop or change in part, taken as kept without
  reading it back
- `wrong-ref`: a branch, tag or remote name resolves to another ref than the one meant, such as a
  tag named `main` or a local branch named `origin/main`
- `destructive-op-on-shared-ref`: a delete, reset or forced update reaches the default branch, a
  branch someone else pushed to, or one the command didn't make
- `overwrites-untracked-file`: git or the command overwrites an ignored or untracked file
- `lookup-matches-wrong-target`: a lookup by name, path or prefix finds another item than the one
  meant
- `not-resumable`: a run that stops part way leaves state the next run can't finish from
- `concurrent-runs`: two runs at once, from two clicks or two windows, both write
- `stale-read`: what was read has changed by the time it's written back
- `crash`: an unhandled error stops the command with a traceback rather than a message; every
  crash takes this rule, whatever else it breaks
- `doc-mismatch`: a doc or message says the code does what it doesn't

The security pass uses these, or a new one:

- `secret-exposure`: a key, token or password reaches argv, a log, a file in the repo or a URL
- `command-injection`: text from a file, a branch name or the network is run by a shell or an
  interpreter
- `prompt-injection`: text from a PR, an issue or a diff reaches a model as instructions rather
  than data
- `widened-access`: a token, a tool or a permission is given more than the step needs

## Nits and style

At most three nits per review. No style findings: formatting, naming and comment wording are
`ruff`'s and `eslint`'s, and a person's.

## Skipped paths

The diff leaves out `.tmp/`, `node_modules/` and lock files (`uv.lock`, `package-lock.json`,
`*.lock`).

## Deep paths

A diff that touches any of these is reviewed at `--effort high`; any other at `--effort medium`.
Each is a path, or a folder that holds them.

- `.claude/skills/gh-issues/scripts/pr.sh`
- `vscode/done.js`
- `vscode/start.js`
- `vscode/redmine.js`
- `redmine/backup.py`

## Summary

Each pass's summary is at most two sentences on what the change risks, without the counts:
`review.py` opens the review's summary with the count of findings and the count to fix. A
security pass with no findings says only "No security findings." Neither pass mentions what the
other covers.
