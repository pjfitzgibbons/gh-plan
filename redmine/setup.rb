# Makes the Redmine of ADR 0011, or brings one back to it. Run in the container:
#   docker compose -f redmine/compose.yml exec -u redmine redmine bin/rails runner /setup/setup.rb
# It makes what is missing, corrects what it owns and leaves the rest alone, so a second run
# prints "nothing to change". It runs in one transaction, so a failed run leaves nothing behind.
# It needs no API key: rails runner works on the database directly, because the REST API
# cannot make custom fields, statuses, workflows, settings or repositories. The one credential it
# takes is the repository web service's key, from SYS_API_KEY, which the Keychain holds. -e names
# it without its value, so the key stays out of the process list:
#   SYS_API_KEY="$(security find-generic-password -s redmine-sys-api -w)" \
#     docker compose -f redmine/compose.yml exec -e SYS_API_KEY -u redmine \
#     redmine bin/rails runner /setup/setup.rb
# Without it, the service is left as it is.

PARENT = { identifier: 'plan', name: 'Plan' }.freeze
# identifier => name. Each repo's .git is mounted at /repos/<identifier>.git (compose.yml).
REPOS = { 'gh-plan' => 'GH Plan', 'tempotag' => 'tempotag' }.freeze
# The milestones of each repo's .tracker/milestones/, as versions in the parent project:
# name => description. Every one still has open items, so each is made open.
VERSIONS = {
  'tempo' => "tempotag's milestone: all its issues closed.",
  'Redmine migration' => 'Redmine is the tracker (ADR 0011).',
  'Bootstrap' => "gh-plan's own work is planned in .tracker/items/ and the tool reads those files.",
  'Phase 0: repo setup' => 'The repo builds, lints and documents itself.',
  'Phase 1: CLI only' => 'The CLI answers what next in the terminal over this repo\'s own tracker.',
  'Phase 2: HTML views' => 'plan render and plan serve show the lane board, the timeline and its arrows.',
  'Phase 3: write UI' => 'Drag to change status and lane.'
}.freeze
POINTS = %w[1 2 3 5 8 13].freeze
# tempotag's one lane is core.
LANES = %w[cli engine github infra model web core].freeze
BASELINES = ['Baseline start', 'Baseline finish'].freeze
ICEBOX = 'Icebox'.freeze
# The administrator takes every role in workflow checks, and a field is read-only only when it is
# read-only for each of them. This role has no members and no read-only rule, so the baselines
# stay editable by the administrator alone.
BASELINE_EDITOR = 'Baseline editor'.freeze
MODULES = %w[issue_tracking repository gantt calendar lane_board].freeze
# The theme the Dockerfile installs.
THEME = 'opale'.freeze
NEXT_UP = 'Next up'.freeze

@changes = []

def note(what)
  @changes << what
  puts what
end

def save(record, what)
  return record unless record.new_record? || record.changed?

  note "#{record.new_record? ? 'made' : 'changed'} #{what}"
  record.save!
  record
end

def setting(name, value)
  return if Setting.send(name) == value

  Setting.send("#{name}=", value)
  note "set #{name}"
end

def all_trackers(field)
  missing = Tracker.all.to_a - field.trackers.to_a
  return if missing.empty?

  field.trackers << missing
  note "gave #{field.name} to #{missing.map(&:name).join(', ')}"
end

def issue_field(name, format, values = nil, searchable: false, formatted: false)
  field = IssueCustomField.find_or_initialize_by(name: name)
  field.field_format = format if field.new_record?
  field.possible_values = values if values
  field.searchable = searchable
  field.text_formatting = 'full' if formatted
  field.multiple = false
  field.is_for_all = true
  field.is_filter = true
  save(field, "custom field #{name}")
  all_trackers(field)
  field
end

def project(identifier, name, parent)
  project = Project.find_or_initialize_by(identifier: identifier)
  project.name = name if project.new_record?
  project.parent_id = parent&.id
  project.is_public = false
  save(project, "project #{identifier}")
  missing = MODULES - project.enabled_module_names
  unless missing.empty?
    project.enabled_module_names = project.enabled_module_names | missing
    note "enabled #{missing.join(', ')} in #{identifier}"
  end
  missing = Tracker.all.to_a - project.trackers.to_a
  unless missing.empty?
    project.trackers << missing
    note "gave #{identifier} the trackers #{missing.map(&:name).join(', ')}"
  end
  project
end

abort 'setup: the lane_board plugin is not loaded; check the mount in compose.yml' unless
  Redmine::Plugin.installed?(:lane_board)

