require File.expand_path('../test_helper', __dir__)

class ReviewFindingTest < ActiveSupport::TestCase
  include ReviewFindingsTestHelper
  fixtures :projects, :users, :issues, :trackers, :issue_statuses, :enumerations,
           :review_findings, :review_rounds

  def finding(**fields)
    ReviewFinding.new(finding_row('1.1', 'lib/a.rb:3-5:some-rule', 1, **fields).merge('issue_id' => 1))
  end

  test 'a raising row is valid' do
    assert finding.valid?, finding.errors.full_messages.join(', ')
  end

  test 'its key is the round that raised it and its place in that round, wherever it moves' do
    assert_equal '1.1', finding.key
    assert_equal '1.3', finding(ordinal: 3).key
    assert_equal '1.1', finding(round: 2, outcome: 'fixed', file: 'lib/b.rb', line_start: 40, line_end: 42).key
  end

  test 'its place is the file and its lines' do
    assert_equal 'lib/a.rb:3-5', finding.place
    assert_equal 'lib/a.rb:3', finding(line_end: 3).place
  end

  test 'a row needs a place in its round, a file, lines in order and a kebab-case rule' do
    refute finding(ordinal: 0).valid?
    refute finding(ordinal: nil).valid?
    refute finding(file: nil).valid?
    refute finding(file: '../a.rb').valid?
    refute finding(file: '/etc/a').valid?
    refute finding(line_start: 0).valid?
    refute finding(line_end: 2).valid?
    refute finding(rule_id: 'Some_Rule').valid?
  end

  test 'a fingerprint is empty or a SHA-256 in lowercase hex' do
    assert finding(fingerprint: 'a' * 64).valid?
    refute finding(fingerprint: 'A' * 64).valid?
    refute finding(fingerprint: 'x').valid?
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
    refute finding(source: 'outcomes', outcome: 'superseded', confirmed_by_id: 2, justification: 'Moved').valid?
  end

  test 'the raising round says new; a later review row may check or supersede' do
    raised = finding(outcome: 'fixed')
    refute raised.valid?
    assert_includes raised.errors[:outcome], 'is new in the round that raises the finding'
    refute finding(outcome: 'superseded').valid?
    assert finding(round: 2, outcome: 'superseded').valid?
    assert finding(round: 2, outcome: 'new').valid?
  end

  test 'a row comes in or after the round that raised its finding' do
    late = finding(raised_round: 2)
    refute late.valid?
    assert_includes late.errors[:raised_round], 'must be round or earlier'
  end

  test 'an answer is confirmed by someone, and one other than fixed is justified' do
    refute finding(source: 'outcomes', outcome: 'fixed').valid?
    assert finding(source: 'outcomes', outcome: 'fixed', confirmed_by_id: 2).valid?
    refute finding(source: 'outcomes', outcome: 'wrong', confirmed_by_id: 2).valid?
    assert finding(source: 'outcomes', outcome: 'wrong', confirmed_by_id: 2, justification: 'Misreads it').valid?
    refute finding(confirmed_by_id: 2).valid?
  end

  test 'round 0 is a missed finding raised by a review' do
    assert finding(round: 0, raised_round: 0).valid?
    refute finding(round: 0, raised_round: 0, outcome: 'fixed').valid?
    assert finding(round: 0, raised_round: 0, source: 'outcomes', outcome: 'fixed', confirmed_by_id: 2).valid?
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
    assert_equal({ '1.1' => 'open', '1.2' => 'fixed', '1.3' => 'intentional', '2.1' => 'fixed, unchecked' }, statuses)
  end

  test "an answer's original finding is its key's review row in the round the key names" do
    raised = finding
    check = finding(round: 2, outcome: 'fixed')
    other = finding(ordinal: 2)
    again = finding(round: 2, raised_round: 2)
    answer = finding(round: 2, source: 'outcomes', outcome: 'fixed')
    assert_same raised, answer.original_finding([check, other, again, raised])
    assert_nil answer.original_finding([check, other, again])
  end

  test "a later row takes the rule of the row that raised its finding" do
    raised = finding
    check = finding(round: 2, outcome: 'fixed', rule_id: 'other-rule')
    check.take_rule_from_original([raised])
    assert_equal 'some-rule', check.rule_id
  end

  test 'a check, a supersede or an answer is refused without its raising row' do
    raised = finding
    [finding(round: 2, outcome: 'fixed'), finding(round: 2, outcome: 'superseded'),
     finding(source: 'outcomes', outcome: 'fixed')].each do |row|
      assert_nil ReviewFinding.refuse_unraised(row, [raised])
      error = assert_raises(ReviewFinding::Refused) { ReviewFinding.refuse_unraised(row, []) }
      assert_equal ["#{row.key}: round 1 didn't raise it"], error.messages
    end
    assert_nil ReviewFinding.refuse_unraised(raised, [])
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
