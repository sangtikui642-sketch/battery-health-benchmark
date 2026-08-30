Feature: FR-13 Govern model plugins and licenses

  Scenario: Approve a complete compatible model plugin
    Given a complete model plugin declaration compatible with the governance policy
    When the agent validates the plugin manifest
    Then the governance decision approves the plugin for compatible plans
    And every required declaration and its fingerprints are retained

  Scenario: Reject an incompatible plugin before planning or training
    Given a complete model plugin declaration with an incompatible target
    When the agent validates the plugin manifest
    Then the governance decision rejects the incompatible plugin
    And no executable plan or training artifact is created

  Scenario: Quarantine a plugin with unknown license status
    Given a complete model plugin declaration omits code and weight licenses
    When the agent validates the plugin manifest
    Then the plugin is excluded from executable plans
    And it may only be listed as an external research reference
    And no external code or weight is bundled

  Scenario: Reject an incomplete plugin declaration before output
    Given a model plugin declaration omits its serialization and loading contract
    When the agent validates the plugin manifest
    Then manifest validation fails before governance output exists

  Scenario: Detect a tampered governance decision through the API and CLI
    Given a governed plugin bundle with one altered decision
    When the researcher verifies plugin governance through the API and CLI
    Then both governance verification paths reject the bundle

  Scenario: Keep governance deterministic and feed only approved plugins to planning
    Given one compatible plugin catalog used for two governance outputs
    When the agent creates both governed plugin bundles
    Then both governance fingerprints are identical without timestamps
    And the approved registry creates a compatible immutable plan

  Scenario: Govern and verify a mixed plugin catalog through the CLI
    Given a catalog containing approved incompatible and unknown-license plugins
    When the researcher runs the plugin governance and verification commands
    Then the CLI reports every governance outcome and a valid offline bundle
