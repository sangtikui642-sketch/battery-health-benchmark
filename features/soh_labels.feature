Feature: FR-04 Generate state-of-health labels

  Scenario: Calculate SOH from configured reference capacity
    Given cycles have valid measured capacities
    And configuration declares the reference capacity
    When the researcher generates SOH labels
    Then each valid cycle receives an SOH value
    And the output records the reference-capacity source

  Scenario: Reject missing reference capacity
    Given neither configuration nor source data provides reference capacity
    When the researcher generates SOH labels
    Then label generation fails
    And the system does not guess a reference capacity
