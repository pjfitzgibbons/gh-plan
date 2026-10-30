# One event on one finding of an issue's branch (design 0005, §3.1): the reviewer raising it or
# checking its fix (`review`), or a person's answer (`outcomes`). Rows are only added; a finding's
# status is its key's latest row, worked out on read. The rule rename is the one change to a row.
class ReviewFinding < ApplicationRecord
  # A request the API refuses with 422, saving nothing from it.
  class Refused < StandardError
    attr_reader :messages

    def initialize(messages)
      @messages = Array(messages)
      super(@messages.join('; '))
    end
  end

  SOURCES = %w[review outcomes].freeze
  TAGS = %w[Important Nit Pre-existing Security].freeze
  SEVERITIES = %w[work files commits tracker nothing].freeze
  VISIBILITIES = %w[silent seen].freeze
  OUTCOMES = %w[new fixed intentional wrong doesnt-matter].freeze
  ANSWERS = (OUTCOMES - %w[new]).freeze
  CHECKS = %w[new fixed].freeze
  REAL = %w[fixed intentional].freeze
  NOISE = %w[wrong doesnt-matter].freeze

  RULE = /\A[a-z0-9]+(?:-[a-z0-9]+)*\z/
  # Past an integer column's range, save raises instead of validating.
  INT_MAX = 2**31

  KEY = %w[file line_start line_end rule_id].freeze
  FIELDS = (KEY + %w[round source outcome tag severity visibility summary body justification]).freeze

  # A finding's rows newest first: by round, then an `outcomes` row after the `review` row of its round.
  NEWEST_FIRST = 'round DESC, CASE source WHEN \'outcomes\' THEN 0 ELSE 1 END'.freeze

  belongs_to :issue
  belongs_to :confirmed_by, class_name: 'User', optional: true

  before_validation :blanks_to_nil

  validates :round, numericality: { only_integer: true, greater_than_or_equal_to: 0, less_than: INT_MAX }
  validates :source, inclusion: { in: SOURCES }
  validates :file, presence: true, length: { maximum: 255 }, format: { without: %r{\A/|(?:\A|/)\.\.(?:/|\z)} }
  validates :line_start, :line_end, numericality: { only_integer: true, greater_than: 0, less_than: INT_MAX }
  validates :rule_id, presence: true, length: { maximum: 255 }, format: { with: RULE }
  validates :outcome, inclusion: { in: OUTCOMES }
  validates :tag, inclusion: { in: TAGS }, allow_nil: true
  validates :severity, inclusion: { in: SEVERITIES }
  validates :visibility, inclusion: { in: VISIBILITIES }, allow_nil: true
  validates :summary, length: { maximum: 255 }
  validate :lines_in_order, :visibility_unless_nothing, :outcome_for_source, :justified, :confirmed_only_by_answer

  def key
    lines = line_start == line_end ? line_start.to_s : "#{line_start}-#{line_end}"
    "#{file}:#{lines}:#{rule_id}"
  end

  def key_fields
    KEY.map { |f| self[f] }
  end

  # A finding to fix (§3.2): silent and losing something, or a crash.
  def to_fix?
    (visibility == 'silent' && severity != 'nothing') || rule_id == 'crash'
  end

  # Where a finding stands after its latest row (§3.1).
  def status
    case [source, outcome]
    in [_, 'new'] then 'open'
    in ['outcomes', 'fixed'] then 'fixed, unchecked'
    in ['review', 'fixed'] then 'fixed'
    else outcome
    end
  end

  # Each key's rows of the given issues, newest first, grouped by [issue_id, key].
  def self.by_finding(scope)
    scope.order(:issue_id, *KEY.map(&:to_sym), Arel.sql(NEWEST_FIRST)).to_a.group_by { |f| [f.issue_id, *f.key_fields] }
  end

  # One issue's ledger and what it adds up to (§3.1, §3.5).
  def self.ledger(issue)
    rows = where(issue_id: issue.id)
    groups = by_finding(rows).values
    findings = groups.map do |events|
      latest = events.first
      raised = events.reverse.find { |f| f.source == 'review' } || events.last
      { 'key' => latest.key, 'round' => raised.round, 'summary' => raised.summary, 'status' => latest.status,
        'to_fix' => latest.to_fix?, 'latest' => latest.as_json }
    end
    open = findings.count { |f| f['to_fix'] && f['status'] == 'open' }
    unchecked = findings.count { |f| f['to_fix'] && f['status'] == 'fixed, unchecked' }
    { 'last_round' => ReviewRound.last_round(issue.id),
      'review_rounds' => ReviewRound.where(issue_id: issue.id).order(:round).as_json,
      'review_findings' => rows.order(:round, :id).as_json,
      'findings' => findings,
      'open_to_fix' => open,
      'fixed_unchecked' => unchecked,
      'rules' => groups.group_by { |events| events.first.rule_id }.transform_values(&:size) }
  end

  # The issue's open-to-fix count, or nil before its first round and with no row, as before any
  # review. A clean round counts 0.
  def self.open_to_fix_count(issue_id)
    rows = where(issue_id: issue_id)
    return nil if !rows.exists? && !ReviewRound.exists?(issue_id: issue_id)

    by_finding(rows).values.count { |events| events.first.to_fix? && events.first.status == 'open' }
  end

  # Adds a POST's round and rows in one transaction (§3.1, §3.3). A row identical to one already
  # there is skipped; anything else wrong refuses the whole request, naming every bad row, with
  # nothing saved. The unique indexes, not a validation, keep each event once. Answers the counts.
  def self.record(issue, rows, user, round: nil)
    raise Refused, 'review_findings must be a list of rows' unless rows.is_a?(Array)

    counts = { 'created' => 0, 'skipped' => 0 }
    errors = []
    transaction do
      unless round.nil?
        begin
          counts['round'] = ReviewRound.record(issue, round)
        rescue Refused => e
          errors.concat(e.messages)
        end
      end
      rounds = ReviewRound.where(issue_id: issue.id).pluck(:round).to_set
      found = where(issue_id: issue.id).to_a
      rows.each_with_index do |row, i|
        counts[record_row(issue, row, user, rounds, found)] += 1
      rescue Refused => e
        errors.concat(e.messages.map { |m| "row #{i + 1}: #{m}" })
      end
      raise ActiveRecord::Rollback if errors.any?
    end
    raise Refused, errors if errors.any?

    counts
  end

  # `found` holds the issue's rows, and gains each row this request adds, so a check or an answer
  # may follow the row that raised it in one request.
  def self.record_row(issue, row, user, rounds, found)
    raise Refused, 'is not an object' unless row.is_a?(Hash)

    row = row.stringify_keys
    unknown = row.keys - FIELDS
    raise Refused, "can't send #{unknown.join(', ')}" if unknown.any?

    typed = row.reject { |_, v| v.nil? || v.is_a?(String) || v.is_a?(Integer) }.keys
    raise Refused, "#{typed.join(', ')} must be a string, a whole number or null" if typed.any?

    finding = new(row.merge('issue_id' => issue.id))
    finding.confirmed_by = user if finding.source == 'outcomes'
    raise Refused, "#{finding.key}: #{finding.errors.full_messages.join(', ')}" unless finding.valid?

    same = found.find { |f| f.key_fields == finding.key_fields && f.round == finding.round && f.source == finding.source }
    if same
      same.assign_attributes(row.except('issue_id'))
      same.blanks_to_nil
      return 'skipped' unless same.changed?

      raise Refused, "#{finding.key} is already in round #{finding.round} from #{finding.source}, " \
                     "with other #{same.changed.join(', ')}"
    end
    if finding.round.positive? && !rounds.include?(finding.round)
      raise Refused, "#{finding.key}: round #{finding.round} isn't recorded"
    end

    earlier = found.select { |f| f.key_fields == finding.key_fields && f.round <= finding.round }
    unless finding.source == 'review' && finding.outcome == 'new'
      raised = earlier.any? { |f| f.source == 'review' && (f.round < finding.round || finding.source == 'outcomes') }
      raise Refused, "#{finding.key}: no earlier round raised it" unless raised
    end

    finding.save!
    found << finding
    'created'
  end

  def blanks_to_nil
    self.class.columns.each do |column|
      next unless %i[string text].include?(column.type)

      value = self[column.name]
      self[column.name] = nil if value.is_a?(String) && value.strip.empty?
    end
  end

  # Per rule, over the given issues, each key counted once by its latest `outcomes` row (§3.6,
  # §3.7): how many were real and how many noise.
  def self.rule_counts(issue_ids)
    answers = by_finding(where(issue_id: issue_ids, source: 'outcomes')).values.map(&:first)
    answers.group_by(&:rule_id).map do |rule, latest|
      { 'rule_id' => rule, 'real' => latest.count { |f| REAL.include?(f.outcome) },
        'noise' => latest.count { |f| NOISE.include?(f.outcome) } }
    end.sort_by { |r| r['rule_id'] }
  end

  # How many keys of the given issues have the rule, and how many of those have an answer.
  def self.rule_findings(issue_ids, rule)
    events = by_finding(where(issue_id: issue_ids, rule_id: rule)).values
    { 'rule_id' => rule, 'findings' => events.size,
      'confirmed' => events.count { |rows| rows.any? { |f| f.source == 'outcomes' } } }
  end

  # Moves every row of the given issues from one rule id to another in one transaction, so a rule
  # renamed in REVIEW.md keeps one id across its findings (§3.10), with a journal note on each issue
  # it touches. A row with the old id on an issue of `tree` that isn't one of the given issues, such
  # as one in a closed project, refuses the rename; the check is inside the transaction, so a row
  # sent meanwhile can't be left behind. A row whose new key another row already has refuses it too.
  # Answers how many rows it renamed and how many issues it noted, or raises Refused having renamed
  # none.
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

      where(issue_id: issue_ids, rule_id: from).group_by(&:issue).each do |issue, rows|
        rows.each do |finding|
          finding.rule_id = to
          finding.save!
        rescue ActiveRecord::RecordNotUnique
          raise Refused, "#{finding.key} is already a finding of issue ##{issue.id}"
        end
        issue.journals.create!(user: user, notes: "#{user.name} renamed review rule #{from} to #{to} " \
                                                  "on #{rows.size} #{rows.size == 1 ? 'row' : 'rows'}.")
        counts['renamed'] += rows.size
        counts['noted'] += 1
      end
    end
    counts
  end

  def as_json(*)
    super(except: %i[id issue_id]).merge('key' => key)
  end

  private

  def lines_in_order
    return unless line_start.is_a?(Integer) && line_end.is_a?(Integer)

    errors.add(:line_end, 'must be line_start or later') if line_end < line_start
  end

  def visibility_unless_nothing
    return unless visibility.nil? && severity.present? && severity != 'nothing'

    errors.add(:visibility, 'is empty only when severity is nothing')
  end

  # The reviewer raises a finding (`new`) or checks a fix (`new` or `fixed`); a person answers.
  def outcome_for_source
    if source == 'review' && !CHECKS.include?(outcome)
      errors.add(:outcome, "from a review is #{CHECKS.join(' or ')}")
    elsif source == 'outcomes' && !ANSWERS.include?(outcome)
      errors.add(:outcome, "from outcomes is #{ANSWERS.join(', ')}")
    end
    errors.add(:round, 'is 0 only for a missed finding a review raised') if round == 0 && source == 'review' && outcome != 'new'
  end

  def justified
    return unless source == 'outcomes' && outcome.present? && outcome != 'fixed' && justification.nil?

    errors.add(:justification, "is needed for #{outcome}")
  end

  def confirmed_only_by_answer
    if source == 'outcomes' && confirmed_by_id.nil?
      errors.add(:confirmed_by_id, 'is needed for outcomes')
    elsif source == 'review' && confirmed_by_id
      errors.add(:confirmed_by_id, 'is empty for a review')
    end
  end
end
