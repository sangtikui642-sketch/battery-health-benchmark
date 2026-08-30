Feature: Audit the v0.2.0rc1 open-source release candidate

  Scenario: Enforce release gates on Windows and Ubuntu
    Given the repository is prepared as a v0.2.0rc1 release candidate
    When the release CI policy is inspected
    Then push and pull requests run on Windows and Ubuntu with Python 3.12
    And frozen dependencies tests quality checks typing and package build are enforced

  Scenario: Keep release metadata consistent
    Given the repository is prepared as a v0.2.0rc1 release candidate
    When the package license and citation metadata are inspected
    Then the version license author and repository identity agree
    And release metadata contains no placeholder identity

  Scenario: Publish complete open-source governance files
    Given the repository is prepared as a v0.2.0rc1 release candidate
    When the open-source governance documents are inspected
    Then every required governance document exists without placeholders
    And private vulnerability reporting and external release authorization are explicit

  Scenario: Build artifacts and expose the CLI release surface
    Given the repository is prepared as a v0.2.0rc1 release candidate
    When the researcher builds the package and inspects both CLI help surfaces
    Then one wheel and one source archive carry version 0.2.0rc1
    And the typed package marker and agent commands are distributed
