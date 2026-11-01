# Imports, once, every item in one repo's .tracker/ into its Redmine project (ADR 0011,
# Consequences). Run in the container, from the gh-plan checkout:
#   gh issue list -R pjfitzgibbons/<repo> --state all --limit 200 \
#     --json number,title,body,closedAt,labels > .tmp/<project>-issues.json
#   docker compose -f redmine/compose.yml exec -T -u redmine redmine \
#     bin/rails runner /setup/import.rb <project> [--dry-run | --check] < .tmp/<project>-issues.json
# An overlay repo's items keep their titles and bodies in its GitHub issues, and the container has
# no gh, so they come in on stdin; gh-plan's items hold their own and read nothing from stdin.
#
# It reads the repo through its read-only .git mount (compose.yml) at the ref SOURCES names.
# An issue is found by its Old ID, so a second run makes nothing new; an issue that exists is left
# as it is, and only a missing parent or relation is added to it. It runs in one transaction, so a
# run that stops halfway leaves nothing behind. --dry-run does the whole import, prints it and
# rolls it back. --check writes nothing: it compares each item with its issue, field by field.
# Needs setup.rb's Old ID and Ordinal fields. It reads no credential.

require 'json'
require 'open3'
require 'yaml'

# Each importable project: its .git mount, the ref its .tracker/ is read at, and its layout.
# :items is gh-plan's Markdown items; :overlay is a .yml per GitHub issue, holding only the fields
# GitHub has no place for. An empty ref reads the index: `git show :<path>`. gh-plan's and
# tempotag's .tracker/ are gone since their import; their entries record where it was read.
SOURCES = {
  'gh-plan' => { git: '/repos/gh-plan.git', ref: 'origin/main', layout: :items },
  'tempotag' => { git: '/repos/tempotag.git', ref: '', layout: :overlay, kinds: {} },
  # The tracker is on this branch only; it has not reached main.
  'ms-security-docs' => { git: '/repos/ms-security-docs.git', ref: 'act-0/tracking-and-agents', layout: :overlay,
                          kinds: { 'kind:defect' => 'Bug', 'kind:config' => 'Support', 'kind:writing' => 'Feature' } }
}.freeze
# The days that start_date and due_date fall on.
ZONE = ActiveSupport::TimeZone['America/Los_Angeles']
STATUSES = { 'Todo' => 'New', 'In Progress' => 'In Progress', 'Done' => 'Closed', 'Icebox' => 'Icebox' }.freeze
TRACKERS = { 'feat' => 'Feature', 'bug' => 'Bug', 'chore' => 'Support' }.freeze
# An overlay item has no type; its tracker comes from the issue's kind: label, or this without one.
UNTYPED = 'Feature'.freeze
PRIORITIES = { 'P0' => 'Urgent', 'P1' => 'High', 'P2' => 'Normal', 'P3' => 'Low' }.freeze
ITEM_KEYS = %w[title type status estimate priority ordinal lane milestone labels parent blocked_by
               started baseline].freeze
OVERLAY_KEYS = %w[issue node_id repo estimate priority status lane milestone].freeze

PROJECT, MODE = ARGV
SOURCE = SOURCES.fetch(PROJECT) { abort "import: name the project to import, one of #{SOURCES.keys.join(', ')}" }
               .merge(project: PROJECT).freeze
abort "import: unknown argument #{MODE}; use --dry-run or --check" unless [nil, '--dry-run', '--check'].include?(MODE)

def git(repo, *args)
  out, status = Open3.capture2('git', "--git-dir=#{repo[:git]}", *args)
  abort "import: git #{args.join(' ')} failed in #{repo[:git]}" unless status.success?
  out
end

def show(repo, path) = git(repo, 'show', "#{repo[:ref]}:#{path}")

