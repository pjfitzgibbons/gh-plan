module ReviewFindings
  # The issue page's "Findings: <k> open silent" (design 0005 §5, L5). Core calls the hook inside
  # div.attributes, after the fields, so Ulysses's groups (design 0004) come before it.
  class Hooks < Redmine::Hook::ViewListener
    render_on :view_issues_show_details_bottom, partial: 'review_findings/issue_count'
  end
end
