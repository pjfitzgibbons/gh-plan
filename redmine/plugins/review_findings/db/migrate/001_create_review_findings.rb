# One row per finding per round (design 0005, §3.1). A change migration, so VERSION=0 drops the
# table and its index.
class CreateReviewFindings < ActiveRecord::Migration[8.1]
  def change
    create_table :review_findings do |t|
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
    add_index :review_findings, %i[issue_id fingerprint round], unique: true,
                                                               name: 'index_review_findings_on_key'
  end
end