def paths(repo, *dirs)
  listing = repo[:ref].empty? ? git(repo, 'ls-files', *dirs) : git(repo, 'ls-tree', '-r', '--name-only', repo[:ref], *dirs)
  listing.lines(chomp: true)
end

def map(table, value, what, id)
  table.fetch(value) { abort "import: #{id} has #{what} #{value.inspect}, which has no mapping" }
end

def day(time) = time&.in_time_zone(ZONE)&.to_date

# A Markdown item: YAML front matter between two --- lines, then the body.
def front_matter(text, path)
  _, yaml, body = text.split(/^---\n/, 3)
  abort "import: #{path} has no front matter" unless body

  [YAML.safe_load(yaml, permitted_classes: [Date, Time]), body.strip]
end

# The first commit on the ref whose copy of the item reads Done: for a merged item, its squash-merge.
def finished(repo, id)
  stamp = git(repo, 'log', repo[:ref], '--reverse', '--format=%cI', '-G^status: Done', '--',
              ".tracker/items/#{id}.md", ".tracker/archive/#{id}.md").lines.first
  abort "import: #{id} is Done, but no commit on #{repo[:ref]} marks it Done" unless stamp

  Time.iso8601(stamp.strip)
end

# Each item as the issue it becomes: the values the import writes and the check compares.
def gh_plan_items(repo)
  versions = paths(repo, '.tracker/milestones').to_h do |path|
    [File.basename(path, '.md'), front_matter(show(repo, path), path).first.fetch('title')]
  end
  paths(repo, '.tracker/items', '.tracker/archive').select { |p| p.end_with?('.md') }.map do |path|
    id = File.basename(path, '.md')
    fields, body = front_matter(show(repo, path), path)
    unknown = fields.keys - ITEM_KEYS
    abort "import: #{id} has fields with no place in Redmine: #{unknown.join(', ')}" unless unknown.empty?
    abort "import: #{id} has labels, which have no place in Redmine" unless fields.fetch('labels', []).empty?

    finish = finished(repo, id) if fields['status'] == 'Done'
    baseline = fields['baseline'] || {}
    {
      old_id: id, project: repo[:project], subject: fields.fetch('title'), description: body,
      tracker: map(TRACKERS, fields.fetch('type'), 'type', id),
      status: map(STATUSES, fields.fetch('status', 'Icebox'), 'status', id),
      priority: map(PRIORITIES, fields.fetch('priority', 'P2'), 'priority', id),
      points: fields['estimate']&.to_s, lane: fields['lane'], ordinal: fields['ordinal']&.to_s,
      version: fields['milestone'] && versions.fetch(fields['milestone']),
      start_date: day(fields['started']), due_date: day(finish), closed_on: finish,
      baseline_start: baseline['start'], baseline_finish: baseline['finish'],
      parent: fields['parent'],
      blockers: fields.fetch('blocked_by', []).map { |entry| entry.partition(' - ').first }.sort
    }
  end
end

# The tracker an issue's kind: label gives, or UNTYPED. Two kinds on one issue stop the import, and
# so does an issue with no labels field for a project with kinds: stdin from a gh issue list without
# --json labels would make every item UNTYPED, and --check, reading the same stdin, would agree.
def tracker_of(issue, kinds, id)
  trackers = labels_of(issue, kinds, id).filter_map { |label| kinds[label.fetch('name')] }.uniq
  abort "import: #{id} has labels for #{trackers.join(' and ')}" if trackers.size > 1

  trackers.first || UNTYPED
end

def labels_of(issue, kinds, id)
  return issue.fetch('labels', []) if kinds.empty?

  issue.fetch('labels') { abort "import: #{id} has no labels on stdin; add labels to gh issue list's --json" }
end