ActiveRecord::Base.transaction do
  if Redmine::DefaultData::Loader.no_data?
    Redmine::DefaultData::Loader.load('en')
    note 'loaded the default trackers, statuses, roles and workflows'
  end

  setting :rest_api_enabled, '1'
  setting :cross_project_issue_relations, '1'
  setting :commit_cross_project_ref, '1'
  setting :commit_ref_keywords, 'refs'
  closed = IssueStatus.find_by!(name: 'Closed')
  setting :commit_update_keywords, [{ 'keywords' => 'fixes', 'status_id' => closed.id.to_s, 'done_ratio' => '' }]
  # The repository web service, so a merge can ask for its commits to be fetched. Never on
  # without a key: an empty key would let any request through.
  if ENV['SYS_API_KEY'].present?
    setting :sys_api_key, ENV['SYS_API_KEY']
    setting :sys_api_enabled, '1'
  end

  # Icebox: open, and in each workflow a copy of New, so it opens no route a role lacks. It gets
  # New's transitions out, with their author and assignee conditions, each status's transition
  # into New, and New to Icebox and back wherever New has a transition out.
  icebox = save(IssueStatus.find_or_initialize_by(name: ICEBOX).tap { |s| s.is_closed = false },
                "status #{ICEBOX}")
  new = IssueStatus.find_by!(name: 'New')
  made = 0
  copies = WorkflowTransition.where(old_status_id: new.id).where.not(new_status_id: icebox.id).map do |t|
    [t, icebox.id, t.new_status_id]
  end
  copies += WorkflowTransition.where(new_status_id: new.id).where.not(old_status_id: [0, icebox.id]).map do |t|
    [t, t.old_status_id, icebox.id]
  end
  copies += WorkflowTransition.where(old_status_id: new.id).where.not(new_status_id: icebox.id).flat_map do |t|
    [[t, new.id, icebox.id], [t, icebox.id, new.id]]
  end
  copies.each do |t, from, to|
    rule = WorkflowTransition.find_or_create_by!(role_id: t.role_id, tracker_id: t.tracker_id,
                                                 old_status_id: from, new_status_id: to,
                                                 author: t.author, assignee: t.assignee)
    made += 1 if rule.previously_new_record?
  end
  note "made #{made} workflow transitions to and from #{ICEBOX}" if made.positive?

  points = issue_field('Points', 'list', POINTS)
  lane = issue_field('Lane', 'list', LANES)
  baselines = BASELINES.map { |name| issue_field(name, 'date') }
  # From the import of each repo's .tracker/ (import.rb): the item's ID, and ready.yml's order.
  issue_field('Old ID', 'string', searchable: true)
  ordinal = issue_field('Ordinal', 'int')
  # Each merged pull request as a Markdown link, so they head the issue: a link field holds only one.
  pull_requests = issue_field('Pull requests', 'string', searchable: true, formatted: true)

  editor = Role.find_or_initialize_by(name: BASELINE_EDITOR)
  editor.permissions = %i[view_issues edit_issues] if editor.new_record?
  editor.assignable = false
  save(editor, "role #{BASELINE_EDITOR}")

  made = 0
  (Role.all.to_a - [editor]).each do |role|
    Tracker.all.each do |tracker|
      IssueStatus.all.each do |status|
        baselines.each do |field|
          rule = WorkflowPermission.find_or_initialize_by(role_id: role.id, tracker_id: tracker.id,
                                                          old_status_id: status.id, field_name: field.id.to_s)
          next if rule.rule == 'readonly'

          rule.rule = 'readonly'
          rule.save!
          made += 1
        end
      end
    end
  end
  note "made #{made} read-only rules for the baselines" if made.positive?

  Role.givable.where.not(id: editor.id).each do |role|
    next if role.has_permission?(:view_lane_board)

    role.add_permission!(:view_lane_board)
    note "gave #{role.name} view_lane_board"
  end

  parent = project(PARENT[:identifier], PARENT[:name], nil)
  REPOS.each do |identifier, name|
    child = project(identifier, name, parent)
    repository = child.repository || Repository::Git.new(project: child, is_default: true)
    repository.url = "/repos/#{identifier}.git"
    save(repository, "repository of #{identifier}")
  end

  VERSIONS.each do |name, description|
    version = Version.find_or_initialize_by(project_id: parent.id, name: name)
    version.description = description if version.new_record?
    version.sharing = 'tree'
    save(version, "version #{name}")
  end

  setting :ui_theme, THEME
  setting :issue_list_default_columns, %W[tracker status priority subject assigned_to updated_on cf_#{pull_requests.id}]

  # Open issues in Ordinal order, Icebox left out: what to pick up next. Public, so it belongs to
  # no one person; its owner is whoever made it, or the first administrator.
  query = IssueQuery.find_or_initialize_by(project_id: parent.id, name: NEXT_UP)
  query.user ||= User.active.where(admin: true).order(:id).first
  query.visibility = Query::VISIBILITY_PUBLIC
  query.filters = { 'status_id' => { operator: '=', values: IssueStatus.where(is_closed: false)
                                                                       .where.not(name: ICEBOX).order(:id).map { |s| s.id.to_s } } }
  query.column_names = [:project, :tracker, :status, :subject, :"cf_#{points.id}", :"cf_#{lane.id}",
                        :"cf_#{ordinal.id}", :assigned_to, :"cf_#{pull_requests.id}"]
  query.sort_criteria = [["cf_#{ordinal.id}", 'asc'], %w[id asc]]
  save(query, "query #{NEXT_UP}")

  setting :welcome_text, <<~MARKDOWN
    ## Plan

    - [Plan project](/projects/#{parent.identifier}): #{REPOS.keys.join(' and ')}
    - [#{NEXT_UP}](/projects/#{parent.identifier}/issues?query_id=#{query.id}): open issues, Icebox left out, in Ordinal order
  MARKDOWN
end

puts @changes.empty? ? 'nothing to change' : "#{@changes.size} changes"
