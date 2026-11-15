from backend.postgres import HybridRow, POSTGRES_SCHEMA, adapt_sql


def test_sqlite_insert_ignore_and_placeholders_translate_for_postgres():
    sql = adapt_sql("INSERT OR IGNORE INTO detections (fingerprint, sensor) VALUES (?, ?)")
    assert sql == (
        "INSERT INTO detections (fingerprint, sensor) VALUES (%s, %s) ON CONFLICT DO NOTHING"
    )


def test_hybrid_row_supports_mapping_and_sqlite_positional_access():
    row = HybridRow([("source", "MODIS_SP"), ("count", 12)])
    assert row["source"] == "MODIS_SP"
    assert row[0] == "MODIS_SP"
    assert row[1] == 12
    assert tuple(row) == ("MODIS_SP", 12)
    assert dict(row) == {"source": "MODIS_SP", "count": 12}


def test_postgres_schema_contains_all_application_tables_and_indexes():
    for table in ("detections", "coverage", "query_windows", "product_availability",
                  "archive_manifests", "imports"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in POSTGRES_SCHEMA
    assert "idx_detections_mode_source_date" in POSTGRES_SCHEMA
