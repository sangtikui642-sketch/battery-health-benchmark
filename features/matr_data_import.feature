Feature: FR-14 Import an official MATR HDF5 battery batch
  As a battery algorithm researcher
  I want a fail-closed adapter for MATR cycle summaries
  So that real battery cycles enter AutoBench with portable provenance

  Scenario: Import a valid MATR HDF5 batch
    Given a structurally faithful anonymous MATR HDF5 batch
    And reviewed public-source metadata for batch b2
    When the researcher imports the MATR batch
    Then normalized cycles and a quality report are created
    And the normalized fields have explicit types units and source mappings

  Scenario: Map stable cell and cycle identities
    Given a structurally faithful anonymous MATR HDF5 batch with unsorted source cycles
    And reviewed public-source metadata for batch b2
    When the researcher imports the MATR batch
    Then cell identifiers use the stable batch and source index
    And source cycle indices are preserved in deterministic order

  Scenario: Record source identity and byte hashes
    Given a structurally faithful anonymous MATR HDF5 batch
    And reviewed public-source metadata for batch b2
    When the researcher imports the MATR batch
    Then the source manifest records portable provenance and the input SHA-256
    And every published output hash verifies against its bytes

  Scenario: Reject a missing or ambiguous MATR structure
    Given an HDF5 file without the required MATR summary structure
    And reviewed public-source metadata for batch b2
    When the researcher imports the MATR batch
    Then the MATR import fails with an actionable structure error
    And no partial MATR output is left behind

  Scenario: Reject inconsistent or physically invalid summary vectors
    Given a MATR HDF5 batch with invalid cycle summary values
    And reviewed public-source metadata for batch b2
    When the researcher imports the MATR batch
    Then the MATR import fails with an actionable value error
    And no partial MATR output is left behind

  Scenario: Reject duplicate cell-cycle identities
    Given a MATR HDF5 batch with a repeated source cycle index
    And reviewed public-source metadata for batch b2
    When the researcher imports the MATR batch
    Then the MATR import fails with an actionable duplicate error
    And no partial MATR output is left behind

  Scenario: Reproduce identical normalized evidence
    Given two byte-identical anonymous MATR HDF5 batches
    And reviewed public-source metadata for batch b2
    When the researcher imports both MATR batches independently
    Then their normalized CSV and source manifest fingerprints are identical

  Scenario: Import MATR through the command line
    Given a structurally faithful anonymous MATR HDF5 batch for the CLI
    When the researcher runs the MATR import command
    Then the CLI reports the imported cell cycle and fingerprint identity
    And the CLI output bundle passes the same provenance checks
