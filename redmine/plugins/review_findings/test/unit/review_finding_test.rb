require File.expand_path('../test_helper', __dir__)

class ReviewFindingTest < ActiveSupport::TestCase
  include ReviewFindingsTestHelper
  fixtures :projects, :users, :issues, :trackers, :issue_statuses, :enumerations,
           :review_findings, :review_rounds

  def finding(**fields)
    ReviewFinding.new(finding_row('lib/a.rb:3-5:some-rule', 1, **fields).merge('issue_id' => 1))
  end

  test 'a raising row is valid' do
    assert finding.valid?, finding.errors.full_messages.join(', ')
  end

  test 'its key names the file, the lines and the rule' do
    assert_equal 'lib/a.rb:3-5:some-rule', finding.key
    assert_equal 'lib/a.rb:3:some-rule', finding(line_end: 3).key
  end

  test 'a key needs a file, lines in order and a kebab-case rule' do
    refute finding(file: nil).valid?
    refute finding(file: '../a.rb').valid?
    refute finding(file: '/etc/a').valid?
    refute finding(line_start: 0).valid?
    refute finding(line_end: 2).valid?
    refute finding(rule_id: 'Some_Rule').valid?
  end

  test 'a value outside the allowed ones is invalid' do
    refute finding(source: 'copilot').valid?
    refute finding(tag: 'Big').valid?
    refute finding(severity: 'data').valid?
    refute finding(visibility: 'loud').valid?
    refute finding(outcome: 'gone').valid?
  end

  test 'visibility is empty only when severity is nothing' do
    refute finding(visibility: nil).valid?
    assert finding(visibility: nil, severity: 'nothing').valid?
  end

  test 'a review raises or checks; a person answers' do
    refute finding(outcome: 'intentional').valid?
    assert finding(outcome: 'fixed', round: 2).valid?
    answer = finding(source: 'outcomes', outcome: 'new', confirmed_by_id: 2)
    refute answer.valid?
    assert_includes answer.errors[:outcome], 'from outcomes is fixed, intentional, wrong, doesnt-matter'
  end

  test 'an answer is confirmed by someone, and one other than fixed is justified' do
    refute finding(source: 'outcomes', outcome: 'fixed').valid?
    assert finding(source: 'outcomes', outcome: 'fixed', confirmed_by_id: 2).valid?
    refute finding(source: 'outcomes', outcome: 'wrong', confirmed_by_id: 2).valid?
    assert finding(source: 'outcomes', outcome: 'wrong', confirmed_by_id: 2, justification: 'Misreads it').valid?
    refute finding(confirmed_by_id: 2).valid?
  end

  test 'round 0 is a missed finding raised by a review' do
    assert finding(round: 0).valid?
    refute finding(round: 0, outcome: 'fixed').valid?
    assert finding(round: 0, source: 'outcomes', outcome: 'fixed', confirmed_by_id: 2).valid?
  end

  test 'an empty string is saved as NULL' do
    row = finding(body: '  ', tag: '')
    row.valid?
    assert_nil row.body
    assert_nil row.tag
  end

  test 'status is the latest row, an answer after the review of its round' do
    statuses = ReviewFinding.by_finding(ReviewFinding.where(issue_id: 1)).values.to_h do |events|
      [events.first.key, events.first.status]
    end
    assert_equal({ 'lib/remote.rb:7:repo-rename' => 'intentional',
                   'lib/sync.rb:12-14:ignored-file-overwrite' => 'open',
                   'lib/sync.rb:40:forced-update' => 'fixed',
                   'lib/sync.rb:60-61:ignored-file-overwrite' => 'fixed, unchecked' }, statuses)
  end

  test 'the open-to-fix count, or nil before any review' do
    assert_equal 1, ReviewFinding.open_to_fix_count(1)
    assert_equal 1, ReviewFinding.open_to_fix_count(2)
    assert_nil ReviewFinding.open_to_fix_count(3)
  end

  test 'a clean round counts 0 open to fix' do
    ReviewRound.create!(issue_id: 3, round: 1, commit: 'b' * 40, ci: 'success')
    assert_equal 0, ReviewFinding.open_to_fix_count(3)
  end
end
