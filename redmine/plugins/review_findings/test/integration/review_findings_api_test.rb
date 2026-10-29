require File.expand_path('../test_helper', __dir__)

class ReviewFindingsApiTest < Redmine::ApiTest::Base
  include ReviewFindingsTestHelper
  fixtures :projects, :users, :email_addresses, :roles, :members, :member_roles, :issues,
           :issue_statuses, :trackers, :projects_trackers, :enabled_modules, :enumerations,
           :journals, :review_findings

  def setup
    super
    enable_review_findings
  end

  def put_findings(rows, user: 'jsmith', issue: 1)
    put "/issues/#{issue}/review_findings.json",
        params: { 'review_findings' => rows }.to_json,
        headers: { 'CONTENT_TYPE' => 'application/json' }.merge(signed_in(user))
  end

  # jsmith and admin have their login as password in Redmine's fixtures; dlopper has foo.
  def signed_in(user)
    credentials(user, user == 'dlopper' ? 'foo' : user)
  end

  def json
    JSON.parse(response.body)
  end

  def note_count
    Journal.where(journalized_type: 'Issue', journalized_id: 1).count
  end

  test 'GET lists the issue findings, every round' do
    get '/issues/1/review_findings.json', headers: signed_in('dlopper')
    assert_response :success
    rows = json['review_findings']
    assert_equal ReviewFinding.where(issue_id: 1).count, rows.size
    assert_equal [1, 2, 3], rows.select { |r| r['fingerprint'] == 'finding/v1:0000000000000006' }.map { |r| r['round'] }
  end

  test 'GET needs view_review_findings' do
    Role.find(2).remove_permission!(:view_review_findings)
    get '/issues/1/review_findings.json', headers: signed_in('dlopper')
    assert_response :forbidden
  end

  test 'GET without the module is refused' do
    Project.find(1).disable_module!(:review_findings)
    get '/issues/1/review_findings.json', headers: signed_in('jsmith')
    assert_response :forbidden
  end

  test 'a PUT inserts rows, and the same PUT again changes nothing' do
    rows = [finding_row('finding/v1:00000000000000a1', 4, line: 3, file: 'lib/a.rb'),
            finding_row('finding/v1:00000000000000a2', 4, severity: 'nothing', visibility: nil)]
    assert_difference 'ReviewFinding.count', 2 do
      put_findings(rows)
    end
    assert_response :success
    assert_equal({ 'created' => 2, 'updated' => 0, 'unchanged' => 0 }, json)
    saved = ReviewFinding.where(issue_id: 1, round: 4).order(:fingerprint).map { |f| [f.id, f.updated_on] }

    travel 1.hour do
      assert_no_difference ['ReviewFinding.count', 'Journal.count'] do
        put_findings(rows)
      end
    end
    assert_response :success
    assert_equal({ 'created' => 0, 'updated' => 0, 'unchanged' => 2 }, json)
    assert_equal saved, ReviewFinding.where(issue_id: 1, round: 4).order(:fingerprint).map { |f| [f.id, f.updated_on] }
  end

  test 'a PUT updates an unconfirmed row with the fields it sends, and leaves the rest' do
    row = review_findings(:open_silent)
    put_findings([{ 'fingerprint' => row.fingerprint, 'round' => 1, 'line' => 14 }])
    assert_response :success
    assert_equal({ 'created' => 0, 'updated' => 1, 'unchanged' => 0 }, json)
    row.reload
    assert_equal 14, row.line
    assert_equal 'lib/sync.rb', row.file
    assert_nil row.confirmed_by_id
  end

  test 'a PUT that omits outcome leaves the outcome as it was' do
    row = review_findings(:open_silent)
    put_findings([{ 'fingerprint' => row.fingerprint, 'round' => 1, 'confirm' => true, 'outcome' => 'fixed',
                    'rule_id' => 'ignored-file-overwrite', 'severity' => 'files', 'visibility' => 'silent' }])
    assert_response :success
    assert_equal 'fixed', row.reload.outcome

    put_findings([finding_row(row.fingerprint, 1, summary: 'Another summary')])
    assert_response :success
    assert_equal 'fixed', row.reload.outcome
    assert_equal 'The copy overwrites an ignored file without a word', row.summary
  end

  test 'a round line may not send an outcome or a justification, even null' do
    row = review_findings(:open_silent)
    [{ 'outcome' => 'fixed' }, { 'outcome' => nil }, { 'justification' => nil }].each do |extra|
      put_findings([{ 'fingerprint' => row.fingerprint, 'round' => 1 }.merge(extra)])
      assert_response :unprocessable_content
      assert_match(/a round line can't send/, json['errors'].first)
    end
    assert_nil row.reload.outcome
  end

  test 'an outcome line confirms an unconfirmed row as its user, without a journal note' do
    row = review_findings(:open_silent)
    assert_no_difference 'note_count' do
      put_findings([{ 'fingerprint' => row.fingerprint, 'round' => 1, 'confirm' => true, 'outcome' => 'wrong',
                      'justification' => 'The file is not ignored', 'rule_id' => 'ignored-file-overwrite',
                      'severity' => 'nothing', 'visibility' => 'seen' }])
    end
    assert_response :success
    row.reload
    assert_equal %w[wrong nothing seen], [row.outcome, row.severity, row.visibility]
    assert_equal 2, row.confirmed_by_id
    assert_not_nil row.confirmed_on
  end

  test 'a round line leaves a confirmed row as it is' do
    row = review_findings(:intentional)
    put_findings([finding_row(row.fingerprint, 1, severity: 'work', visibility: 'silent', summary: 'Changed')])
    assert_response :success
    assert_equal({ 'created' => 0, 'updated' => 0, 'unchanged' => 1 }, json)
    row.reload
    assert_equal %w[nothing seen intentional], [row.severity, row.visibility, row.outcome]
    assert_equal 'A renamed repo is refused', row.summary
  end

  test 'a change to a confirmed row adds a journal note with the user and the old and new values' do
    row = review_findings(:wrong)
    confirmed_on = row.confirmed_on
    assert_difference 'note_count', 1 do
      put_findings([{ 'fingerprint' => row.fingerprint, 'round' => 2, 'confirm' => true, 'outcome' => 'fixed',
                      'justification' => nil, 'rule_id' => 'repo-rename', 'severity' => 'nothing',
                      'visibility' => 'seen' }], user: 'admin')
    end
    assert_response :success
    row.reload
    assert_equal 'fixed', row.outcome
    assert_nil row.justification
    assert_equal 1, row.confirmed_by_id
    assert_not_equal confirmed_on, row.confirmed_on

    note = Journal.where(journalized_type: 'Issue', journalized_id: 1).order(:id).last
    assert_equal 1, note.user_id
    assert_includes note.notes, 'Redmine Admin changed review finding finding/v1:0000000000000004 in round 2'
    assert_includes note.notes, '* outcome: "wrong" → "fixed"'
    assert_includes note.notes, '* justification: "It is read once" → (empty)'
    assert_not_includes note.notes, 'severity'
  end

  test 'a second send of the same outcome line changes nothing' do
    row = review_findings(:wrong)
    line = { 'fingerprint' => row.fingerprint, 'round' => 2, 'confirm' => true, 'outcome' => 'wrong',
             'justification' => 'It is read once', 'rule_id' => 'repo-rename', 'severity' => 'nothing',
             'visibility' => 'seen' }
    assert_no_difference 'note_count' do
      put_findings([line], user: 'admin')
    end
    assert_response :success
    assert_equal({ 'created' => 0, 'updated' => 0, 'unchanged' => 1 }, json)
    row_after = row.reload
    assert_equal 2, row_after.confirmed_by_id
    assert_equal review_findings(:wrong).confirmed_on, row_after.confirmed_on
  end

  test "a verdict line sets a confirmed row's verified_round, with a journal note" do
    row = review_findings(:repeat_round_1)
    assert_difference 'note_count', 1 do
      put_findings([{ 'fingerprint' => row.fingerprint, 'round' => 1, 'verified_round' => 2 }])
    end
    assert_response :success
    assert_equal 2, row.reload.verified_round
    assert_includes Journal.order(:id).last.notes, '* verified_round: (empty) → "2"'
  end

  test 'an outcome line needs a row and an outcome' do
    put_findings([{ 'fingerprint' => 'finding/v1:00000000000000ff', 'round' => 1, 'confirm' => true,
                    'outcome' => 'fixed' }])
    assert_response :unprocessable_content
    assert_match(/has no row in round 1/, json['errors'].first)

    put_findings([{ 'fingerprint' => review_findings(:open_silent).fingerprint, 'round' => 1, 'confirm' => true,
                    'outcome' => '' }])
    assert_response :unprocessable_content
    assert_match(/needs an outcome/, json['errors'].first)
  end

  test 'a confirmed row always has an outcome, and an unconfirmed one may not' do
    confirmed = review_findings(:fixed_verified)
    confirmed.outcome = nil
    refute confirmed.valid?
    assert confirmed.errors.added?(:outcome, :blank)
    assert review_findings(:open_silent).valid?
  end

  test 'a 422 saves nothing from its request' do
    rows = [finding_row('finding/v1:00000000000000b1', 4),
            { 'fingerprint' => review_findings(:open_silent).fingerprint, 'round' => 1, 'line' => 99 },
            finding_row('finding/v1:00000000000000b2', 4, tag: 'Huge')]
    assert_no_difference ['ReviewFinding.count', 'Journal.count'] do
      put_findings(rows)
    end
    assert_response :unprocessable_content
    assert_equal 1, json['errors'].size
    assert_match(/\Arow 3: Tag/, json['errors'].first)
    assert_equal 12, review_findings(:open_silent).reload.line
  end

  test 'unknown fields, wrong types and a body that is not a list are refused' do
    put_findings([finding_row('finding/v1:00000000000000c1', 4, colour: 'red')])
    assert_response :unprocessable_content
    put_findings([finding_row('finding/v1:00000000000000c1', 4, summary: ['a', 'b'])])
    assert_response :unprocessable_content
    put_findings([finding_row('finding/v1:00000000000000c1', '4')])
    assert_response :unprocessable_content
    put_findings([finding_row('finding/v1:00000000000000c1', 4, confirm: 'yes')])
    assert_response :unprocessable_content
    put_findings({ 'fingerprint' => 'finding/v1:00000000000000c1' })
    assert_response :unprocessable_content
    assert_equal 0, ReviewFinding.where(fingerprint: 'finding/v1:00000000000000c1').count
  end

  test 'L1: a PUT without manage_review_findings gets 403' do
    assert_no_difference 'ReviewFinding.count' do
      put_findings([finding_row('finding/v1:00000000000000d1', 4)], user: 'dlopper')
    end
    assert_response :forbidden
  end

  test 'L1: a pr_url off the pattern gets 422' do
    ['javascript:alert(1)', 'https://github.com/example/plan/pull/1?x=<script>'].each do |url|
      put_findings([finding_row('finding/v1:00000000000000d2', 4, pr_url: url)])
      assert_response :unprocessable_content
    end
    assert_nil ReviewFinding.find_by(fingerprint: 'finding/v1:00000000000000d2')
  end

  # Another window's send commits the same round first; this one hits the unique index, rolls
  # back and runs again, finding the rows (§3.10).
  test 'L12: two sends of one round leave one set of rows' do
    rows = [finding_row('finding/v1:00000000000000e1', 4), finding_row('finding/v1:00000000000000e2', 4)]
    other_window = Module.new do
      define_method(:record) do |issue, sent, user, **round|
        unless @raced
          @raced = true
          super(issue, sent, user, **round)
          raise ActiveRecord::RecordNotUnique, 'UNIQUE constraint failed: review_findings.issue_id'
        end
        super(issue, sent, user, **round)
      end
    end
    ReviewFinding.singleton_class.prepend(other_window)
    begin
      assert_difference 'ReviewFinding.count', 2 do
        put_findings(rows)
      end
    ensure
      other_window.send(:remove_method, :record)
      ReviewFinding.remove_instance_variable(:@raced) if ReviewFinding.instance_variable_defined?(:@raced)
    end
    assert_response :success
    assert_equal({ 'created' => 0, 'updated' => 0, 'unchanged' => 2 }, json)
    assert_equal 1, ReviewFinding.where(issue_id: 1, fingerprint: 'finding/v1:00000000000000e1').count
  end

  test 'L12: a send that fails twice answers 500 and saves nothing' do
    ReviewFinding.stubs(:record).raises(ActiveRecord::StatementTimeout, 'database is locked')
    assert_no_difference 'ReviewFinding.count' do
      put_findings([finding_row('finding/v1:00000000000000e3', 4)])
    end
    assert_response :internal_server_error
  end

  test 'rules.json counts real and noise per rule over confirmed rows, each fingerprint once by its latest round' do
    get '/projects/1/review_findings/rules.json', headers: signed_in('dlopper')
    assert_response :success
    assert_equal [{ 'rule_id' => 'ignored-file-overwrite', 'real' => 2, 'noise' => 1 },
                  { 'rule_id' => 'repo-rename', 'real' => 1, 'noise' => 2 }], json['rules']
  end

  test 'rules.json counts only the issues the user can see' do
    # Developer sees only its own issues: dlopper is assigned issue 2, and not issue 1.
    Role.find(2).update!(issues_visibility: 'own')
    get '/projects/1/review_findings/rules.json', headers: signed_in('dlopper')
    assert_response :success
    assert_equal [{ 'rule_id' => 'ignored-file-overwrite', 'real' => 1, 'noise' => 0 }], json['rules']
  end

  test 'rules.json needs view_review_findings' do
    Role.find(2).remove_permission!(:view_review_findings)
    get '/projects/1/review_findings/rules.json', headers: signed_in('dlopper')
    assert_response :forbidden
  end

  def rename_rule(to, user: 'jsmith', from: 'ignored-file-overwrite')
    put "/projects/1/review_findings/rules/#{from}.json",
        params: { 'new_rule_id' => to }.to_json,
        headers: { 'CONTENT_TYPE' => 'application/json' }.merge(signed_in(user))
  end

  def rule_ids
    ReviewFinding.order(:id).pluck(:id, :rule_id)
  end

  test 'GET rules/:rule_id counts the rule findings and the confirmed ones' do
    get '/projects/1/review_findings/rules/ignored-file-overwrite.json', headers: signed_in('dlopper')
    assert_response :success
    assert_equal({ 'rule_id' => 'ignored-file-overwrite', 'findings' => 4, 'confirmed' => 3 }, json)
  end

  test 'a rename moves every finding to the new rule, notes each confirmed one, and a second changes nothing' do
    assert_difference 'Journal.count', 3 do
      rename_rule('file-overwrite')
    end
    assert_response :success
    assert_equal({ 'renamed' => 4, 'noted' => 3 }, json)
    assert_equal 0, ReviewFinding.where(rule_id: 'ignored-file-overwrite').count
    assert_equal 4, ReviewFinding.where(rule_id: 'file-overwrite').count
    note = Journal.order(:id).last
    assert_includes note.notes, 'changed review finding'
    assert_includes note.notes, '* rule_id: "ignored-file-overwrite" → "file-overwrite"'
    assert_equal User.find_by(login: 'jsmith'), note.user
    assert_not_nil review_findings(:fixed_verified).reload.confirmed_by_id

    assert_no_difference 'Journal.count' do
      rename_rule('file-overwrite')
    end
    assert_response :success
    assert_equal({ 'renamed' => 0, 'noted' => 0 }, json)
  end

  test 'a new rule id that is not kebab-case, or is the old one, is refused' do
    before = rule_ids
    ['File-Overwrite', '', nil, 'ignored-file-overwrite'].each do |to|
      assert_no_difference 'Journal.count' do
        rename_rule(to)
      end
      assert_response :unprocessable_content
      assert_match(/new_rule_id/, json['errors'].first)
    end
    assert_equal before, rule_ids
  end

  test 'L3: a rename that fails part-way renames none' do
    ReviewFinding.where(rule_id: 'ignored-file-overwrite').order(:id).last.update_column(:summary, '')
    before = rule_ids
    assert_no_difference 'Journal.count' do
      rename_rule('file-overwrite')
    end
    assert_response :unprocessable_content
    assert_match(/Summary/, json['errors'].first)
    assert_equal before, rule_ids
  end

  test 'L1: a rename without manage_review_findings gets 403' do
    rename_rule('file-overwrite', user: 'dlopper')
    assert_response :forbidden
    assert_equal 0, ReviewFinding.where(rule_id: 'file-overwrite').count
  end

  test 'L1: a finding on an issue the user cannot see refuses the whole rename' do
    # Manager sees only its own issues: jsmith wrote issue 1, and issue 2, which has the seed row,
    # is made dlopper's.
    Role.find(1).update!(issues_visibility: 'own')
    Issue.find(2).update_columns(author_id: 3, assigned_to_id: 3)
    Watcher.where(watchable_type: 'Issue', watchable_id: 2).delete_all
    before = rule_ids
    rename_rule('file-overwrite')
    assert_response :unprocessable_content
    assert_match(/1 finding with rule ignored-file-overwrite is on issues you can't see or manage/, json['errors'].first)
    assert_equal before, rule_ids
  end
end
