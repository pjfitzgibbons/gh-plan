require File.expand_path('../test_helper', __dir__)

class ReviewFindingsApiTest < Redmine::ApiTest::Base
  include ReviewFindingsTestHelper
  fixtures :projects, :users, :email_addresses, :roles, :members, :member_roles, :issues,
           :issue_statuses, :trackers, :projects_trackers, :enabled_modules, :enumerations,
           :journals, :watchers, :review_findings, :review_rounds

  OPEN = 'lib/sync.rb:12-14:ignored-file-overwrite'.freeze
  UNCHECKED = 'lib/sync.rb:60-61:ignored-file-overwrite'.freeze

  def setup
    super
    enable_review_findings
  end

  def post_ledger(rows = nil, round: nil, user: 'jsmith', issue: 1)
    body = {}
    body['review_round'] = round if round
    body['review_findings'] = rows unless rows.nil?
    post "/issues/#{issue}/review_findings.json",
         params: body.to_json, headers: { 'CONTENT_TYPE' => 'application/json' }.merge(signed_in(user))
  end

  # jsmith and admin have their login as password in Redmine's fixtures; dlopper has foo.
  def signed_in(user)
    credentials(user, user == 'dlopper' ? 'foo' : user)
  end

  def json
    JSON.parse(response.body)
  end

  def ledger
    get '/issues/1/review_findings.json', headers: signed_in('dlopper')
    assert_response :success
    json
  end

  test 'GET returns the ledger and what it adds up to' do
    body = ledger
    assert_equal 2, body['last_round']
    assert_equal [1, 2], body['review_rounds'].map { |r| r['round'] }
    assert_equal ['example/app', 7], body['review_rounds'].first.values_at('repo', 'pr_number')
    assert_equal 8, body['review_findings'].size
    statuses = body['findings'].to_h { |f| [f['key'], f['status']] }
    assert_equal 'open', statuses[OPEN]
    assert_equal 'fixed', statuses['lib/sync.rb:40:forced-update']
    assert_equal 'fixed, unchecked', statuses[UNCHECKED]
    assert_equal 'intentional', statuses['lib/remote.rb:7:repo-rename']
    assert_equal [1, 1], body.values_at('open_to_fix', 'fixed_unchecked')
    assert_equal({ 'forced-update' => 1, 'ignored-file-overwrite' => 2, 'repo-rename' => 1 }, body['rules'])
  end

  test 'GET before any review has last_round 0' do
    get '/issues/3/review_findings.json', headers: signed_in('jsmith')
    assert_response :success
    assert_equal [0, [], []], json.values_at('last_round', 'review_rounds', 'findings')
  end

  test 'GET needs view_review_findings, and the module' do
    Role.find(2).remove_permission!(:view_review_findings)
    get '/issues/1/review_findings.json', headers: signed_in('dlopper')
    assert_response :forbidden
    Project.find(1).disable_module!(:review_findings)
    get '/issues/1/review_findings.json', headers: signed_in('jsmith')
    assert_response :forbidden
  end

  test 'a POST adds the next round and its rows, and the same POST again adds nothing' do
    rows = [finding_row('lib/b.rb:9:crash', 3, severity: 'nothing', visibility: 'seen'),
            finding_row(UNCHECKED, 3, outcome: 'fixed', tag: nil, summary: nil, body: 'Scans the new path')]
    assert_difference ['ReviewRound.count'], 1 do
      assert_difference 'ReviewFinding.count', 2 do
        post_ledger(rows, round: round_row(3, controls: ['REVIEW.md']))
      end
    end
    assert_response :success
    assert_equal({ 'created' => 2, 'skipped' => 0, 'round' => 'created' }, json)
    assert_equal [3, 2, 0], ledger.values_at('last_round', 'open_to_fix', 'fixed_unchecked')

    assert_no_difference ['ReviewRound.count', 'ReviewFinding.count', 'Journal.count'] do
      post_ledger(rows, round: round_row(3, controls: ['REVIEW.md']))
    end
    assert_response :success
    assert_equal({ 'created' => 0, 'skipped' => 2, 'round' => 'skipped' }, json)
  end

  test 'a clean round is a round with no rows' do
    post_ledger(round: round_row(3, summary: '0 findings.'))
    assert_response :success
    assert_equal 3, ledger['last_round']
  end

  test 'a failed ci is a numbered round too' do
    post_ledger(round: round_row(3, ci: 'failure', ci_summary: 'assess: 2 failed'))
    assert_response :success
    assert_equal 'failure', ReviewRound.find_by(issue_id: 1, round: 3).ci
  end

  test 'a round that is not the next one is refused' do
    [1, 4].each do |k|
      assert_no_difference 'ReviewRound.count' do
        post_ledger(round: round_row(k, commit: 'c' * 40))
      end
      assert_response :unprocessable_content
    end
    assert_match(/round 4 isn't the next one; the last is 2/, json['errors'].first)
  end

  test 'a round already recorded with other values, or a commit already reviewed, is refused' do
    post_ledger(round: round_row(2, ci: 'success'))
    assert_response :unprocessable_content
    assert_match(/round 2 is already recorded with other ci/, json['errors'].first)
    post_ledger(round: round_row(3, commit: '1' * 40))
    assert_response :unprocessable_content
    assert_match(/is already round 1/, json['errors'].first)
  end

  test 'a row whose event is there with other values is refused, and nothing in the request is saved' do
    assert_no_difference ['ReviewRound.count', 'ReviewFinding.count'] do
      post_ledger([finding_row('lib/c.rb:1:crash', 3, severity: 'nothing', visibility: 'seen'),
                   answer_row(UNCHECKED, 2, 'wrong', justification: 'Changed my mind')],
                  round: round_row(3))
    end
    assert_response :unprocessable_content
    assert_equal ["row 2: #{UNCHECKED} is already in round 2 from outcomes, with other outcome, justification"],
                 json['errors']
  end

  test 'a later answer goes in a later round' do
    post_ledger([answer_row(UNCHECKED, 3, 'wrong', justification: 'The path is never renamed')], round: round_row(3))
    assert_response :success
    assert_equal 'wrong', ledger['findings'].find { |f| f['key'] == UNCHECKED }['status']
  end

  test 'an answer or a check needs a round that raised the finding' do
    post_ledger([answer_row('lib/new.rb:1:crash', 2)])
    assert_response :unprocessable_content
    assert_equal ["row 1: lib/new.rb:1:crash: no earlier round raised it"], json['errors']
    post_ledger([finding_row('lib/new.rb:1:crash', 2, outcome: 'fixed')])
    assert_response :unprocessable_content
    assert_match(/no earlier round raised it/, json['errors'].first)
  end

  test 'a row in a round Redmine has not recorded is refused' do
    post_ledger([finding_row('lib/c.rb:1:crash', 5)])
    assert_response :unprocessable_content
    assert_match(/round 5 isn't recorded/, json['errors'].first)
  end

  test 'a missed finding is a round 0 review row, answered like any other' do
    post_ledger([finding_row('lib/d.rb:4-6:ignored-file-overwrite', 0, tag: 'Important'),
                 answer_row('lib/d.rb:4-6:ignored-file-overwrite', 0)])
    assert_response :success
    assert_equal 'fixed, unchecked', ledger['findings'].find { |f| f['key'] == 'lib/d.rb:4-6:ignored-file-overwrite' }['status']
  end

  test 'an answer is confirmed by the sender' do
    post_ledger([answer_row(OPEN, 1)])
    assert_response :success
    assert_equal User.find_by(login: 'jsmith'), ReviewFinding.find_by(file: 'lib/sync.rb', line_start: 12, source: 'outcomes').confirmed_by
  end

  test 'an unknown field, a bad value, or a row that is not an object is refused' do
    post_ledger([finding_row('lib/c.rb:1:crash', 2, fingerprint: 'x')])
    assert_match(/can't send fingerprint/, json['errors'].first)
    post_ledger([finding_row('lib/c.rb:1:crash', 2, severity: 'data')])
    assert_match(/Severity/, json['errors'].first)
    post_ledger(['row'])
    assert_equal ['row 1: is not an object'], json['errors']
    post_ledger(round: round_row(3, repo: 'not a repo'))
    assert_match(/Repo/, json['errors'].first)
    assert_response :unprocessable_content
  end

  test 'a POST without manage_review_findings gets 403' do
    assert_no_difference 'ReviewFinding.count' do
      post_ledger([finding_row('lib/c.rb:1:crash', 2)], user: 'dlopper')
    end
    assert_response :forbidden
  end

  # Another window's send commits the same round first; this one hits a unique index, rolls back
  # and runs again, skipping the rows (§3.3).
  test 'two sends of one round leave one set of rows' do
    rows = [finding_row('lib/e.rb:1:crash', 3), finding_row('lib/e.rb:2:crash', 3)]
    other_window = Module.new do
      define_method(:record) do |issue, sent, user, **round|
        unless @raced
          @raced = true
          super(issue, sent, user, **round)
          raise ActiveRecord::RecordNotUnique, 'UNIQUE constraint failed: review_rounds.issue_id'
        end
        super(issue, sent, user, **round)
      end
    end
    ReviewFinding.singleton_class.prepend(other_window)
    begin
      assert_difference 'ReviewFinding.count', 2 do
        post_ledger(rows, round: round_row(3))
      end
    ensure
      other_window.send(:remove_method, :record)
      ReviewFinding.remove_instance_variable(:@raced) if ReviewFinding.instance_variable_defined?(:@raced)
    end
    assert_response :success
    assert_equal({ 'created' => 0, 'skipped' => 2, 'round' => 'skipped' }, json)
  end

  test 'a send that fails twice answers 500 and saves nothing' do
    ReviewFinding.stubs(:record).raises(ActiveRecord::StatementTimeout, 'database is locked')
    assert_no_difference 'ReviewFinding.count' do
      post_ledger([finding_row('lib/c.rb:1:crash', 2)])
    end
    assert_response :internal_server_error
  end

  test 'rules.json counts real and noise per rule, each key once by its latest answer' do
    post_ledger([answer_row(UNCHECKED, 3, 'wrong', justification: 'Never renamed')], round: round_row(3))
    get '/projects/1/review_findings/rules.json', headers: signed_in('dlopper')
    assert_response :success
    assert_equal [{ 'rule_id' => 'forced-update', 'real' => 1, 'noise' => 0 },
                  { 'rule_id' => 'ignored-file-overwrite', 'real' => 0, 'noise' => 1 },
                  { 'rule_id' => 'repo-rename', 'real' => 1, 'noise' => 0 }], json['rules']
  end

  test 'rules.json counts only the issues the user can see, and needs view_review_findings' do
    Role.find(2).update!(issues_visibility: 'own')
    get '/projects/1/review_findings/rules.json', headers: signed_in('dlopper')
    assert_response :success
    assert_equal [], json['rules']
    Role.find(2).remove_permission!(:view_review_findings)
    get '/projects/1/review_findings/rules.json', headers: signed_in('dlopper')
    assert_response :forbidden
  end

  test 'GET rules/:rule_id counts the keys with the rule, and the answered ones' do
    get '/projects/1/review_findings/rules/ignored-file-overwrite.json', headers: signed_in('dlopper')
    assert_response :success
    assert_equal({ 'rule_id' => 'ignored-file-overwrite', 'findings' => 2, 'confirmed' => 1 }, json)
  end

  def rename_rule(to, user: 'jsmith', from: 'ignored-file-overwrite')
    put "/projects/1/review_findings/rules/#{from}.json",
        params: { 'new_rule_id' => to }.to_json,
        headers: { 'CONTENT_TYPE' => 'application/json' }.merge(signed_in(user))
  end

  def rule_ids
    ReviewFinding.order(:id).pluck(:id, :rule_id)
  end

  test 'a rename moves every row to the new rule, notes each issue once, and a second changes nothing' do
    assert_difference 'Journal.count', 1 do
      rename_rule('file-overwrite')
    end
    assert_response :success
    assert_equal({ 'renamed' => 3, 'noted' => 1 }, json)
    assert_equal 3, ReviewFinding.where(rule_id: 'file-overwrite').count
    note = Journal.order(:id).last
    assert_equal 'John Smith renamed review rule ignored-file-overwrite to file-overwrite on 3 rows.', note.notes
    assert_equal 1, note.journalized_id

    assert_no_difference 'Journal.count' do
      rename_rule('file-overwrite')
    end
    assert_equal({ 'renamed' => 0, 'noted' => 0 }, json)
  end

  test 'a new rule id that is not kebab-case, or is the old one, is refused' do
    before = rule_ids
    ['File-Overwrite', '', nil, 'ignored-file-overwrite'].each do |to|
      rename_rule(to)
      assert_response :unprocessable_content
      assert_match(/new_rule_id/, json['errors'].first)
    end
    assert_equal before, rule_ids
  end

  test 'a rename onto a key another row has renames none' do
    post_ledger([finding_row('lib/sync.rb:12-14:copy-overwrite', 1)])
    assert_response :success
    before = rule_ids
    assert_no_difference 'Journal.count' do
      rename_rule('copy-overwrite')
    end
    assert_response :unprocessable_content
    assert_match(/is already a finding of issue #1/, json['errors'].first)
    assert_equal before, rule_ids
  end

  test 'a rename without manage_review_findings gets 403' do
    rename_rule('file-overwrite', user: 'dlopper')
    assert_response :forbidden
  end

  test 'a row on an issue the user cannot see refuses the whole rename' do
    # Manager sees only its own issues: jsmith wrote issue 1, and issue 2, which has the crash row,
    # is made dlopper's.
    Role.find(1).update!(issues_visibility: 'own')
    Issue.find(2).update_columns(author_id: 3, assigned_to_id: 3)
    Watcher.where(watchable_type: 'Issue', watchable_id: 2).delete_all
    before = rule_ids
    rename_rule('panic', from: 'crash')
    assert_response :unprocessable_content
    assert_match(/1 finding with rule crash is on issues you can't see or manage/, json['errors'].first)
    assert_equal before, rule_ids
  end
end
