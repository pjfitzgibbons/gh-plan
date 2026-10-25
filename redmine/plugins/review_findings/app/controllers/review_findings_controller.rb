# The review findings API (design 0005, §3.1). JSON only; the routes refuse any other format.
class ReviewFindingsController < ApplicationController
  before_action :find_issue, only: %i[index update]
  before_action :find_project_by_project_id, only: [:rules]
  before_action :authorize
  accept_api_auth :index, :update, :rules

  def index
    findings = ReviewFinding.where(issue_id: @issue.id).order(:round, :id)
    render json: { 'review_findings' => findings.as_json }
  end

  # Two sends of one round meet at the unique index, or wait on SQLite's write lock: the loser
  # rolls back and runs once more, finding the rows the winner saved (§3.10). A second failure is
  # a 500, and the sender keeps its file to send again.
  def update
    rows = params.to_unsafe_h['review_findings']
    attempts = 0
    begin
      counts = ReviewFinding.record(@issue, rows, User.current)
    rescue ActiveRecord::RecordNotUnique, ActiveRecord::StatementTimeout
      attempts += 1
      retry if attempts < 2
      raise
    end
    render json: counts
  rescue ReviewFinding::Refused => e
    render json: { 'errors' => e.messages }, status: :unprocessable_content
  end

  # Over the issues of the project and its sub-projects that the user may see findings in.
  def rules
    projects = @project.self_and_descendants.where(Project.allowed_to_condition(User.current, :view_review_findings))
    issue_ids = Issue.visible.where(project_id: projects.select(:id)).select(:id)
    render json: { 'rules' => ReviewFinding.rule_counts(issue_ids) }
  end
end
