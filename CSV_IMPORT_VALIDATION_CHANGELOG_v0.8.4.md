# EmberLens v0.8.4 — FIRMS VIIRS brightness alias validation

## Bug

The v0.8.3 importer treated the generic `brightness` header as MODIS-only. NASA FIRMS documents that VIIRS web services also use `Brightness` as an alias for the VIIRS I-4 brightness temperature, and `Brightness_2` as an alias for VIIRS I-5. This caused valid VIIRS web-service CSVs to fail with `MODIS brightness fields found in a VIIRS import`.

## Fix

- Allow `brightness` in both MODIS and VIIRS imports.
- Recognize `brightness_2` as a VIIRS-specific alias.
- Continue rejecting `bright_t31` in VIIRS imports.
- Reject VIIRS-specific `bright_ti4`, `bright_ti5`, and `brightness_2` fields in MODIS imports.
- Keep row-level `instrument` and VIIRS satellite/platform validation.
- Add regression tests for NASA's VIIRS web-service aliases, VIIRS standard CSV fields, MODIS fields, and cross-sensor rejection.
- Bump app and frontend version to 0.8.4.

## Deployment

Deploy the v0.8.4 code to Vercel and retry the VIIRS import. **No Supabase SQL migration or database reset is required.** The parser change only affects new import attempts; it does not alter already-imported records.

## Scientific/data-quality note

Brightness temperature fields are used here for schema validation only; EmberLens does not currently store or calibrate these fields as part of its harmonization output. The importer preserves the source/platform identity and FRP/date/location fields used by the application.
