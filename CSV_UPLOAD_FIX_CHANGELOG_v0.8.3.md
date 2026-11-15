# EmberLens v0.8.3 — signed TUS endpoint fix

## Bug fixed

The v0.8.2 browser flow created a Supabase signed-upload token and correctly sent it in the `x-signature` header, but its TUS endpoint URL targeted the ordinary `/storage/v1/upload/resumable` route. Supabase has a separate signed-resumable route. The mismatch caused Storage to reject the initial upload request with `403 AccessDenied` / `Invalid Compact JWS`.

## Changes

- Changed the TUS endpoint to `/storage/v1/upload/resumable/sign` on the project's direct Storage hostname.
- Kept the signed token in the `x-signature` header.
- Updated endpoint regression tests and application version to 0.8.3.
- Updated deployment troubleshooting instructions.
- Kept the 50 MB bucket limit, private bucket, server-only service-role key, 6 MiB chunks, upload completion/import flow, and 200,000-row parser cap.

## Deployment

Deploy this version to Vercel. No new Supabase SQL migration is required if the v0.8.2 `emberlens-csv-imports` bucket migration has already been applied. Start a fresh upload after deployment so it receives a new signed token. Do not reuse an upload ticket created by the old deployment.

## Verification

Automated tests and static checks are recorded in `CSV_UPLOAD_VERIFICATION_v0.8.3.json`. A live Supabase transfer still requires the user's deployed project and credentials; this package does not claim that a production upload has been tested.
