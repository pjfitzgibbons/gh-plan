require File.expand_path('../test_helper', __dir__)

class ReviewRoundsApiTest < Redmine::ApiTest::Base
  include ReviewFindingsTestHelper
  fixtures :projects, :users, :email_addresses, :roles, :members, :member_roles, :issues,
           :issue_statuses, :trackers, :projects_trackers, :enabled_modules, :enumerations,
           :journals, :review_findings, :review_rounds

  def setup
    super
    enable_review_findings
  end

  def put_round(round, rows = nil, user: 'jsmith', issue: 1)
    body = { 'review_round' => round }
    body['review_findings'] = rows unless rows.nil?
    put "/issues/#{issue}/review_findings.json", params: body.to_json,
                                                 headers: { 'CONTENT_TYPE' => 'application/json' }
                                                   .merge(credentials(user, user == 'dlopper' ? 'foo' : user))
  end

  def json
    JSON.parse(response.body)
  end

  def round_4(**fields)
    { 'commit' => '4' * 40, 'round' => 4, 'ci' => 'success', 'ci_summary' => 'assess: 9 passed',
      'base' => '0' * 40, 'effort' => 'high', 'review_md' => 'base', 'summary' => '1 finding, 1 to fix.',
      'controls' => ['REVIEW.md'], 'dropped' => [], 'new_rules' => ['new-rule'] }.merge(fields.stringify_keys)
  end

  test 'GET returns the issue rounds beside its findings' do
    get '/issues/1/review_findings.json', headers: credentials('dlopper', 'foo')
    assert_response :success
    assert_equal [1, 2, 3, nil], json['review_rounds'].map { |r| r['round'] }
    assert_equal '1' * 40, json['review_rounds'].first['commit']
    assert_equal [], json['review_rounds'].first['controls']
  end

  test 'a PUT records the round and its rows together, and the same PUT again changes nothing' do
    rows = [finding_row('finding/v1:00000000000000f1', 4, commit: '4' * 40)]
    assert_difference ['ReviewRound.count', 'ReviewFinding.count'], 1 do
      put_round(round_4, rows)
    end
    assert_response :success
    assert_equal({ 'created' => 1, 'updated' => 0, 'unchanged' => 0, 'round' => 'created' }, json)
    saved = ReviewRound.find_by(commit: '4' * 40)
    assert_equal [4, ['REVIEW.md'], ['new-rule']], [saved.round, saved.controls, saved.new_rules]

    assert_no_difference ['ReviewRound.count', 'ReviewFinding.count'] do
      put_round(round_4, rows)
    end
    assert_equal({ 'created' => 0, 'updated' => 0, 'unchanged' => 1, 'round' => 'unchanged' }, json)
  end

  test 'a clean round is recorded with no rows, and counts 0 open silent' do
    put_round(round_4(commit: '5' * 40, round: 1, summary: '0 findings, 0 to fix.'), [], issue: 3)
    assert_response :success
    assert_equal 0, ReviewFinding.open_silent_count(3)
    get '/issues/3/review_findings.json', headers: credentials('jsmith', 'jsmith')
    assert_equal [], json['review_findings']
    assert_equal [1], json['review_rounds'].map { |r| r['round'] }
  end

  test 'a review whose CI failed has no round number and takes no rows' do
    put_round(round_4(round: nil, ci: 'failure'))
    assert_response :success
    assert_nil ReviewRound.find_by(commit: '4' * 40).round

    assert_no_difference ['ReviewRound.count', 'ReviewFinding.count'] do
      put_round(round_4(commit: '6' * 40, round: nil, ci: 'failure'), [finding_row('finding/v1:00000000000000f2', 4)])
    end
    assert_response :unprocessable_content
    assert_match(/takes no rows/, json['errors'].join)
  end

  test 'a round number goes with a passing CI, and only with one' do
    put_round(round_4(ci: 'failure'))
    assert_response :unprocessable_content
    put_round(round_4(round: nil))
    assert_response :unprocessable_content
    assert_nil ReviewRound.find_by(commit: '4' * 40)
  end

  test "a round's number never changes, and two commits can't share one" do
    put_round(round_4(commit: '1' * 40))
    assert_response :unprocessable_content
    assert_match(/already round 1/, json['errors'].first)

    assert_no_difference 'ReviewRound.count' do
      put_round(round_4(round: 3))
    end
    assert_response :unprocessable_content
    assert_match(/round 3 is 3{40}'s/, json['errors'].first)
  end

  test 'a bad round field saves neither the round nor its rows' do
    [{ 'ci' => 'green' }, { 'colour' => 'red' }, { 'controls' => [1] }, { 'commit' => 'abc' },
     { 'effort' => 'max' }].each do |bad|
      assert_no_difference ['ReviewRound.count', 'ReviewFinding.count'] do
        put_round(round_4(**bad.symbolize_keys), [finding_row('finding/v1:00000000000000f3', 4)])
      end
      assert_response :unprocessable_content
    end
  end

  test 'a bad row saves neither the rows nor the round' do
    assert_no_difference ['ReviewRound.count', 'ReviewFinding.count'] do
      put_round(round_4, [finding_row('finding/v1:00000000000000f4', 4, tag: 'Huge')])
    end
    assert_response :unprocessable_content
  end

  test 'L1: a round PUT needs manage_review_findings' do
    assert_no_difference 'ReviewRound.count' do
      put_round(round_4, user: 'dlopper')
    end
    assert_response :forbidden
  end
end
