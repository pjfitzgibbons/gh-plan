# One review of one pushed commit of an issue's branch (design 0005, §3.3): a round once its CI
# passed, numbered from 1, and a review with no number when it didn't. A round with no findings
# is still a row, so a clean review leaves a trace.
class ReviewRound < ApplicationRecord
  CI = %w[success failure none].freeze
  EFFORTS = %w[medium high].freeze
  SOURCES = %w[base commit].freeze
  FIELDS = %w[commit round ci ci_summary base effort review_md summary controls dropped new_rules].freeze
  LISTS = %w[controls dropped new_rules].freeze

  belongs_to :issue

  serialize :controls, coder: JSON, type: Array
  serialize :dropped, coder: JSON, type: Array
  serialize :new_rules, coder: JSON, type: Array

  validates :commit, format: { with: /\A[0-9a-f]{40}\z/ }
  validates :base, format: { with: /\A[0-9a-f]{40}\z/ }, allow_nil: true
  validates :round, numericality: { only_integer: true, greater_than: 0, less_than: ReviewFinding::INT_MAX },
                    allow_nil: true
  validates :ci, inclusion: { in: CI }
  validates :effort, inclusion: { in: EFFORTS }, allow_nil: true
  validates :review_md, inclusion: { in: SOURCES }, allow_nil: true
  validates :ci_summary, length: { maximum: 255 }
  validate :numbered_only_when_passed, :lists_of_strings

  before_validation do
    %w[ci_summary base effort review_md summary].each { |f| self[f] = nil if self[f].is_a?(String) && self[f].strip.empty? }
  end

  # Records the round a PUT sends, by its commit, inside the caller's transaction. Answers
  # created, updated or unchanged, or raises Refused. A round's number never changes once given:
  # its rows are keyed by it.
  def self.record(issue, fields)
    raise ReviewFinding::Refused, 'review_round must be an object' unless fields.is_a?(Hash)

    fields = fields.stringify_keys
    unknown = fields.keys - FIELDS
    raise ReviewFinding::Refused, "review_round can't send #{unknown.join(', ')}" if unknown.any?

    typed = fields.reject do |k, v|
      v.nil? || v.is_a?(String) || v.is_a?(Integer) || (LISTS.include?(k) && v.is_a?(Array))
    end.keys
    raise ReviewFinding::Refused, "review_round: #{typed.join(', ')} has the wrong type" if typed.any?

    found = find_or_initialize_by(issue_id: issue.id, commit: fields['commit'].to_s)
    created = found.new_record?
    if !created && found.round && fields.key?('round') && fields['round'] != found.round
      raise ReviewFinding::Refused, "review_round: #{found.commit} is already round #{found.round}"
    end

    taken = fields['round'].is_a?(Integer) &&
            where(issue_id: issue.id, round: fields['round']).where.not(commit: found.commit).pick(:commit)
    raise ReviewFinding::Refused, "review_round: round #{fields['round']} is #{taken}'s" if taken

    found.assign_attributes(fields.except('commit'))
    return ['unchanged', found] unless found.changed?
    raise ReviewFinding::Refused, found.errors.full_messages.map { |m| "review_round: #{m}" } unless found.save

    [created ? 'created' : 'updated', found]
  end

  def as_json(*)
    super(except: %i[id issue_id])
  end

  private

  def numbered_only_when_passed
    errors.add(:round, 'is given only when ci is success') if round && ci != 'success'
    errors.add(:round, 'is needed when ci is success') if round.nil? && ci == 'success'
  end

  def lists_of_strings
    LISTS.each do |list|
      value = self[list]
      errors.add(list, 'must be a list of strings') unless value.nil? || (value.is_a?(Array) && value.all?(String))
    end
  end
end
