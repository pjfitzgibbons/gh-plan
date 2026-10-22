# gh-plan

GH Plan plans work in [Redmine](https://www.redmine.org/), outside the repos it plans
([ADR 0011](docs/adr/0011-redmine-is-the-tracker.md)). Each repo is a Redmine project under one
parent project, so a milestone, a dependency or a lane can span repos. GitHub Issues isn't used.
Pull requests are the one GitHub integration.

This repo is the setup around Redmine:

- [`redmine/`](redmine/README.md): Redmine in Docker on localhost, its projects, fields and
  workflow, the lane board plugin, and the nightly backup.
- [`vscode/`](vscode/README.md): the VS Code extension. Its Next up panel starts, ships and
  finishes each issue.

The design is in [`docs/architecture.md`](docs/architecture.md).
