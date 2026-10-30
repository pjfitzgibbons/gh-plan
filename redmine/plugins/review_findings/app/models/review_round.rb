# One review of one pushed commit of an issue's branch (design 0005, §3.1): round k, numbered from
# 1 whatever its CI. A round with no findings is still a row, so a clean review leaves a trace. A
# row is only added, never changed.
class ReviewRound < ApplicationRecord
  CI = %w[success failure none].freeze
  EFFORTS = %w[medium high].freeze
  SOURCES = %w[base commit].freeze
  FIELDS = %w[round commit ci ci_summary repo pr_number base effort review_md summary controls new_rules].freeze
  LISTS = %w[controls new_rules].freeze
  REPO = %r{\A[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+\z}

  belongs_to :issue

  serialize :controls, coder: JSON, type: Array
  serialize :new_rules, coder: JSON, type: Array

  validates :round, numericality: { only_integer: true, greater_than: 0, less_than: ReviewFinding::INT_MAX }
  validates :commit, format: { with: /\A[0-9a-f]{40}\z/ }
  validates :base, format: { with: /\A[0-9a-f]{40}\z/ }, allow_nil: true
  validates :ci, inclusion: { in: CI }
  validates :ci_summary, length: { maximum: 255 }
  validates :repo, format: { with: REPO }, length: { maximum: 255 }, allow_nil: true
  validates :pr_number, numericality: { only_integer: true, greater_than: 0, less_than: ReviewFinding::INT_MAX },
                        allow_nil: true
  validates :effort, inclusion: { in: EFFORTS }, allow_nil: true
  validates :review_md, inclusion: { in: SOURCES }, allow_nil: true
  validate :lists_of_strings, :pr_with_repo

  before_validation do
    %w[ci_summary repo base effort review_md summary].each do |f|
      self[f] = nil if self[f].is_a?(String) && self[f].strip.empty?
    end
  end

  # The issue's highest round, or 0 before its first.
  def self.last_round(issue_id)
    where(issue_id: issue_id).maximum(:round) || 0
  end

  # Adds the round a POST sends, inside the caller's transaction. Answers 'created', or 'skipped'
  # when the same round is already there with the same values; raises Refused for a round already
  # there with other values, a commit another round has, or a number that isn't the next one.
  def self.record(issue, fields)
    raise ReviewFinding::Refused, 'review_round must be an object' unless fields.is_a?(Hash)

    fields = fields.stringify_keys
    unknown = fields.keys - FIELDS
    raise ReviewFinding::Refused, "review_round can't send #{unknown.join(', ')}" if unknown.any?

    typed = fields.reject do |k, v|
      v.nil? || v.is_a?(String) || v.is_a?(Integer) || (LISTS.include?(k) && v.is_a?(Array))
    end.keys
    raise ReviewFinding::Refused, "review_round: #{typed.join(', ')} has the wrong type" if typed.any?

    k = fields['round']
    raise ReviewFinding::Refused, 'review_round needs a round, as a whole number' unless k.is_a?(Integer)

    found = find_by(issue_id: issue.id, round: k)
    if found
      found.assign_attributes(fields)
      return 'skipped' unless found.changed?

      raise ReviewFinding::Refused, "review_round: round #{k} is already recorded with other " \
                                    "#{found.changed.join(', ')}"
    end

    taken = where(issue_id: issue.id, commit: fields['commit'].to_s).pick(:round)
    raise ReviewFinding::Refused, "review_round: #{fields['commit']} is already round #{taken}" if taken

    last = last_round(issue.id)
    raise ReviewFinding::Refused, "review_round: round #{k} isn't the next one; the last is #{last}" if k != last + 1

    round = new(fields.merge('issue_id' => issue.id))
    raise ReviewFinding::Refused, round.errors.full_messages.map { |m| "review_round: #{m}" } unless round.save

    'created'
  end

  def as_json(*)
    super(except: %i[id issue_id])
  end

  private

  def lists_of_strings
    LISTS.each do |list|
      value = self[list]
      errors.add(list, 'must be a list of strings') unless value.nil? || (value.is_a?(Array) && value.all?(String))
    end
  end

  def pr_with_repo
    errors.add(:pr_number, 'needs a repo') if pr_number && repo.nil?
  end
end
