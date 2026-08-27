Feature: FR-05 Train SOH prediction baselines

  Scenario Outline: Train a supported baseline
    Given validated data and a leakage-free split manifest
    And the selected model is <model>
    When the researcher trains the baseline
    Then preprocessing is fitted using training data only
    And predictions are generated for the test partition
    And model configuration and random seed are saved

    Examples:
      | model                      |
      | linear_regression          |
      | random_forest              |
      | histogram_gradient_boosting|
