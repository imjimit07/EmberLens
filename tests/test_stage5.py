"""Stage 5 deployment, operations, performance and release acceptance tests."""
from __future__ import annotations

import csv
import importlib
import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core import connect, import_rows, parse_csv

MODIS = """latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,frp,version
30.1,75.1,2025-10-03,0930,Terra,MODIS,82,11,6.1
30.2,75.2,2025-10-04,1020,Aqua,MODIS,73,9,6.1
"""
VIIRS = """latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,frp,version
30.1,75.1,2025-10-03,0930,N,VIIRS,n,11,2.0
"""


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    old_db = os.environ.get('EMBERLENS_DB')
    old_env = os.environ.get('EMBERLENS_ENV')
    monkeypatch.setenv('EMBERLENS_DB', str(tmp_path / 'stage5.sqlite'))
    monkeypatch.setenv('EMBERLENS_ENV', 'development')
    from backend import main
    importlib.reload(main)
    with TestClient(main.app) as client:
        yield main, client, tmp_path / 'stage5.sqlite'
    if old_db is None:
        monkeypatch.delenv('EMBERLENS_DB', raising=False)
    else:
        monkeypatch.setenv('EMBERLENS_DB', old_db)
    if old_env is None:
        monkeypatch.delenv('EMBERLENS_ENV', raising=False)
    else:
        monkeypatch.setenv('EMBERLENS_ENV', old_env)
    importlib.reload(main)


def test_health_readiness_empty_profile_and_security_headers(app_client):
    _, client, _ = app_client
    live = client.get('/api/health/live')
    ready = client.get('/api/health/ready')
    profile = client.get('/api/profile')
    assert live.status_code == 200 and live.json()['version'] == '0.8.6'
    assert ready.status_code == 200 and ready.json()['status'] == 'ready'
    assert profile.status_code == 200
    assert profile.json()['data_mode'] == 'empty'
    assert profile.json()['total_observations'] == 0
    assert ready.headers['x-content-type-options'] == 'nosniff'
    assert ready.headers['x-frame-options'] == 'DENY'
    assert profile.headers['cache-control'] == 'no-store'


def test_profile_and_bounded_filtered_streaming_export(app_client):
    _, client, db_path = app_client
    with connect(db_path) as db:
        rows, stats = parse_csv(MODIS, 'MODIS_SP')
        import_rows(db, rows, 'MODIS_SP', 'modis.csv', stats=stats)
        rows, stats = parse_csv(VIIRS, 'VIIRS_SNPP_SP')
        import_rows(db, rows, 'VIIRS_SNPP_SP', 'viirs.csv', stats=stats)

    profile = client.get('/api/profile').json()
    assert profile['data_mode'] == 'real'
    assert profile['total_observations'] == 3
    assert {x['source'] for x in profile['sources']} == {'MODIS_SP', 'VIIRS_SNPP_SP'}

    response = client.get('/api/export/detections.csv', params={
        'region': 'punjab', 'start': '2025-10-01', 'end': '2025-10-31',
        'sensor': 'MODIS', 'limit': 1,
    })
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/csv')
    assert response.headers['x-data-mode'] == 'real'
    lines = list(csv.DictReader(io.StringIO(response.text)))
    assert len(lines) == 1
    assert lines[0]['source'] == 'MODIS_SP'
    assert client.get('/api/export/detections.csv', params={'limit': 100001}).status_code == 422


def test_request_connections_use_existing_schema_and_indexes(tmp_path):
    path = tmp_path / 'indexed.sqlite'
    with connect(path, initialize=True) as db:
        db.execute("INSERT INTO detections (fingerprint,sensor,source,acq_date,acq_time,latitude,longitude,simulated) VALUES ('x','MODIS','MODIS_SP','2025-01-01','1200',30,75,0)")
    with connect(path, initialize=False) as db:
        plan = [str(row[3]) for row in db.execute("""EXPLAIN QUERY PLAN SELECT * FROM detections
            WHERE simulated=0 AND source='MODIS_SP' AND acq_date BETWEEN '2025-01-01' AND '2025-12-31'""")]
        mode = db.execute('PRAGMA journal_mode').fetchone()[0]
        assert mode.lower() == 'wal'
        assert any('idx_detections_mode_source_date' in item for item in plan), plan
        assert db.execute('PRAGMA busy_timeout').fetchone()[0] >= 15000


def test_gzip_and_static_cache_headers(app_client):
    _, client, _ = app_client
    response = client.get('/api/coverage?start=2025-01-01&end=2025-01-05',
                          headers={'accept-encoding': 'gzip'})
    assert response.status_code == 200
    assert response.headers.get('content-encoding') == 'gzip'
    page = client.get('/')
    assert page.status_code == 200
    assert page.headers.get('cache-control') == 'no-cache'


