Feature: FR-11 Select and validate models without test leakage

  Scenario: Lock a candidate using validation evidence only
    Given completed candidate runs with validation predictions
    When the agent applies the declared selection policy
    Then candidates are ranked without access to test labels or test metrics
    And the selected plugin version and configuration are locked
    And the selection decision links to validation evidence

  Scenario: Reject a candidate with physically implausible predictions
    Given a candidate predicts SOH outside the configured plausible range
    When the agent applies the mandatory validation gates
    Then the physical plausibility gate fails
    And the report identifies the affected cell and cycle without changing the prediction
    And the agent does not declare that candidate a winner

  Scenario: Finalize one held-out test after selection is locked
    Given a selected candidate and configuration are locked
    And all mandatory validation gates passed
    When the researcher finalizes the run
    Then the locked candidate is evaluated once on the held-out test partition
    And final predictions and metrics are linked to the locked selection
    And another test evaluation requires a new run identity

  Scenario: Do not declare a winner after a mandatory gate failure
    Given all completed candidates fail at least one mandatory gate
    When the agent applies the declared selection policy
    Then the run state becomes rejected
    And all negative evidence is retained
    And no validation threshold is weakened automatically

  Scenario: Reject execution evidence that exposes the test partition
    Given completed candidate evidence claims access to the test partition
    When the agent applies the declared selection policy
    Then the run state becomes rejected
    And the test isolation violation is recorded
    And no selection lock is created

  Scenario: Validate and finalize a locked run through the CLI
    Given portable evidence and a completed candidate execution bundle
    When the researcher validates and finalizes the run through the CLI
    Then the CLI records the validated locked and finalized states
    And the CLI reports the run and selection identities
    And repeating finalization through the CLI is rejected without overwriting evidence
