# technical-writer — gh-plan

<a id="technical-writer-lenses"></a>

## 1. Lenses

Two lenses are added to the global L1–L10:

| Lens | Question |
|---|---|
| L11 Invariants | Which of the invariants in [architecture §10](../architecture.md#invariants) does this touch, and why does each still hold? |
| L12 Offline and concurrency | What happens without the network? What happens when two clones or agents edit the same issue? |

<a id="technical-writer-design-docs"></a>

## 2. Design docs

After the global sections 1–5, a design doc here adds:

6. Testing
7. Requirements coverage: which IDs in [requirements](../requirements.md) the design meets
8. Work breakdown: the issues that build it

<a id="technical-writer-issues"></a>

## 3. Issues

An issue is a Redmine issue, `#<n>` ([ADR 0011](../adr/0011-redmine-is-the-tracker.md)): its fields, then a Markdown description.

- **Subject:** an imperative outcome. It carries no estimate.
- **Tracker:** `Feature`, `Bug` or `Support`, and names the branch: `feat/`, `fix/` or `chore/`.
- **Fields:** `Points`, `Lane`, `Ordinal`, the version, the parent and the relations are fields, never lines in the description. Points is one of 1, 2, 3 or 5. 8 and 13 mean "split this" and are never used as an estimate.
- **Sections:** the global four, Task, Guardrails, Lenses and Done. Lenses lists L1 to L12. Done's pass/fail conditions include what the test suite proves, and each `[x]` lens has one that names it, as `L11: …`. There is no *Planning* section; the fields hold what it held.
- **Lanes** are the values of Redmine's `Lane` field: `cli`, `core`, `engine`, `github`, `infra`, `model`, `web`.
- **Dependencies** are Redmine's `blocks` relations, and children are sub-tasks, never lines in the description.
- **Imported issues** are rewritten to this outline when they're picked up, before any code, and never in bulk. An issue still in the older outline (Summary, Scope, Acceptance, Tests, Lenses) is rewritten the same way.
