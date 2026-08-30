Feature: FR-12 Publish a deterministic and verifiable evidence bundle

  Scenario: Generate a finalized evidence bundle without a language model
    Given a finalized agent run with portable source evidence
    And no language model or network service is configured
    When the researcher generates the deterministic evidence report
    Then the strict evidence manifest links plans configurations fingerprints outcomes and gates
    And the held-out test claims link to finalization artifacts
    And the evidence bundle verifies offline

  Scenario: Retain every outcome in a rejected run report
    Given a rejected run containing successful failed timed-out and gate-rejected candidates
    When the researcher generates the deterministic evidence report
    Then successful failed timed-out and rejected outcomes remain visible
    And no winner or final-test performance is claimed

  Scenario: Report a locked run that has not been finalized
    Given a locked agent run without held-out test evaluation
    When the researcher generates the deterministic evidence report
    Then the report explicitly marks the final test as not finalized
    And no quantitative claim uses the test partition

  Scenario: Detect a tampered evidence artifact through the API and CLI
    Given a generated evidence bundle with one altered protected artifact
    When the researcher verifies the evidence bundle through the API and CLI
    Then both verification paths reject the bundle
    And the original evidence sources remain unchanged

  Scenario: Resolve every quantitative report claim to evidence
    Given a finalized agent run with portable source evidence
    When the researcher generates the deterministic evidence report
    Then every quantitative claim resolves to a hashed artifact and numeric JSON location

  Scenario: Keep evidence fingerprints deterministic across output directories
    Given one locked agent run used for two report outputs
    When the researcher generates both deterministic evidence reports
    Then both evidence fingerprints are identical
    And no timestamp participates in the evidence fingerprint
