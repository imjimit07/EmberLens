-- EmberLens v0.8.2: private temporary bucket for direct-to-Supabase CSV uploads.
-- Supabase Free has a 50 MB maximum global file-size limit; this bucket matches it.
-- Objects are deleted by the API after the CSV has been imported.
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'emberlens-csv-imports',
    'emberlens-csv-imports',
    FALSE,
    50000000,
    ARRAY['text/csv', 'application/vnd.ms-excel', 'application/octet-stream']::text[]
)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    public = FALSE,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;