def overlay_items(repo, issues)
  versions = paths(repo, '.tracker/milestones').to_h do |path|
    fields = YAML.safe_load(show(repo, path))
    [fields.fetch('id'), fields.fetch('title')]
  end
  paths(repo, '.tracker/items').select { |p| p.end_with?('.yml') }.map do |path|
    fields = YAML.safe_load(show(repo, path))
    id = "#{fields.fetch('repo')}##{fields.fetch('issue')}"
    unknown = fields.keys - OVERLAY_KEYS
    abort "import: #{id} has fields with no place in Redmine: #{unknown.join(', ')}" unless unknown.empty?
    issue = issues.fetch(fields['issue']) { abort "import: #{id} is not in the GitHub issues on stdin" }

    finish = Time.iso8601(issue.fetch('closedAt')) if fields['status'] == 'Done'
    {
      old_id: id, project: repo[:project], subject: issue.fetch('title'),
      description: issue.fetch('body').strip, tracker: tracker_of(issue, repo[:kinds], id),
      status: map(STATUSES, fields.fetch('status'), 'status', id),
      priority: map(PRIORITIES, fields.fetch('priority', 'P2'), 'priority', id),
      points: fields['estimate']&.to_s, lane: fields['lane'], ordinal: nil,
      version: fields['milestone'] && versions.fetch(fields['milestone']),
      start_date: nil, due_date: day(finish), closed_on: finish,
      baseline_start: nil, baseline_finish: nil, parent: nil, blockers: []
    }
  end
end

def github_issues
  text = $stdin.read
  abort 'import: no GitHub issues on stdin; see the top of import.rb' if text.strip.empty?

  JSON.parse(text).to_h { |issue| [issue.fetch('number'), issue] }
end

FIELDS = %w[Old\ ID Points Lane Ordinal Baseline\ start Baseline\ finish].to_h do |name|
  [name, IssueCustomField.find_by(name: name) || abort("import: no #{name} field; run setup.rb first")]
end.freeze
CUSTOM = { points: 'Points', lane: 'Lane', ordinal: 'Ordinal', baseline_start: 'Baseline start',
           baseline_finish: 'Baseline finish' }.freeze

def find(old_id)
  found = Issue.joins(:custom_values)
               .where(custom_values: { custom_field_id: FIELDS['Old ID'].id, value: old_id }).to_a
  abort "import: #{found.size} issues have Old ID #{old_id}" if found.size > 1

  found.first
end

def version(item)
  Version.find_by(project: Project.find_by!(identifier: 'plan'), name: item[:version]) ||
    abort("import: #{item[:old_id]} is in milestone #{item[:version]}, which has no version; run setup.rb")
end

def create(item, author)
  issue = Issue.new(
    project: Project.find_by!(identifier: item[:project]), author: author,
    tracker: Tracker.find_by!(name: item[:tracker]), status: IssueStatus.find_by!(name: item[:status]),
    priority: IssuePriority.find_by!(name: item[:priority]),
    fixed_version: item[:version] && version(item),
    subject: item[:subject], description: item[:description],
    start_date: item[:start_date], due_date: item[:due_date]
  )
  values = { FIELDS['Old ID'].id.to_s => item[:old_id] }
  CUSTOM.each { |key, name| values[FIELDS[name].id.to_s] = item[key].to_s unless item[key].nil? }
  issue.custom_field_values = values
  issue.save!
  return issue unless item[:closed_on]

  # Redmine stamps closed_on with the save; the item's own finish replaces it, with no journal.
  # update_columns changes nothing on an issue just made (it returns false), so this is update_all.
  Issue.where(id: issue.id).update_all(closed_on: item[:closed_on])
  issue.reload
end

