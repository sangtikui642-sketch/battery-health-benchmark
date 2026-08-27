Feature: FR-08 Protect sensitive data

  Scenario: Reject disallowed repository content
    Given raw battery data or a credential is present in a tracked location
    When the researcher runs the repository safety check
    Then the check fails without displaying the credential value
    And the report identifies the disallowed file type

  Scenario: Use reproducible anonymous fixtures
    Given an acceptance test requires representative battery data
    When the test suite runs
    Then it uses an anonymous reproducible fixture
    And it does not depend on company data or a personal directory
