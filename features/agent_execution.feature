Feature: FR-10 Execute bounded model candidates

  Scenario: Run all candidates under one locked plan
    Given an immutable plan containing several compatible candidates
    When the agent executes the modelling plan
    Then every candidate uses the same cell-level split
    And preprocessing and tuning use training and validation data only
    And each candidate receives a traceable run record

  Scenario: Isolate a failed candidate
    Given an immutable plan contains one valid and one failing candidate
    When the agent executes the modelling plan
    Then the valid candidate completes normally
    And the failing candidate receives a structured failure record
    And the failing candidate is not silently removed from the comparison

  Scenario: Reject an operation outside the locked plan
    Given an immutable plan with bounded models parameters and runtime
    When an execution request exceeds a declared bound
    Then the request is rejected without changing the plan
    And the policy violation is recorded

  Scenario: Execute a locked plan through the CLI
    Given portable evidence and an immutable executable plan
    When the researcher runs the agent execute command
    Then the CLI creates a completed candidate execution bundle
    And held-out test predictions remain unavailable to candidate execution
