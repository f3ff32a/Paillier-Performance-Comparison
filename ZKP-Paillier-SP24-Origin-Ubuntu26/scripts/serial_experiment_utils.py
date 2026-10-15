"""Shared, standard-library-only result validation and atomic file writes."""
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path

PHASES = (
    'encryption time', 'circuit create time', 'value assign time',
    'commit time', 'prove time', 'verify time', 'attach time-V(s)',
)
SERIAL_ENV = {
    'OMP_NUM_THREADS': '1', 'OMP_THREAD_LIMIT': '1',
    'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
    'BLIS_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1',
    'NUMEXPR_NUM_THREADS': '1',
}


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + str(os.getpid()))
    try:
        with temporary.open('wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path, data):
    atomic_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2,
                                  allow_nan=False) + '\n').encode('utf-8'))


def atomic_csv(path, headers, rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    atomic_bytes(path, stream.getvalue().encode('utf-8'))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load_json(path):
    try:
        with Path(path).open(encoding='utf-8') as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return None


def read_valid_csv(path, parameters):
    """Return (headers, rows, error); reject partial/failed/non-serial results."""
    try:
        with Path(path).open(newline='', encoding='utf-8-sig') as stream:
            reader = csv.DictReader(stream)
            headers = reader.fieldnames
            rows = list(reader)
        if not headers or len(headers) != len(set(headers)):
            raise ValueError('missing or duplicate CSV header')
        required = set(PHASES) | {
            'message count', 'byte length(bit)', 'proof size/message',
            'message/batch', 'slot size(byte)', 'range proof count', 'byte length(byte)',
            'wall_seconds', 'verification', 'run_index',
            'keygen_seconds', 'seed', 'execution_backend', 'ntl_threads',
        }
        if required - set(headers):
            raise ValueError('missing CSV columns: ' + ', '.join(sorted(required - set(headers))))
        if len(rows) != parameters['repeat']:
            raise ValueError(f"expected {parameters['repeat']} rows, found {len(rows)}")
        for index, row in enumerate(rows, 1):
            if None in row or any(value is None for value in row.values()):
                raise ValueError('malformed CSV row')
            if row['verification'].strip() != 'PASS':
                raise ValueError(f'run {index} is not PASS')
            if int(row['message count']) != parameters['mc'] or int(row['byte length(bit)']) != parameters['bits']:
                raise ValueError('CSV parameters do not match configuration')
            expected = {'message/batch': parameters['batch'], 'slot size(byte)': parameters['slot'],
                        'range proof count': parameters['range'], 'byte length(byte)': parameters['bits'] // 8}
            if any(int(row[key]) != value for key, value in expected.items()):
                raise ValueError('CSV protocol parameters do not match configuration')
            if int(row['run_index']) != index:
                raise ValueError('run_index must be 1..repeat in order')
            if row['execution_backend'].strip() != 'serial' or int(row['ntl_threads']) != 1:
                raise ValueError('result does not declare serial execution with one NTL thread')
            if row['seed'].strip() != (parameters['seed'] or ''):
                raise ValueError('CSV seed does not match configuration')
            for key in (*PHASES, 'wall_seconds', 'keygen_seconds', 'proof size/message'):
                value = float(row[key])
                if not math.isfinite(value) or value < 0:
                    raise ValueError('invalid finite nonnegative measurement: ' + key)
                if key in ('wall_seconds', 'proof size/message') and value <= 0:
                    raise ValueError('measurement must be strictly positive: ' + key)
        return headers, rows, None
    except (OSError, ValueError, TypeError, KeyError) as error:
        return None, [], str(error)


def job_key(series, bits, mc):
    return f'{series}-{bits}bit-mc{mc}'


def valid_resume(metadata, parameters, binary_hash, state_dir):
    if not isinstance(metadata, dict):
        return False
    if metadata.get('status') != 'PASS' or metadata.get('parameters') != parameters:
        return False
    if metadata.get('binary_sha256') != binary_hash:
        return False
    path = Path(state_dir) / 'result.csv'
    headers, rows, error = read_valid_csv(path, parameters)
    return error is None and metadata.get('csv_sha256') == sha256(path)
