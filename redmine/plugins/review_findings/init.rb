Redmine::Plugin.register :review_findings do
  name 'Review findings'
  version '1.0.0'
  project_module :review_findings do
    permission :view_review_findings, { review_findings: %i[index tab rules rule] }, read: true
    permission :manage_review_findings, { review_findings: %i[update rename] }, require: :member
  end
  menu :project_menu, :review_findings, { controller: 'review_findings', action: 'tab' },
       caption: 'Findings', param: :project_id
end

# Autoloaded from lib/; naming it here loads it, which registers the hook.
ReviewFindings::Hooks
