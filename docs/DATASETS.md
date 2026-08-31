# Dataset Sources and Local Acquisition

This repository contains source code, anonymous synthetic fixtures, and dataset adapters. It does
not redistribute third-party battery datasets. Public download access is not treated as a license
or as permission to redistribute data.

## MATR battery dataset

FR-14 targets the MATLAB v7.3/HDF5 files published for Severson et al., *Data-driven prediction of
battery cycle life before capacity degradation* (Nature Energy, 2019).

Reviewed source chain:

- author-maintained code and format description:
  <https://github.com/rdbraatz/data-driven-prediction-of-battery-cycle-life-before-capacity-degradation>;
- official data landing page: <https://data.matr.io/1/>;
- paper DOI: <https://doi.org/10.1038/s41560-019-0356-8>;
- representative local verification file: `2017-06-30_batchdata_updated_struct_errorcorrect.mat`;
- exact official file endpoint:
  <https://data.matr.io/1/api/v1/file/5c86bf13fa2ede00015ddd82/download>;
- expected source size reported by the official endpoint: 2,007,331,155 bytes.
- locally verified SHA-256:
  `63ab200d09ecb237fee5ef3a5c5db76e3212e3206a0bd92f769e1427fed338b8`.

The author parser and the acquired corrected batch both use `h5py` and a `batch/summary`
object-reference array. Older parser code shows `cycle` and `QDischarge` stored behind another
object reference. The acquired 2017-06-30 corrected batch stores both fields directly as `float64`
arrays. FR-14 supports these two explicitly observed, semantically equivalent layouts, records the
observed storage mode, and rejects mixed or unknown representations.

No explicit dataset redistribution license was established from the reviewed source pages.
Accordingly, the local usage status recorded by the example is
`public-access-research-no-redistribution-claim`. Users remain responsible for reviewing the
upstream terms and their intended use.

## Local-only acquisition and import

Place the exact upstream bytes under ignored `data/raw/MATR/`. Never commit the file or generated
normalized data. After independently verifying its source and SHA-256, run:

```powershell
uv run battery-health import-matr `
  --input data/raw/MATR/2017-06-30_batchdata_updated_struct_errorcorrect.mat `
  --output data/processed/MATR/b2-import `
  --batch-id b2 `
  --source-url https://data.matr.io/1/api/v1/file/5c86bf13fa2ede00015ddd82/download `
  --acquired-on 2026-08-31 `
  --usage-terms-status public-access-research-no-redistribution-claim
```

The new output directory contains:

- `cycles.csv`: `cell_id`, `cycle_index`, and `capacity_ah`, deterministically ordered;
- `quality_report.json`: integrity counts and validation status;
- `source_manifest.json`: portable source metadata, input/output SHA-256 values, exact field and
  unit mapping, import statistics, limitations, and a canonical import fingerprint.

The import is a data-integrity operation. It does not create SOH/RUL labels, train a model, prove
generalization, grant redistribution rights, or establish BMS readiness.

## Recorded representative-batch smoke validation

On 2026-08-31, the exact command above exited 0 in 4.153 seconds and recorded:

- source bytes: 2,007,331,155;
- storage layout: `direct_numeric_dataset` for both summary vectors;
- cells: 48;
- normalized rows/cycles: 24,920;
- columns: `cell_id` (string, dimensionless), `cycle_index` (int64, cycle), and `capacity_ah`
  (float64, Ah);
- `cycles.csv` SHA-256:
  `ab9028e35effbd6a6afaf2f9ecb537fa6aeefa728144e5d8ffe09b99785be773`;
- `quality_report.json` SHA-256:
  `b716f5c0362f828326b16d17313c2d93a4e1905223a3224954a7636598cd2da8`;
- `source_manifest.json` SHA-256:
  `bf1d4d9c6ad6dd4670b3330113c7aa1c66d4e24296c3da57057237b373ede19f`;
- import fingerprint:
  `93e8dac89c310bd329de0957222ac802d542713476637d8b222e0f976bb3df05`.

A second independent output directory produced the same three output SHA-256 values. The raw file
and both output directories remained ignored and untracked.
