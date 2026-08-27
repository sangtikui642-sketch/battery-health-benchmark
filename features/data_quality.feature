Feature: FR-02 Validate battery data quality

  Scenario: Reject duplicate cycles
    Given one cell has the same cycle index twice
    When the researcher validates the cycle data
    Then validation fails
    And the quality report records the duplicate count

  Scenario: Report an invalid capacity without silently deleting it
    Given a cycle has a negative capacity
    When the researcher validates the cycle data
    Then validation fails
    And the quality report identifies the cell and cycle
