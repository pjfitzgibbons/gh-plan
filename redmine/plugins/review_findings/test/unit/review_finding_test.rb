require File.expand_path('../test_helper', __dir__)

class ReviewFindingTest < ActiveSupport::TestCase
  include ReviewFindingsTestHelper
  fixtures :projects, :users, :issues, :review_findings

  def finding(**fields)
    ReviewFinding.new({ issue_id: 1 }.merge(finding_row('finding/v1:00000000000000aa', 1, **fields).symbolize_keys))
  end

  def assert_refused(attribute, **fields)
    record = finding(**fields)
    assert_not record.valid?, "#{fields} should be refused"
    assert record.errors[attribute].any?, "#{fields} should be refused on #{attribute}: #{record.errors.full_messages}"
  end

  test 'every fixture row is valid' do
    ReviewFinding.find_each { |f| assert f.valid?, "#{f.fingerprint}: #{f.errors.full_messages}" }
  end

  test 'a row with the required fields is valid' do
    assert finding.valid?, finding.errors.full_messages.inspect
  end

  test 'values outside the allowed ones are refused' do
    assert_refused :tag, tag: 'Huge'
    assert_refused :tag, tag: nil
    assert_refused :severity, severity: 'everything'
    assert_refused :visibility, visibility: 'loud'
    assert_refused :outcome, outcome: 'fixd'
    assert_refused :summary, summary: nil
    assert_refused :summary, summary: 'x' * 256
    assert_refused :commit, commit: 'abc123'
    assert_refused :commit, commit: 'A' * 40
    assert_refused :line, line: 0
    assert_refused :line, line: 'twelve'
    assert_refused :rule_id, rule_id: 'Ignored_File'
    assert_refused :file, file: '/etc/passwd'
    assert_refused :file, file: 'lib/../../secrets'
    assert_refused :round, round: -1
    assert_refused :round, round: 'one'
    assert_refused :round, round: 2**31
    assert_refused :line, line: 10**20
    assert_refused :verified_round, verified_round: 1
  end

  test 'fingerprints follow the versioned recipes' do
    assert_refused :fingerprint, fingerprint: 'finding/v1:xyz'
    assert_refused :fingerprint, fingerprint: 'finding/v2:0000000000000001'
    assert_refused :fingerprint, fingerprint: "seed/v1:82-R6.3\nmore"
    assert finding(fingerprint: 'seed/v1:82-R6.3').valid?
    assert finding(fingerprint: 'missed/v1:00000000000000ab', round: 0).valid?
  end

  test 'round 0 is for a missed finding, and only for one' do
    assert_refused :round, round: 0
    assert_refused :round, fingerprint: 'missed/v1:00000000000000ab', round: 1
  end

  test 'visibility is empty only when severity is nothing' do
    assert_refused :visibility, visibility: nil
    assert finding(visibility: nil, severity: 'nothing').valid?
  end

  test 'an empty string is saved as NULL' do
    record = finding(body: '', rule_id: '', file: '  ', visibility: '', severity: 'nothing')
    assert record.save, record.errors.full_messages.inspect
    record.reload
    assert_nil record.body
    assert_nil record.rule_id
    assert_nil record.file
    assert_nil record.visibility
  end

  test 'pr_url must be a GitHub pull request' do
    assert finding(pr_url: 'https://github.com/example/plan/pull/114').valid?
    assert_refused :pr_url, pr_url: 'javascript:alert(1)'
    assert_refused :pr_url, pr_url: 'http://github.com/example/plan/pull/114'
    assert_refused :pr_url, pr_url: 'https://github.com/example/plan/pull/114/files'
    assert_refused :pr_url, pr_url: "https://github.com/example/plan/pull/114\njavascript:alert(1)"
    assert_refused :pr_url, pr_url: 'https://github.com.evil.example/a/b/pull/1'
  end

  test 'the key is unique by issue, fingerprint and round' do
    taken = review_findings(:open_silent)
    assert finding(fingerprint: taken.fingerprint, round: 2).valid?
    assert ReviewFinding.new(finding(fingerprint: taken.fingerprint).attributes.merge('issue_id' => 2)).valid?
    assert_raises(ActiveRecord::RecordNotUnique) do
      finding(fingerprint: taken.fingerprint, round: taken.round).save
    end
  end

  test "a request reads the issue's rows once, however many it sends" do
    rows = (1..10).map { |n| finding_row(format('finding/v1:%016x', 0xf00 + n), 4) }
    rows << finding_row(review_findings(:open_silent).fingerprint, 1, summary: 'Reworded')
    selects = []
    count = ->(*, payload) { selects << payload[:sql] if payload[:sql] =~ /\ASELECT .*review_findings/i }
    ActiveSupport::Notifications.subscribed(count, 'sql.active_record') do
      assert_equal({ 'created' => 10, 'updated' => 1, 'unchanged' => 0 },
                   ReviewFinding.record(Issue.find(1), rows, User.find(2)))
    end
    assert_equal 1, selects.size, selects.join("\n")
  end

  test 'a key sent twice in one request makes one row' do
    row = finding_row('finding/v1:00000000000000f1', 4)
    counts = ReviewFinding.record(Issue.find(1), [row, row.merge('summary' => 'Second')], User.find(2))
    assert_equal({ 'created' => 1, 'updated' => 1, 'unchanged' => 0 }, counts)
    assert_equal ['Second'], ReviewFinding.where(fingerprint: 'finding/v1:00000000000000f1').pluck(:summary)
  end

  test 'the open silent count is nil with no rows, and counts only silent rows with no outcome' do
    assert_equal 2, ReviewFinding.open_silent_count(1)
    assert_equal 0, ReviewFinding.open_silent_count(2)
    assert_nil ReviewFinding.open_silent_count(3)
    assert_equal [review_findings(:open_silent).id, review_findings(:repeat_round_3).id].sort,
                 ReviewFinding.where(issue_id: 1).open_silent.ids.sort
  end

  test 'a finding answered in a later round is no longer open silent' do
    fingerprint = 'finding/v1:00000000000000cc'
    ReviewFinding.record(Issue.find(3), [finding_row(fingerprint, 1)], User.find(2))
    assert_equal 1, ReviewFinding.open_silent_count(3)
    ReviewFinding.record(Issue.find(3), [finding_row(fingerprint, 2)], User.find(2))
    assert_equal 1, ReviewFinding.open_silent_count(3)
    ReviewFinding.record(Issue.find(3), [{ 'fingerprint' => fingerprint, 'round' => 2, 'confirm' => true,
                                           'outcome' => 'fixed' }], User.find(2))
    assert_equal 0, ReviewFinding.open_silent_count(3)
  end
end
