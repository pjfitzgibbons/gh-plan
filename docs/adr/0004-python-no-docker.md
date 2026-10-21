# ADR 0004: Python, run with `uv run`, with no Docker on the run path

<a id="status"></a>
## 1. Status

Superseded by [ADR 0011](0011-redmine-is-the-tracker.md): the Python package is retired, and
Redmine runs in Docker.

<a id="context"></a>
## 2. Context

The tool is a single-user program on one machine. Its job is to read and write a git working tree
and to call `gh` with the host's credentials. That call is a fixed constraint:
[`G3`](../requirements.md) forbids a new credential, and `gh` is already authenticated.

The committed tracker files are YAML, and Markdown with YAML front matter
([ADR 0007](0007-tracker-files-are-yaml.md)). The schema
has to be validated at write time for [`B3`](../requirements.md), and the index is SQLite
([ADR 0001](0001-git-is-the-database.md)).

<a id="decision"></a>
## 3. Decision

The tool is written in Python 3.14 or later. The floor is the one version CI tests. Python was
chosen for knowledge and simplicity.

It runs with `uv run`, and `uv.lock` pins its dependencies. Docker is not on the run path.

<a id="alternatives"></a>
## 4. Alternatives considered

- Go. It builds a single static binary with no interpreter or virtual environment, so it would
  still run years from now, and its reproducibility is strong. `modernc.org/sqlite` gives SQLite
  in pure Go with no cgo, and `html/template` covers templating. It is the better choice if the
  tool should be a standalone artifact installed with `go install`. Against it: validation is
  written by hand or with `go-playground/validator`, where Pydantic needs less code.
- TypeScript or Node. A build step and `node_modules` for a tool with no rich front end.
- Rust. Compile times and ceremony for a CRUD application.
- Ruby or PHP. Not chosen; Python won on knowledge and simplicity.
- Docker as the run path. Running in a container means bind-mounting the repo and `~/.config/gh`
  and reconciling uid and gid, which is more moving parts than `uv run plan serve` for a
  single-user tool bound to localhost. Docker's advantages do not apply here: there is one
  service, `uv.lock` already pins dependencies, and the tool must not be isolated from the repo it
  edits.

<a id="consequences"></a>
## 5. Consequences

- A machine running the tool needs Python 3.14, `uv`, and an authenticated `gh`.
- The libraries follow from the choice: Pydantic v2 for the schema, Typer for the CLI, ruamel.yaml
  for the tracker files, and the standard library's `sqlite3` for the index
  ([design 0001, *Layout*](../design/0001-phase-1.md#layout)).
- `gh.py`, `git.py` and the `timekeeping/` adapters ([ADR 0006](0006-tracked-time.md)) are the only
  modules that spawn a process or open a network connection, so everything else is testable without
  a network.
- A `Dockerfile` is optional. It can run `plan validate` hermetically in CI, and it serves as a
  reproducibility fallback if the tool is still wanted years from now.
