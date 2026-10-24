# syntax=docker/dockerfile:1.7
# Redmine with the Opale theme (setup.rb selects it). The theme's release is pinned by version and
# checksum, and its assets are compiled here, so a recreated container keeps it.
# An exact tag, the one place Redmine's version lives. Dependabot bumps patches; README, Upgrading,
# has the checks a new tag needs, the lane board's among them.
FROM redmine:7.0.2

ADD --checksum=sha256:5e43d5cdd30cabe790e7702c9add093c8448bfe87862dc32490daf3dd76552c9 \
    https://github.com/gagnieray/opale/releases/download/1.7.2/opale-1.7.2.zip /tmp/opale.zip
RUN apt-get update \
 && apt-get install -y --no-install-recommends unzip \
 && unzip -q /tmp/opale.zip -d /tmp/opale \
 && mv /tmp/opale/opale-1.7.2 themes/opale \
 && rm -rf /tmp/opale /tmp/opale.zip \
 && apt-get purge -y unzip \
 && rm -rf /var/lib/apt/lists/*
# Rails loads a database configuration to compile assets; the entrypoint writes the real one at start.
RUN printf 'production:\n  adapter: sqlite3\n  database: /tmp/assets.db\n' > config/database.yml \
 && SECRET_KEY_BASE_DUMMY=1 RAILS_ENV=production bin/rails assets:precompile \
 && rm -f config/database.yml /tmp/assets.db \
 && chown -R redmine:redmine themes/opale public/assets
