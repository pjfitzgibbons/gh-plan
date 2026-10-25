require File.expand_path('../../../test/test_helper', __dir__)

module ReviewFindingsTestHelper
  # Unit and integration tests each keep their own fixture paths, so each class adds the plugin's.
  def self.included(test_class)
    test_class.fixture_paths += [File.expand_path('fixtures', __dir__)]
  end

  # Project 1 gets the module; Manager (jsmith) may view and manage, Developer (dlopper) only view.
  def enable_review_findings
    Project.find(1).enable_module!(:review_findings)
    Role.find(1).add_permission!(:view_review_findings, :manage_review_findings)
    Role.find(2).add_permission!(:view_review_findings)
  end

  def finding_row(fingerprint, round = 1, **fields)
    { 'fingerprint' => fingerprint, 'round' => round, 'tag' => 'Important', 'severity' => 'files',
      'visibility' => 'silent', 'summary' => 'A copy loses a file' }.merge(fields.stringify_keys)
  end
end
