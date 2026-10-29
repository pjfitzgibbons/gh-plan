# One row per reviewed commit of an issue's branch (design 0005, §3.3). It only adds, so
# review_findings and its rows are left as they were, and VERSION=1 drops just this table.
class CreateReviewRounds < ActiveRecord::Migration[8.1]
  def change
    create_table :review_rounds do |t|
      t.integer :issue_id, null: false
      t.string :commit, limit: 40, null: false
      t.integer :round
      t.string :ci, null: false
      t.string :ci_summary
      t.string :base, limit: 40
      t.string :effort
      t.string :review_md
      t.text :summary
      t.text :controls
      t.text :dropped
      t.text :new_rules
      t.datetime :created_on, null: false
      t.datetime :updated_on, null: false
    end
    add_index :review_rounds, %i[issue_id commit], unique: true, name: 'index_review_rounds_on_commit'
    add_index :review_rounds, %i[issue_id round], unique: true, name: 'index_review_rounds_on_round'
  end
end
