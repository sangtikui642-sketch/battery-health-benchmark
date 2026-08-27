Feature: FR-01 Import and normalize battery cycle data
  As a battery algorithm researcher
  I want different input tables converted to a normalized schema
  So that feature and model code can be reused

  Scenario: Import a valid configured CSV file
    Given a CSV file containing cell identity, cycle index, and capacity
    And a field mapping with capacity measured in ampere-hours
    When the researcher imports the cycle data
    Then a normalized cycle file is created
    And a quality report records the cell count and cycle count
    And the report contains no machine-specific absolute input path

  Scenario: Reject input missing a required field
    Given a CSV file without a cell identity field
    And a field mapping with capacity measured in ampere-hours
    When the researcher imports the cycle data
    Then the import fails with an error naming the missing field
    And no partial normalized output is left behind
