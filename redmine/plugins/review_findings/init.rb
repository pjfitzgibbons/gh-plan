Redmine::Plugin.register :review_findings do
  name 'Review findings'
  version '1.0.0'
  project_module :review_findings do
    permission :view_review_findings, { review_findings: %i[index rules] }, read: true
    permission :manage_review_findings, { review_findings: [:update] }, require: :member
  end
end
