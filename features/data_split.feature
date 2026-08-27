Feature: FR-03 Split data without cell leakage

  Scenario: Create train validation and test groups by cell
    Given normalized data containing several cells and cycles
    When the researcher creates a split using cell identity
    Then each cell occurs in exactly one partition
    And the split manifest records the random seed and cell lists

  Scenario: Reject a manifest with cell leakage
    Given a cell identity appears in both train and test
    When the researcher validates the split manifest
    Then validation fails
    And the error identifies the overlapping cell
