Feature: FR-09 Plan an auditable modelling experiment

  Scenario: Create an immutable experiment plan
    Given validated SOH-labelled data and a leakage-free split manifest
    And a registry containing compatible model plugins
    And a resource and validation policy
    When the researcher asks the agent to plan an experiment
    Then the plan records the target data split features models metrics gates budget and seeds
    And the plan records input code environment and license fingerprints
    And the plan receives a stable fingerprint before execution

  Scenario: Reject planning without compatible candidates
    Given validated SOH-labelled data and a leakage-free split manifest
    And no registered model satisfies the feature contract
    When the researcher asks the agent to plan an experiment
    Then planning fails before model training
    And no apparently executable plan is created

  Scenario: Create a planned experiment through the CLI
    Given a completed portable benchmark evidence bundle and planning files
    When the researcher runs the agent plan command
    Then the CLI creates an immutable plan in PLANNED state
    And no candidate training artifacts are created
