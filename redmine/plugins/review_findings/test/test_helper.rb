require File.expand_path('../../../test/test_helper', __dir__)

module ReviewFindingsTestHelper
  # Unit and integration tests each keep their own fixture paths, so each class adds the plugin's.
  def self.included(test_class)
    test_class.fixture_paths += [File.expand_path('fixtures', __dir__)]
  end

  # Project 1 gets the module; Manager (jsmith) may view and manage, Developer (dlopper) only view.
  def enable_review_findings
    Project.find(1).enable_module!(:review_findings)
    Role.find(1).add_permission!(:view_review_findings, :manage_review_findings)
    Role.find(2).add_permission!(:view_review_findings)
  end

  # A row raising finding `k.n`, or with `source` and `outcome`, checking or answering it. `place`
  # is `file:lines:rule`, where the finding is at `round`.
  def finding_row(key, place, round, **fields)
    raised, ordinal = key.split('.').map { |part| Integer(part) }
    file, lines, rule = place.split(':')
    first, last = lines.split('-').map(&:to_i)
    { 'round' => round, 'source' => 'review', 'file' => file, 'line_start' => first,
      'line_end' => last || first, 'rule_id' => rule, 'raised_round' => raised, 'ordinal' => ordinal,
      'outcome' => 'new', 'tag' => 'Important',
      'severity' => 'files', 'visibility' => 'silent', 'summary' => 'A copy loses a file' }
      .merge(fields.stringify_keys)
  end

  def answer_row(key, place, round, outcome = 'fixed', **fields)
    finding_row(key, place, round, source: 'outcomes', outcome: outcome).except('tag', 'summary')
                                                                       .merge(fields.stringify_keys)
  end

  def round_row(round, **fields)
    { 'round' => round, 'commit' => round.to_s(16) * 40, 'ci' => 'success', 'repo' => 'example/app',
      'pr_number' => 7 }.merge(fields.stringify_keys)
  end
end
