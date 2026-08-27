Feature: FR-06 Evaluate SOH predictions

  Scenario: Generate a traceable evaluation report
    Given test data has actual and predicted SOH values
    When the researcher evaluates the predictions
    Then the report contains MAE RMSE MAPE and R-squared
    And the report records test sample and cell counts
    And per-cycle predictions and a comparison plot are saved

  Scenario: Reject an empty test partition
    Given the split manifest contains no test cells
    When the researcher evaluates the predictions
    Then evaluation fails
    And no apparently valid metrics file is generated
