"""redmine/sql.rb: one read-only query on Redmine's database, on a copy of a fixture database.

The fixture has the `review_findings` table as the plugin's migration makes it, and an `issues`
table with the columns the README's queries read. The README's queries run against it too, so a
query that stops parsing fails here. The host's Ruby needs the sqlite3 gem; without it the tests
skip, except in CI, where they fail.
"""

import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SQL_RB = ROOT / "redmine" / "sql.rb"
README = ROOT / "redmine" / "README.md"
MIGRATION = ROOT / "redmine/plugins/review_findings/db/migrate/002_create_review_ledger.rb"

SCHEMA = """
CREATE TABLE issues (id integer PRIMARY KEY, subject varchar NOT NULL);
CREATE TABLE "review_findings" ("id" integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  "issue_id" integer NOT NULL, "round" integer NOT NULL, "source" varchar NOT NULL,
  "file" varchar NOT NULL, "line_start" integer NOT NULL, "line_end" integer NOT NULL,
  "rule_id" varchar NOT NULL, "outcome" varchar NOT NULL, "tag" varchar,
  "severity" varchar NOT NULL, "visibility" varchar, "summary" varchar(255), "body" text,
  "justification" text, "confirmed_by_id" integer, "created_on" datetime(6) NOT NULL);
"""
# (issue, round, source, file, line_start, line_end, rule, outcome, severity, visibility, summary,
# created)
FINDINGS = [
    # Raised, answered fixed, and the fix checked in round 2.
    (
        7,
        1,
        "review",
        "a.py",
        3,
        3,
        "ignored-file-overwrite",
        "new",
        "files",
        "silent",
        "overwrites a file",
        "2026-10-12 12:00:00",
    ),
    (
        7,
        1,
        "outcomes",
        "a.py",
        3,
        3,
        "ignored-file-overwrite",
        "fixed",
        "files",
        "silent",
        "overwrites a file",
        "2026-10-13 12:00:00",
    ),
    (
        7,
        2,
        "review",
        "a.py",
        3,
        3,
        "ignored-file-overwrite",
        "fixed",
        "files",
        "silent",
        "overwrites a file",
        "2026-10-19 12:00:00",
    ),
    # Open to fix: silent, and loses something.
    (
        7,
        2,
        "review",
        "b.py",
        9,
        9,
        "lost-commit",
        "new",
        "commits",
        "silent",
        "drops a commit\twith a tab",
        "2026-10-19 12:00:00",
    ),
    # Open to fix: a crash, though it's loud.
    (
        8,
        1,
        "review",
        "c.py",
        1,
        4,
        "crash",
        "new",
        "work",
        "loud",
        "loses an edit",
        "2026-10-12 12:00:00",
    ),
    # Raised twice, then answered wrong in its second round.
    (
        8,
        1,
        "review",
        "d.py",
        5,
        5,
        "lost-commit",
        "new",
        "commits",
        "silent",
        "answered in its second round",
        "2026-10-12 12:00:00",
    ),
    (
        8,
        2,
        "review",
        "d.py",
        5,
        5,
        "lost-commit",
        "new",
        "commits",
        "silent",
        "answered in its second round",
        "2026-10-19 12:00:00",
    ),
    (
        8,
        2,
        "outcomes",
        "d.py",
        5,
        5,
        "lost-commit",
        "wrong",
        "commits",
        "silent",
        "answered in its second round",
        "2026-10-19 13:00:00",
    ),
    # Not yet answered, and loses nothing, so not open to fix.
    (
        8,
        1,
        "review",
        "f.py",
        2,
        2,
        "ignored-file-overwrite",
        "new",
        "nothing",
        None,
        "not yet answered",
        "2026-10-12 12:00:00",
    ),
    # Its issue was deleted: the plugin's routes no longer see it, so the queries leave it out too.
    (
        99,
        1,
        "review",
        "e.py",
        1,
        1,
        "ignored-file-overwrite",
        "new",
        "files",
        "silent",
        "on a deleted issue",
        "2026-10-12 12:00:00",
    ),
    (
        99,
        1,
        "outcomes",
        "e.py",
        1,
        1,
        "ignored-file-overwrite",
        "fixed",
        "files",
        "silent",
        "on a deleted issue",
        "2026-10-12 13:00:00",
    ),
]


