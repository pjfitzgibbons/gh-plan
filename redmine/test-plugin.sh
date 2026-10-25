#!/bin/sh
# Inside the Dockerfile's test stage, with the plugin mounted at plugins/<name>: migrates it, rolls
# it back to VERSION=0 and checks that every table it made is gone, migrates it again, then runs
# its tests.
set -eu
name="$1"
tables() {
  bin/rails runner 'puts ActiveRecord::Base.connection.tables.sort' > "$1"
}
tables /tmp/tables-before
bin/rake redmine:plugins:migrate NAME="$name"
bin/rake redmine:plugins:migrate NAME="$name" VERSION=0
tables /tmp/tables-after
left="$(comm -13 /tmp/tables-before /tmp/tables-after | tr '\n' ' ')"
if [ -n "$left" ]; then
  echo "test-plugin: VERSION=0 left $left" >&2
  exit 1
fi
echo "test-plugin: VERSION=0 left none of ${name}'s tables"
bin/rake redmine:plugins:migrate NAME="$name"
exec bin/rake redmine:plugins:test NAME="$name"
