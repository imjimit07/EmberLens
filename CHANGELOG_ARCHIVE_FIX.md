# EmberLens archive-based analysis fixes

- Harmonization Lab now renders observed MODIS and VIIRS spatial-weekly series for the existing `archive_exploratory` backend response, including the descriptive median ratio and observed-week table.
- The archive fallback is explicitly **not** retrieval-verified calibration or holdout validation.
- Seasonal Signal Explorer defaults to `archive_positive_only` evidence mode.
- Weekly selections work even when the comparison sensor has no positive detections.
- Updated the Stage 3 test to assert the existing archive-positive fallback behavior.
- Archive-only results still depend on real historical standard-processing FIRMS detections being present in the configured database. Missing weeks are unknown, not zero.
