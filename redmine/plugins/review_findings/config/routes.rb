scope format: true, constraints: { format: 'json' } do
  get 'issues/:id/review_findings', to: 'review_findings#index'
  put 'issues/:id/review_findings', to: 'review_findings#update'
  get 'projects/:project_id/review_findings/rules', to: 'review_findings#rules'
end
