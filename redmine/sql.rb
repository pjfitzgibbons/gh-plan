# Runs one SQL query on Redmine's database, read-only, and prints the rows as tab-separated text,
# the column names first (design 0005 §3.6). Run in the container:
#   docker compose -f redmine/compose.yml exec -T -u redmine redmine ruby /setup/sql.rb "<SELECT …>"
# The connection is opened read-only, so a write raises SQLite3::ReadOnlyException and changes
# nothing: a mistyped query can't change the tracker. VACUUM is refused before it runs, since
# VACUUM INTO writes a copy of the database even on a read-only connection. A second statement
# after the first is refused, not ignored. NULL prints as an empty field; a tab, newline, carriage
# return or backslash in a value prints as \t, \n, \r or \\, and any other control character as
# \x and two hex digits, so each row is one line; bytes that aren't UTF-8 print as U+FFFD. SQL_DB
# names another database, for the tests.

require 'sqlite3'

DB = ENV.fetch('SQL_DB', '/usr/src/redmine/sqlite/redmine.db')
ESCAPES = { "\\" => '\\\\', "\t" => '\t', "\n" => '\n', "\r" => '\r' }.freeze
# The C0 controls, DEL and the C1 controls, which a terminal could act on or break a row at.
CONTROLS = /[\\\u0000-\u001f\u007f-\u009f]/
# Whitespace, comments and semicolons, which may come before the query and after it.
FILLER = %r{\A(?:\s+|;|--[^\n]*(?:\n|\z)|/\*.*?\*/)*}m
VACUUM = /\AVACUUM\b/i
USAGE = 'usage: sql.rb "<SELECT …>"'.freeze

def field(value)
  return '' if value.nil?

  value.to_s.dup.force_encoding(Encoding::UTF_8).scrub
       .gsub(CONTROLS) { |c| ESCAPES.fetch(c) { format('\\x%02x', c.ord) } }
end

def refuse(message)
  warn message
  exit 2
end

query = ARGV[0].to_s.sub(FILLER, '')
refuse USAGE unless ARGV.length == 1 && !query.empty?
if query.match?(VACUUM)
  refuse 'sql.rb: VACUUM is refused: VACUUM INTO writes even on a read-only connection'
end

begin
  db = SQLite3::Database.new(DB, readonly: true)
  stmt = db.prepare(query)
  # SQLite gives a closed statement for one that is only a comment, such as an unclosed /*.
  refuse USAGE if stmt.closed?
  rest = stmt.remainder.sub(FILLER, '')
  refuse 'sql.rb: one statement only; this query has more after the first' unless rest.empty?
  # Every row is read and formatted before any prints, so a query that fails part-way prints
  # only the error.
  out = [stmt.columns, *stmt.execute.to_a].map { |row| row.map { |v| field(v) }.join("\t") }
  puts out
rescue SQLite3::Exception => e
  warn "sql.rb: #{e.class}: #{e.message}"
  exit 1
ensure
  stmt.close unless stmt.nil? || stmt.closed?
  db&.close
end
