Feature: FR-07 Reproduce an experiment

  Scenario: Repeat a deterministic experiment
    Given data manifest configuration dependency lock and random seed are unchanged
    When the researcher runs the same experiment twice
    Then both runs use the same split
    And deterministic predictions agree within the documented tolerance
    And each run has a separate traceable record