RUBY = shutil.which("ruby")


def _ruby_with_sqlite3() -> bool:
    if not RUBY:
        return False
    return subprocess.run([RUBY, "-rsqlite3", "-e", ""], capture_output=True).returncode == 0


if not _ruby_with_sqlite3():
    if os.environ.get("CI"):
        pytest.fail("CI has no Ruby with the sqlite3 gem: test.yml installs it", pytrace=False)
    pytest.skip("needs ruby with the sqlite3 gem", allow_module_level=True)


@pytest.fixture(scope="module")
def fixture_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("fixture") / "redmine.db"
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=wal")  # as the live database is
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO issues VALUES (?, ?)", [(7, "seven"), (8, "eight")])
    db.executemany(
        "INSERT INTO review_findings (issue_id, round, source, file, line_start, line_end,"
        " rule_id, outcome, severity, visibility, summary, created_on)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        FINDINGS,
    )
    db.commit()
    db.close()
    return path


@pytest.fixture
def db(fixture_db, tmp_path) -> Path:
    copy = tmp_path / "redmine.db"
    shutil.copy(fixture_db, copy)
    return copy


def sql(db: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [RUBY, str(SQL_RB), *args],
        env={**os.environ, "SQL_DB": str(db)},
        capture_output=True,
        text=True,
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_select_prints_tab_separated_rows_with_the_column_names_first(db):
    run = sql(db, "SELECT id, subject FROM issues ORDER BY id")
    assert (run.returncode, run.stderr) == (0, "")
    assert run.stdout == "id\tsubject\n7\tseven\n8\teight\n"


def test_null_is_empty_and_tabs_newlines_and_backslashes_are_escaped(db):
    run = sql(
        db,
        "SELECT NULL AS n, 'a\tb' AS t, 'c' || char(10) || 'd' AS nl, 'e' || char(13) AS cr,"
        " 'e\\f' AS bs",
    )
    assert run.returncode == 0
    assert run.stdout == "n\tt\tnl\tcr\tbs\n\ta\\tb\tc\\nd\te\\r\te\\\\f\n"


def test_every_other_control_character_prints_as_a_hex_escape(db):
    run = sql(
        db,
        "SELECT char(0) || char(27) || '[2J' || char(11) || char(12) || char(127)"
        " || char(155) || '2J' || 'é' AS c",
    )
    assert (run.returncode, run.stderr) == (0, "")
    assert run.stdout == "c\n\\x00\\x1b[2J\\x0b\\x0c\\x7f\\x9b2Jé\n"


def test_a_blob_or_bytes_that_arent_utf8_print_as_replacement_characters(db):
    run = sql(db, "SELECT 'é' AS e, x'ff' AS blob, CAST(x'ff' AS TEXT) AS bad")
    assert (run.returncode, run.stderr) == (0, "")
    assert run.stdout == "e\tblob\tbad\né\t\ufffd\t\ufffd\n"


def test_vacuum_into_is_refused_and_writes_no_copy(db, tmp_path):
    copy = tmp_path / "copy.db"
    run = sql(db, f"VACUUM INTO '{copy}'")
    assert run.returncode == 2
    assert "VACUUM is refused" in run.stderr
    assert not copy.exists()


# On a read-only connection to a WAL database with no -wal file, as the copy is, SQLite 3.45
# reports leaving WAL mode as a disk I/O error rather than a read-only one; another version may
# not. Either way nothing changes.
@pytest.mark.parametrize(
    "query, error",
    [
        ("CREATE TABLE x (y)", "ReadOnlyException"),
        ("DELETE FROM review_findings", "ReadOnlyException"),
        (
            "UPDATE review_findings SET rule_id = 'new-id' WHERE rule_id = 'lost-commit'",
            "ReadOnlyException",
        ),
        ("PRAGMA journal_mode=delete", "(IOException|ReadOnlyException)"),
    ],
)
def test_a_write_raises_and_leaves_the_database_unchanged(db, query, error):
    before = digest(db)
    run = sql(db, query)
    assert run.returncode == 1
    assert re.search(f"SQLite3::{error}", run.stderr)
    assert run.stdout == ""
    assert digest(db) == before
    tables = sqlite3.connect(db).execute("SELECT name FROM sqlite_master").fetchall()
    assert ("x",) not in tables


def test_a_second_statement_is_refused_not_ignored(db):
    run = sql(db, "SELECT 1; DELETE FROM issues")
    assert run.returncode == 2
    assert "one statement only" in run.stderr
    assert run.stdout == ""
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM issues").fetchone() == (2,)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1 AS one;",
        "SELECT 1 AS one;;",
        "SELECT 1 AS one; -- a note",
        "SELECT 1 AS one; /* unclosed",
        "-- a note\nSELECT 1 AS one /* and another */ ;",
        "  /* a note */ select 1 AS one",
    ],
)
def test_comments_semicolons_and_space_around_one_statement_are_allowed(db, query):
    run = sql(db, query)
    assert (run.returncode, run.stderr, run.stdout) == (0, "", "one\n1\n")