def import(items, author)
  made = 0
  issues = items.to_h do |item|
    issue = find(item[:old_id])
    if issue
      puts "exists #{item[:old_id]} as ##{issue.id}"
    else
      issue = create(item, author)
      made += 1
      puts "made ##{issue.id} #{item[:old_id]} (#{item[:project]}, #{item[:status]}): #{item[:subject]}"
    end
    [item[:old_id], issue]
  end
  lookup = ->(id, of) { issues.fetch(id) { abort "import: #{of} names #{id}, which is not an item" } }
  items.each do |item|
    issue = issues[item[:old_id]]
    if item[:parent] && issue.parent_id.nil?
      issue.parent_issue_id = lookup.(item[:parent], item[:old_id]).id
      issue.save!
      made += 1
      puts "made #{item[:parent]} the parent of #{item[:old_id]}"
    end
    item[:blockers].each do |id|
      relation = IssueRelation.find_or_initialize_by(issue_from: lookup.(id, item[:old_id]), issue_to: issue,
                                                     relation_type: IssueRelation::TYPE_BLOCKS)
      next unless relation.new_record?

      relation.save!
      made += 1
      puts "made #{id} blocks #{item[:old_id]}"
    end
  end
  made
end

# The issue as the item would describe it, for the check.
def described(issue, old_ids)
  value = ->(name) { issue.custom_field_value(FIELDS[name]).presence }
  {
    old_id: value.('Old ID'), project: issue.project.identifier, subject: issue.subject,
    description: issue.description.to_s, tracker: issue.tracker.name, status: issue.status.name,
    priority: issue.priority.name, points: value.('Points'), lane: value.('Lane'), ordinal: value.('Ordinal'),
    version: issue.fixed_version&.name, start_date: issue.start_date, due_date: issue.due_date,
    closed_on: issue.closed? ? issue.closed_on : nil,
    baseline_start: value.('Baseline start')&.to_date, baseline_finish: value.('Baseline finish')&.to_date,
    parent: issue.parent && old_ids[issue.parent_id],
    blockers: issue.relations_to.select { |r| r.relation_type == IssueRelation::TYPE_BLOCKS }
                   .map { |r| old_ids[r.issue_from_id] }.sort
  }
end

def check(items)
  imported = Issue.joins(:custom_values).where(custom_values: { custom_field_id: FIELDS['Old ID'].id })
                  .where.not(custom_values: { value: [nil, ''] })
  old_ids = imported.includes(:custom_values).to_h { |issue| [issue.id, issue.custom_field_value(FIELDS['Old ID'])] }
  differences = []
  items.group_by { |item| item[:project] }.each do |project, group|
    count = imported.joins(:project).where(projects: { identifier: project }).count
    puts "#{project}: #{group.size} items, #{count} imported issues"
    differences << "#{project} has #{count} imported issues for #{group.size} items" if count != group.size
  end
  items.each do |item|
    issue = find(item[:old_id])
    next differences << "#{item[:old_id]} has no issue" unless issue

    actual = described(issue, old_ids)
    item.each do |key, expected|
      got = actual[key]
      same = case key
             when :closed_on then expected&.to_i == got&.to_i
             # Redmine stores every line ending as \r\n.
             when :description then expected.gsub("\r\n", "\n") == got.gsub("\r\n", "\n")
             else expected == got
             end
      differences << "#{item[:old_id]} (##{issue.id}) #{key}: item #{expected.inspect}, issue #{got.inspect}" unless same
    end
  end
  differences.each { |line| puts line }
  puts differences.empty? ? "no differences in #{items.size} items" : "#{differences.size} differences"
  differences.empty?
end

items = SOURCE[:layout] == :items ? gh_plan_items(SOURCE) : overlay_items(SOURCE, github_issues)
author = User.active.where(admin: true).order(:id).first || abort('import: no active administrator')

if MODE == '--check'
  exit(check(items) ? 0 : 1)
end

User.current = author
# Mail goes out from jobs that run after the transaction, so it is off for the whole run.
ActionMailer::Base.perform_deliveries = false
made = nil
ActiveRecord::Base.transaction do
  made = import(items, author)
  raise ActiveRecord::Rollback if MODE == '--dry-run'
end
puts MODE == '--dry-run' ? "dry run: #{made} to make, nothing written" : "#{made} made, #{items.size} items"
