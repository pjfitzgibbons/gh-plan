# The ledger (design 0005, §3.1): one review_rounds row per reviewed commit, and one review_findings
# row per event on a finding, keyed by file, lines and rule. It replaces 001's table, which live
# Redmine held no rows in. A change migration: rolled back, it drops both tables and restores 001's,
# so VERSION=0 leaves none.
class CreateReviewLedger < ActiveRecord::Migration[8.1]
  def change
    remove_index :review_findings, %i[issue_id fingerprint round], unique: true,
                                                                  name: 'index_review_findings_on_key'
    drop_table :review_findings do |t|
      t.integer :issue_id, null: false
      t.string :commit, limit: 40
      t.integer :round, null: false
      t.string :fingerprint, limit: 64, null: false
      t.string :file
      t.integer :line
      t.string :rule_id
      t.string :tag, null: false
      t.string :severity, null: false
      t.string :visibility
      t.string :summary, limit: 255, null: false
      t.text :body
      t.string :outcome
      t.text :justification
      t.integer :verified_round
      t.integer :confirmed_by_id
      t.datetime :confirmed_on
      t.string :pr_url
      t.datetime :created_on, null: false
      t.datetime :updated_on, null: false
    end

    create_table :review_findings do |t|
      t.integer :issue_id, null: false
      t.integer :round, null: false
      t.string :source, null: false
      t.string :file, null: false
      t.integer :line_start, null: false
      t.integer :line_end, null: false
      t.string :rule_id, null: false
      t.string :outcome, null: false
      t.string :tag
      t.string :severity, null: false
      t.string :visibility
      t.string :summary, limit: 255
      t.text :body
      t.text :justification
      t.integer :confirmed_by_id
      t.datetime :created_on, null: false
    end
    add_index :review_findings, %i[issue_id file line_start line_end rule_id round source], unique: true,
                                                                                           name: 'index_review_findings_on_event'

    create_table :review_rounds do |t|
      t.integer :issue_id, null: false
      t.integer :round, null: false
      t.string :commit, limit: 40, null: false
      t.string :ci, null: false
      t.string :ci_summary
      t.string :repo
      t.integer :pr_number
      t.string :base, limit: 40
      t.string :effort
      t.string :review_md
      t.text :summary
      t.text :controls
      t.text :new_rules
      t.datetime :created_on, null: false
    end
    add_index :review_rounds, %i[issue_id round], unique: true, name: 'index_review_rounds_on_round'
    add_index :review_rounds, %i[issue_id commit], unique: true, name: 'index_review_rounds_on_commit'
  end
end