def test_a_bad_query_names_the_error(db):
    run = sql(db, "SELEC 1")
    assert run.returncode == 1
    assert "SQLite3::SQLException" in run.stderr


@pytest.mark.skipif(os.getuid() == 0, reason="root owns the file this test borrows")
def test_a_database_another_user_owns_is_refused():
    run = sql(Path("/etc/hosts"), "SELECT 1")
    assert run.returncode == 2
    assert "owner, root (exec -u root)" in run.stderr


def test_a_missing_database_is_an_error_and_is_not_created(tmp_path):
    run = sql(tmp_path / "none.db", "SELECT 1")
    assert run.returncode == 1
    assert "CantOpenException" in run.stderr
    assert not (tmp_path / "none.db").exists()


@pytest.mark.parametrize(
    "args", [[], [""], [";"], ["-- only a comment"], ["/* unclosed"], ["SELECT 1", "SELECT 2"]]
)
def test_anything_but_one_query_prints_the_usage(db, args):
    run = sql(db, *args)
    assert run.returncode == 2
    assert run.stderr.startswith("usage:")


def test_the_fixture_has_the_migrations_columns():
    table = re.search(
        r"create_table :review_findings do.*?^\s+end$", MIGRATION.read_text(), re.M | re.S
    )
    migrated = re.findall(r"^\s+t\.\w+ :(\w+)", table[0], re.M)
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    columns = [row[1] for row in db.execute("PRAGMA table_info(review_findings)")]
    assert columns == ["id", *migrated]


def readme_queries() -> list[str]:
    return re.findall(r"^```sql\n(.*?)^```", README.read_text(), re.M | re.S)


def test_the_readme_gives_four_queries():
    assert len(readme_queries()) == 4


@pytest.mark.parametrize(
    "index, expected",
    [
        (0, "rule_id\toutcome\tn\nignored-file-overwrite\tfixed\t1\nlost-commit\twrong\t1\n"),
        (1, "rule_id\tn\ncrash\t1\nignored-file-overwrite\t1\nlost-commit\t1\n"),
        # a.py's week is the one it was raised in, not its latest row's.
        (2, "week\toutcome\tn\n2026-41\tfixed\t1\n"),
        (
            3,
            "issue\tsubject\tround\tkey\tseverity\tsummary\n"
            "7\tseven\t2\tb.py:9:lost-commit\tcommits\tdrops a commit\\twith a tab\n"
            "8\teight\t1\tc.py:1-4:crash\twork\tloses an edit\n",
        ),
    ],
)
def test_the_readme_queries_run_on_the_fixture(db, index, expected):
    run = sql(db, readme_queries()[index])
    assert (run.returncode, run.stderr) == (0, "")
    assert run.stdout == expected
