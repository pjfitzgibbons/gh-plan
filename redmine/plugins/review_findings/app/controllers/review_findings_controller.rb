# The review findings API (design 0005, §3.1). JSON only; the routes refuse any other format.
class ReviewFindingsController < ApplicationController
  before_action :find_issue, only: %i[index update]
  before_action :find_project_by_project_id, only: %i[rules rule rename]
  before_action :authorize
  accept_api_auth :index, :update, :rules, :rule, :rename

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
    render json: { 'rules' => ReviewFinding.rule_counts(issue_ids(:view_review_findings)) }
  end

  # How many findings have the rule, and how many of them are confirmed, over the same issues.
  def rule
    render json: ReviewFinding.rule_findings(issue_ids(:view_review_findings), params[:rule_id])
  end

  # Renames the rule on every finding in the project and its sub-projects (§3.10), or on none: a
  # finding on an issue the user can't manage findings in refuses the whole rename.
  def rename
    tree = Issue.where(project_id: @project.self_and_descendants.select(:id)).select(:id)
    render json: ReviewFinding.rename_rule(issue_ids(:manage_review_findings), tree, params[:rule_id],
                                           params.to_unsafe_h['new_rule_id'], User.current)
  rescue ReviewFinding::Refused => e
    render json: { 'errors' => e.messages }, status: :unprocessable_content
  end

  private

  # The issues the user may see in the project and its sub-projects that have the permission.
  def issue_ids(permission)
    projects = @project.self_and_descendants.where(Project.allowed_to_condition(User.current, permission))
    Issue.visible.where(project_id: projects.select(:id)).select(:id)
  end
end
