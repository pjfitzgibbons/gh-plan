require File.expand_path('../test_helper', __dir__)

class ReviewFindingsViewsTest < Redmine::IntegrationTest
  include ReviewFindingsTestHelper
  fixtures :projects, :users, :email_addresses, :roles, :members, :member_roles, :issues,
           :issue_statuses, :trackers, :projects_trackers, :enabled_modules, :enumerations,
           :journals, :review_findings, :review_rounds

  def setup
    super
    enable_review_findings
    log_user('jsmith', 'jsmith')
  end

  test 'the tab lists one line per finding, each open finding to fix first' do
    get '/projects/ecookbook/review_findings'
    assert_response :success
    assert_select '#main-menu a.review-findings.selected', text: 'Findings'
    rows = css_select('table.list.review-findings tbody tr')
    assert_equal 5, rows.size
    assert_equal [true, true, false, false, false], rows.map { |tr| tr['class'].include?('open-to-fix') }
  end

  test 'the tab filtered to an issue lists only its findings, by key and status' do
    get '/projects/ecookbook/review_findings', params: { issue_id: 1 }
    assert_response :success
    assert_select 'h2 a[href="/issues/1"]'
    assert_select 'table.list.review-findings tbody tr', 4
    assert_select 'td', text: 'lib/sync.rb:60-61:ignored-file-overwrite'
    assert_select 'td', text: /fixed, unchecked/
    assert_select 'td', text: 'fixed (round 2)'
  end

  test 'the tab filtered to an issue with no rows says no review yet' do
    get '/projects/ecookbook/review_findings', params: { issue_id: 3 }
    assert_response :success
    assert_select 'p.nodata', text: 'No review yet.'
    assert_select 'table.list.review-findings', 0
  end

  test 'an issue_id that is not a number is a 404, not an error' do
    get '/projects/ecookbook/review_findings', params: { issue_id: ['1'] }
    assert_response :not_found
    get '/projects/ecookbook/review_findings', params: { issue_id: { 'x' => '1' } }
    assert_response :not_found
    get '/projects/ecookbook/review_findings', params: { issue_id: '1abc' }
    assert_response :not_found
  end

  test 'the tab answers HTML only' do
    get '/projects/ecookbook/review_findings.json'
    assert_response :not_found
    # Redmine takes ?format=json for an API call, which tab doesn't accept, so authorize refuses it.
    get '/projects/ecookbook/review_findings', params: { format: 'json' }
    assert_response :forbidden
  end

  test "an issue outside the project's tree is a 404" do
    get '/projects/ecookbook/review_findings', params: { issue_id: 4 }
    assert_response :not_found
  end

  test "a finding's text renders escaped, as text" do
    ReviewFinding.create!(issue_id: 3, **finding_row('lib/a.rb:1:crash', 0,
                                                     summary: '<script>alert(1)</script>',
                                                     body: '<a href="https://example.com">x</a>').symbolize_keys)
    get '/projects/ecookbook/review_findings', params: { issue_id: 3 }
    assert_response :success
    assert_select 'table.list.review-findings script', 0
    assert_select 'a[href="https://example.com"]', 0
    assert_includes response.body, '&lt;script&gt;alert(1)&lt;/script&gt;'
    assert_includes response.body, '&lt;a href="https://example.com"&gt;x&lt;/a&gt;'
  end

  test "the issue page shows its open-to-fix count, linking to the tab filtered to it" do
    get '/issues/1'
    assert_select 'div.attributes div.review-findings div.value ' \
                  'a[href="/projects/ecookbook/review_findings?issue_id=1"]', text: '1 open to fix'
    get '/issues/2'
    assert_select 'div.attributes div.review-findings div.value a', text: '1 open to fix'
    get '/issues/3'
    assert_select 'div.attributes div.review-findings div.value a', text: 'no review yet'
  end

  test 'without View review findings there is no count, and the tab is refused' do
    Role.find(1).remove_permission!(:view_review_findings)
    get '/issues/1'
    assert_response :success
    assert_select 'div.review-findings', 0
    get '/projects/ecookbook/review_findings'
    assert_response :forbidden
  end
end
