# One finding in one review round of an issue's branch (design 0005, §3.1).
class ReviewFinding < ApplicationRecord
  # A request the API refuses with 422, saving nothing from it.
  class Refused < StandardError
    attr_reader :messages

    def initialize(messages)
      @messages = Array(messages)
      super(@messages.join('; '))
    end
  end

  TAGS = %w[Important Nit Pre-existing Security].freeze
  SEVERITIES = %w[work files commits tracker nothing].freeze
  VISIBILITIES = %w[silent seen].freeze
  OUTCOMES = %w[fixed intentional wrong doesnt-matter].freeze
  REAL = %w[fixed intentional].freeze
  NOISE = %w[wrong doesnt-matter].freeze

  FINGERPRINT = %r{\A(?:(?:finding|missed)/v1:[0-9a-f]{16}|seed/v1:[A-Za-z0-9._-]+)\z}
  PR_URL = %r{\Ahttps://github\.com/[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+/pull/[1-9][0-9]*\z}
  RULE = /\A[a-z0-9]+(?:-[a-z0-9]+)*\z/
  # Past an integer column's range, save raises instead of validating.
  INT_MAX = 2**31

  # What a row is found by, what a round line may set, and what an outcome line sets (§3.4). Only
  # an outcome line confirms, and the fields it sets are the ones a journal note reports.
  KEY = %w[fingerprint round].freeze
  ROUND_FIELDS = %w[commit file line rule_id tag severity visibility summary body verified_round pr_url].freeze
  CONFIRMED_FIELDS = %w[outcome justification rule_id severity visibility].freeze
  NOTED_FIELDS = (CONFIRMED_FIELDS + %w[verified_round]).freeze

  belongs_to :issue
  belongs_to :confirmed_by, class_name: 'User', optional: true

  before_validation :blanks_to_nil

  validates :fingerprint, presence: true, length: { maximum: 64 }
  validates :fingerprint, format: { with: FINGERPRINT }, allow_nil: true
  validates :round, numericality: { only_integer: true, greater_than_or_equal_to: 0, less_than: INT_MAX }
  validates :commit, format: { with: /\A[0-9a-f]{40}\z/ }, allow_nil: true
  validates :file, length: { maximum: 255 }, format: { without: %r{\A/|(?:\A|/)\.\.(?:/|\z)} }, allow_nil: true
  validates :line, numericality: { only_integer: true, greater_than: 0, less_than: INT_MAX }, allow_nil: true
  validates :rule_id, length: { maximum: 255 }, format: { with: RULE }, allow_nil: true
  validates :tag, inclusion: { in: TAGS }
  validates :severity, inclusion: { in: SEVERITIES }
  validates :visibility, inclusion: { in: VISIBILITIES }, allow_nil: true
  validates :summary, presence: true, length: { maximum: 255 }
  validates :outcome, inclusion: { in: OUTCOMES }, allow_nil: true
  # The counting queries (§3.6) read a confirmed row's outcome without a fallback.
  validates :outcome, presence: true, if: :confirmed_by_id?
  validates :verified_round, numericality: { only_integer: true, less_than: INT_MAX }, allow_nil: true
  validates :pr_url, format: { with: PR_URL }, allow_nil: true
  validate :visibility_unless_nothing, :round_zero_for_missed, :verified_after_round

  # Upserts a request's rows by (fingerprint, round) in one transaction (§3.1, §3.3). Answers the
  # counts, or raises Refused naming every bad row, having saved nothing. The issue's rows are read
  # once, inside the transaction; the unique index, not a validation, keeps the key unique.
  def self.record(issue, rows, user)
    raise Refused, 'review_findings must be a list of rows' unless rows.is_a?(Array)

    counts = { 'created' => 0, 'updated' => 0, 'unchanged' => 0 }
    errors = []
    transaction do
      fingerprints = rows.filter_map { |row| row.stringify_keys['fingerprint'] if row.is_a?(Hash) }
      found = where(issue_id: issue.id, fingerprint: fingerprints).index_by { |f| [f.fingerprint, f.round] }
      rows.each_with_index do |row, i|
        counts[record_row(issue, row, user, found)] += 1
      rescue Refused => e
        errors.concat(e.messages.map { |m| "row #{i + 1}: #{m}" })
      end
      raise ActiveRecord::Rollback if errors.any?
    end
    raise Refused, errors if errors.any?

    counts
  end

  # `found` holds the issue's rows by [fingerprint, round], and gains each row this request makes,
  # so a key sent twice in one request updates the row the first line made.
  def self.record_row(issue, row, user, found)
    raise Refused, 'is not an object' unless row.is_a?(Hash)

    row = row.stringify_keys
    confirm = row.delete('confirm')
    raise Refused, '"confirm" must be true or false' unless [nil, true, false].include?(confirm)

    allowed = KEY + (confirm ? CONFIRMED_FIELDS : ROUND_FIELDS)
    unknown = row.keys - allowed
    if unknown.any?
      raise Refused, "#{confirm ? 'an outcome line' : 'a round line'} can't send #{unknown.join(', ')}"
    end

    typed = row.reject { |_, v| v.nil? || v.is_a?(String) || v.is_a?(Integer) }.keys
    raise Refused, "#{typed.join(', ')} must be a string, a whole number or null" if typed.any?

    fingerprint = row.delete('fingerprint')
    round = row.delete('round')
    raise Refused, 'needs a fingerprint' unless fingerprint.is_a?(String) && fingerprint.present?
    raise Refused, 'needs a round, as a whole number' unless round.is_a?(Integer)

    finding = found[[fingerprint, round]]
    if confirm
      raise Refused, "#{fingerprint} has no row in round #{round}" unless finding
      raise Refused, 'an outcome line needs an outcome' if row['outcome'].blank?
    end
    finding ||= found[[fingerprint, round]] = new(fingerprint: fingerprint, round: round)
    finding.issue = issue
    finding.apply(row, user, confirm: confirm)
  end

  # Sets a row's fields from one line, then saves it if anything changed. A confirmed row changes
  # only from an outcome line, apart from verified_round, which a round's verdict line sets; each
  # change to a confirmed row adds a journal note to the issue.
  def apply(row, user, confirm: false)
    created = new_record?
    confirmed = confirmed_by_id.present?
    before = NOTED_FIELDS.index_with { |f| self[f] }

    row = row.slice('verified_round') if confirmed && !confirm
    assign_attributes(row)
    blanks_to_nil
    if confirm && (!confirmed || changed?)
      self.confirmed_by = user
      self.confirmed_on = Time.now
    end
    return 'unchanged' unless changed?
    raise Refused, errors.full_messages unless save

    note_change(before, user) if confirmed
    created ? 'created' : 'updated'
  end

  def blanks_to_nil
    self.class.columns.each do |column|
      next unless %i[string text].include?(column.type)

      value = self[column.name]
      self[column.name] = nil if value.is_a?(String) && value.strip.empty?
    end
  end

  # Per rule, over confirmed rows of the given issues, each fingerprint counted once by its latest
  # confirmed round (§3.6, §3.7): how many were real and how many were noise.
  def self.rule_counts(issue_ids)
    latest = <<~SQL.squish
      review_findings.round = (SELECT MAX(g.round) FROM review_findings g
        WHERE g.issue_id = review_findings.issue_id AND g.fingerprint = review_findings.fingerprint
          AND g.confirmed_by_id IS NOT NULL)
    SQL
    counts = where(issue_id: issue_ids).where.not(confirmed_by_id: nil).where.not(rule_id: nil)
                                       .where(latest).group(:rule_id, :outcome).count
    counts.each_with_object({}) do |((rule, outcome), n), rules|
      rule_row = rules[rule] ||= { 'rule_id' => rule, 'real' => 0, 'noise' => 0 }
      rule_row['real'] += n if REAL.include?(outcome)
      rule_row['noise'] += n if NOISE.include?(outcome)
    end.values.sort_by { |r| r['rule_id'] }
  end

  # How many rows of the given issues have the rule, and how many of those are confirmed.
  def self.rule_findings(issue_ids, rule)
    rows = where(issue_id: issue_ids, rule_id: rule)
    { 'rule_id' => rule, 'findings' => rows.count, 'confirmed' => rows.where.not(confirmed_by_id: nil).count }
  end

  # Moves every row of the given issues from one rule id to another in one transaction, so a rule
  # renamed in REVIEW.md keeps one id across its findings (§3.10). Each confirmed row gets the
  # journal note an outcome line's change makes. A row with the old id on an issue of `tree` that
  # isn't one of the given issues, such as one in a closed project, refuses the rename; the check
  # is inside the transaction, so a row sent meanwhile can't be left behind. Answers how many it
  # renamed and noted, or raises Refused having renamed none.
  def self.rename_rule(issue_ids, tree, from, to, user)
    unless to.is_a?(String) && RULE.match?(to)
      raise Refused, 'new_rule_id must be lowercase words and digits joined by hyphens'
    end
    raise Refused, "new_rule_id is #{from}, the rule's own id" if to == from

    counts = { 'renamed' => 0, 'noted' => 0 }
    transaction do
      outside = where(rule_id: from, issue_id: tree).where.not(issue_id: issue_ids).count
      if outside.positive?
        raise Refused, "#{outside} #{outside == 1 ? 'finding' : 'findings'} with rule #{from} " \
                       "#{outside == 1 ? 'is' : 'are'} on issues you can't see or manage review " \
                       'findings in, or in a closed project'
      end

      where(issue_id: issue_ids, rule_id: from).order(:id).each do |finding|
        counts['renamed'] += 1
        counts['noted'] += 1 if finding.rename_rule(to, user)
      end
    end
    counts
  end

  # Answers whether it added a journal note, which it does for a confirmed row.
  def rename_rule(to, user)
    before = NOTED_FIELDS.index_with { |f| self[f] }
    self.rule_id = to
    raise Refused, "#{fingerprint} in round #{round}: #{errors.full_messages.join(', ')}" unless save
    return false unless confirmed_by_id.present?

    note_change(before, user)
    true
  end

  private

  def note_change(before, user)
    lines = NOTED_FIELDS.filter_map do |f|
      "* #{f}: #{shown(before[f])} → #{shown(self[f])}" unless before[f] == self[f]
    end
    return if lines.empty?

    issue.journals.create!(
      user: user,
      notes: "#{user.name} changed review finding #{fingerprint} in round #{round}:\n\n#{lines.join("\n")}"
    )
  end

  def shown(value)
    value.nil? ? '(empty)' : "\"#{value}\""
  end

  def visibility_unless_nothing
    return unless visibility.nil? && severity.present? && severity != 'nothing'

    errors.add(:visibility, 'is empty only when severity is nothing')
  end

  def round_zero_for_missed
    return unless round.is_a?(Integer) && fingerprint.is_a?(String)
    return if (round == 0) == fingerprint.start_with?('missed/')

    errors.add(:round, 'is 0 for a missed finding, and only for one')
  end

  def verified_after_round
    return unless verified_round.is_a?(Integer) && round.is_a?(Integer)

    errors.add(:verified_round, 'must be a later round') unless verified_round > round
  end
end
