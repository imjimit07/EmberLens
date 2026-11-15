"""Streaming import of user-downloaded historical NASA FIRMS archives.

Archive CSVs contain *positive detections only*. Loading a CSV does not establish
that any date/region was fully observed; this module never marks query coverage.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import sqlite3
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from backend.core import SOURCES, iter_firms_csv

INSERT_DETECTIONS = """INSERT OR IGNORE INTO detections
    (fingerprint,sensor,source,acq_date,acq_time,latitude,longitude,satellite,confidence,frp,version,simulated)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""
MAX_ZIP_MEMBER_BYTES = 600 * 1024 * 1024


def import_archive_stream(db: sqlite3.Connection, stream, source: str, filename: str,
                          *, sha256: str | None = None, chunk_size: int = 4000) -> dict:
    """Atomic, bounded-RAM import; duplicate rows are ignored across files/reruns."""
    if source not in SOURCES:
        raise ValueError('Unsupported source')
    if chunk_size < 1:
        raise ValueError('chunk_size must be positive')
    stats = {'rows_seen': 0, 'rows_accepted': 0, 'rows_invalid': 0,
             'first_date': None, 'last_date': None}
    added = 0
    batch = []
    # sqlite connect() defaults to implicit transactions. The whole archive member
    # is one transaction, so a corrupted/truncated CSV cannot leave partial rows.
    try:
        with db:
            for row in iter_firms_csv(stream, source, stats):
                batch.append(row)
                if len(batch) >= chunk_size:
                    cursor = db.executemany(INSERT_DETECTIONS, batch)
                    added += max(cursor.rowcount, 0)
                    batch.clear()
            if stats['rows_seen'] and not stats['rows_accepted']:
                raise ValueError('No valid detections in archive CSV')
            if batch:
                cursor = db.executemany(INSERT_DETECTIONS, batch)
                added += max(cursor.rowcount, 0)
            db.execute("""INSERT INTO archive_manifests
                (source,filename,sha256,rows_seen,rows_invalid,rows_accepted,rows_added,first_date,last_date)
                VALUES(?,?,?,?,?,?,?,?,?)""", (
                source, filename[:512], sha256, stats['rows_seen'], stats['rows_invalid'],
                stats['rows_accepted'], added, stats['first_date'], stats['last_date']))
    except (csv.Error, UnicodeError) as e:
        raise ValueError(f'Malformed or undecodable archive CSV: {e}') from e
    return {**stats, 'rows_added': added, 'source': source, 'filename': filename,
            'sha256': sha256, 'coverage_claim': 'positive_detections_only'}


def sha256_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as fh:
        for piece in iter(lambda: fh.read(1024 * 1024), b''):
            sha.update(piece)
    return sha.hexdigest()


def iter_archive_members(path: Path) -> Iterator[tuple[str, BinaryIO]]:
    """Each yielded stream is only valid until the next iteration; use `with`."""
    name = path.name.lower()
    if name.endswith('.csv'):
        with path.open('rb') as fh:
            yield path.name, fh
    elif name.endswith('.csv.gz') or name.endswith('.gz'):
        with gzip.open(path, 'rb') as fh:
            yield path.name, fh
    elif name.endswith('.zip'):
        with zipfile.ZipFile(path) as archive:
            members = [x for x in archive.infolist() if not x.is_dir() and x.filename.lower().endswith('.csv')]
            if not members:
                raise ValueError(f'{path}: ZIP has no CSV entries')
            if len(members) > 100:
                raise ValueError('ZIP contains too many CSV entries (max 100)')
            for member in members:
                if member.file_size > MAX_ZIP_MEMBER_BYTES:
                    raise ValueError(f'ZIP member exceeds 600 MB: {member.filename}')
                with archive.open(member, 'r') as fh:
                    yield f'{path.name}!{member.filename}', fh
    else:
        raise ValueError(f'Unsupported archive file: {path}')


def import_archive_file(db: sqlite3.Connection, path: Path, source: str) -> list[dict]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    file_hash = sha256_file(path)
    results = []
    for member_name, binary_stream in iter_archive_members(path):
        with io.TextIOWrapper(binary_stream, encoding='utf-8-sig', newline='') as txt:
            results.append(import_archive_stream(db, txt, source, member_name,
                                                sha256=hashlib.sha256((file_hash + '|' + member_name).encode()).hexdigest()))
    return results


def discover_archives(input_path: Path) -> list[Path]:
    path = Path(input_path)
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(path)
    return sorted(p for p in path.rglob('*') if p.is_file() and
                  (p.name.lower().endswith('.csv') or p.name.lower().endswith('.csv.gz')
                   or p.name.lower().endswith('.zip')))