def test_production_mode_hides_openapi_docs(tmp_path, monkeypatch):
    old_db = os.environ.get('EMBERLENS_DB')
    old_env = os.environ.get('EMBERLENS_ENV')
    monkeypatch.setenv('EMBERLENS_DB', str(tmp_path / 'production.sqlite'))
    monkeypatch.setenv('EMBERLENS_ENV', 'production')
    from backend import main
    importlib.reload(main)
    try:
        client = TestClient(main.app)
        status = client.get('/api/status').json()
        assert status['docs_enabled'] is False
        assert status['admin_required'] is True
        assert status['admin_token_configured'] is False
        assert client.get('/docs').status_code == 404
        assert client.get('/openapi.json').status_code == 404
        assert client.get('/api/health/ready').status_code == 200
        # All write paths fail closed if the production token is not configured.
        assert client.post('/api/import/csv?source=MODIS_SP&filename=test.csv',
                           content=MODIS, headers={'content-type': 'text/csv'}).status_code == 503
        monkeypatch.setenv('EMBERLENS_ADMIN_TOKEN', 'a' * 32)
        assert client.post('/api/import/csv?source=MODIS_SP&filename=test.csv',
                           content=MODIS, headers={'content-type': 'text/csv'}).status_code == 401
        bad = {'X-EmberLens-Admin': 'wrong-token'}
        assert client.post('/api/import/csv?source=MODIS_SP&filename=test.csv',
                           content=MODIS, headers={'content-type': 'text/csv', **bad}).status_code == 401
        good = {'X-EmberLens-Admin': 'a' * 32}
        upload = client.post('/api/import/csv?source=MODIS_SP&filename=test.csv',
                             content=MODIS, headers={'content-type': 'text/csv', **good})
        assert upload.status_code == 200 and upload.json()['rows_added'] == 2
        assert client.post('/api/availability/refresh').status_code == 401
        assert client.post('/api/import/firms', json={'region': 'punjab', 'source': 'MODIS_SP',
                           'start_date': '2025-10-01', 'days': 1}).status_code == 401
        assert client.post('/api/import/firms-range', json={'region': 'punjab', 'source': 'MODIS_SP',
                           'start_date': '2025-10-01', 'end_date': '2025-10-02'}).status_code == 401
    finally:
        if old_db is None:
            monkeypatch.delenv('EMBERLENS_DB', raising=False)
        else:
            monkeypatch.setenv('EMBERLENS_DB', old_db)
        if old_env is None:
            monkeypatch.delenv('EMBERLENS_ENV', raising=False)
        else:
            monkeypatch.setenv('EMBERLENS_ENV', old_env)
        importlib.reload(main)


def test_online_database_backup_and_integrity_check(tmp_path):
    db_path = tmp_path / 'source.sqlite'
    backup_path = tmp_path / 'backups' / 'copy.sqlite'
    with connect(db_path) as db:
        rows, stats = parse_csv(MODIS, 'MODIS_SP')
        import_rows(db, rows, 'MODIS_SP', 'modis.csv', stats=stats)
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run([
        sys.executable, str(root / 'scripts' / 'db_maintenance.py'),
        '--db', str(db_path), '--backup', str(backup_path), '--integrity-check', '--optimize'
    ], cwd=root, capture_output=True, text=True, check=True)
    report = json.loads(proc.stdout)
    assert report['integrity_check'] == 'ok'
    assert backup_path.exists() and backup_path.stat().st_size > 0
    with sqlite3.connect(backup_path) as db:
        assert db.execute('SELECT COUNT(*) FROM detections').fetchone()[0] == 2


def test_query_benchmark_runs_without_importing_application(tmp_path):
    db_path = tmp_path / 'bench.sqlite'
    with connect(db_path) as db:
        rows, stats = parse_csv(MODIS, 'MODIS_SP')
        import_rows(db, rows, 'MODIS_SP', 'modis.csv', stats=stats)
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run([
        sys.executable, str(root / 'scripts' / 'benchmark_queries.py'),
        '--db', str(db_path), '--start', '2025-01-01', '--end', '2025-12-31', '--runs', '2'
    ], cwd=root, capture_output=True, text=True, check=True)
    report = json.loads(proc.stdout)
    assert report['dataset_rows'] == 2
    assert report['runs'] == 2
    assert report['median_ms'] >= 0


def test_request_database_context_closes_connection(app_client, monkeypatch):
    main, _, _ = app_client
    captured = []
    original = main.connect

    def tracked_connect(*args, **kwargs):
        db = original(*args, **kwargs)
        captured.append(db)
        return db

    monkeypatch.setattr(main, 'connect', tracked_connect)
    with main.open_db() as db:
        db.execute('SELECT 1').fetchone()
    assert captured
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        captured[-1].execute('SELECT 1')
