Feature: Run an offline synthetic SOH benchmark

  Scenario: Complete the three-model benchmark from one leakage-safe split
    Given an offline deterministic demo configuration
    When the researcher runs the benchmark command
    Then the command succeeds without network access
    And all three registered baselines reference the same split manifest
    And every model retains predictions metrics and a comparison plot
    And the run retains reproducibility evidence and limitations without selecting a winner
