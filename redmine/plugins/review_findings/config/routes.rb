get 'projects/:project_id/review_findings', to: 'review_findings#tab', as: 'project_review_findings',
    format: false

scope format: true, constraints: { format: 'json' } do
  get 'issues/:id/review_findings', to: 'review_findings#index'
  put 'issues/:id/review_findings', to: 'review_findings#update'
  get 'projects/:project_id/review_findings/rules', to: 'review_findings#rules'
  get 'projects/:project_id/review_findings/rules/:rule_id', to: 'review_findings#rule'
  put 'projects/:project_id/review_findings/rules/:rule_id', to: 'review_findings#rename'
end
