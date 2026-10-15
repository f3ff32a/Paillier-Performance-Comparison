#!/usr/bin/env python3
"""Run serial experiments sequentially with verified resume and explicit failure states."""
import argparse
import csv
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time
from serial_experiment_utils import (
    SERIAL_ENV, atomic_bytes, atomic_csv, atomic_json, job_key, load_json,
    read_valid_csv, sha256, valid_resume,
)
from summarize import summarize_directory

ROOT = Path(__file__).resolve().parent.parent
MC_LONG = [1, 10, 20, 40, 80, 100, 200, 400, 800, 1000, 2000]
MC_GRID = MC_LONG[:8]
STATUS_FIELDS = ['series', 'bits', 'mc', 'repeat', 'status', 'returncode', 'reason', 'csv', 'log', 'updated_utc']


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def stamp():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S-%fZ')


def numbers(tokens):
    values = []
    for token in tokens:
        for part in token.split(','):
            if not part.isascii() or not part.isdecimal() or int(part) <= 0:
                raise argparse.ArgumentTypeError('lists require positive decimal integers')
            values.append(int(part))
    if len(set(values)) != len(values):
        raise argparse.ArgumentTypeError('duplicate parameter value')
    return sorted(values)


def terminate_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    # Also remove a descendant that ignored SIGTERM after its parent exited.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def runtime_identity(binary, build_info):
    """Fingerprint actual resolved libraries, as well as recorded build metadata."""
    libraries = {}
    missing = []
    try:
        result = subprocess.run(['ldd', str(binary)], capture_output=True, text=True,
                                timeout=10, check=False)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if 'not found' in line:
                    missing.append(line.strip())
                for value in re.findall(r'(/[^\s]+)', line):
                    path = Path(value).resolve()
                    if path.is_file():
                        libraries[str(path)] = sha256(path)
    except (OSError, subprocess.TimeoutExpired):
        # An executable mock script is not a dynamically linked ELF program.
        # Real ELF runs record whether dependency inspection was available.
        result = None
    info_hash = sha256(build_info) if build_info.is_file() else None
    identity = dict(binary_sha256=sha256(binary), build_info_sha256=info_hash,
                    library_fingerprints=libraries, missing_libraries=sorted(missing),
                    dependency_inspection_returncode=result.returncode if result else None)
    # Build-info timestamps are useful provenance but may change after an
    # otherwise identical make. Resume follows actual executable/libraries.
    fingerprinted = {key: value for key, value in identity.items() if key != 'build_info_sha256'}
    digest = hashlib.sha256(json.dumps(fingerprinted, sort_keys=True).encode()).hexdigest()
    return identity, digest


def configurations(args):
    mcs = numbers(args.mc) if args.mc else None
    bits = numbers(args.bits) if args.bits else None
    if bits and any(bit < 64 or bit % 8 for bit in bits):
        raise ValueError('bits must be a multiple of 8 and at least 64')
    if args.suite == 'long512' and bits not in (None, [512]):
        raise ValueError('long512 has fixed --bits 512; use grid for custom lengths')
    jobs = []
    if args.suite in ('long512', 'both'):
        jobs.extend(('long512', 512, mc) for mc in (mcs or MC_LONG))
    if args.suite in ('grid', 'both'):
        jobs.extend(('grid', bit, mc) for bit in (bits or [256, 512, 1024, 2048]) for mc in (mcs or MC_GRID))
    if args.suite == 'smoke':
        jobs.extend(('smoke', bit, mc) for bit in (bits or [64]) for mc in (mcs or [1]))
    return jobs


def stored_metadata(out):
    """Repair malformed state into explicit FAIL without losing its original bytes."""
    manifest = load_json(out / 'manifest.json')
    entries = []
    for path in sorted((out / '.runs').glob('*/metadata.json')):
        match = re.fullmatch(r'(long512|grid|smoke)-(\d+)bit-mc(\d+)', path.parent.name)
        if not match:
            continue
        series, bits, mc = match.groups()
        expected = dict(bits=int(bits), mc=int(mc), **manifest['protocol_parameters'])
        meta = load_json(path)
        if (not isinstance(meta, dict) or meta.get('series') != series or
                meta.get('parameters') != expected or not isinstance(meta.get('updated_utc'), str) or
                meta.get('status') not in ('PASS', 'FAIL', 'TIMEOUT', 'INTERRUPTED', 'RUNNING') or
                meta.get('binary_sha256') != manifest['binary_sha256']):
            atomic_bytes(path.parent / ('metadata.invalid-' + stamp() + '.json'), path.read_bytes())
            meta = dict(series=series, parameters=expected, binary_sha256=manifest['binary_sha256'],
                        status='FAIL', reason='stored metadata is malformed or does not match manifest parameters',
                        updated_utc=now())
            atomic_json(path, meta)
        entries.append((path, meta))
    return entries


def write_statuses(out):
    rows = []
    for file, meta in stored_metadata(out):
        params = meta['parameters']
        rows.append(dict(series=meta['series'], bits=params['bits'], mc=params['mc'], repeat=params['repeat'],
                         status=meta['status'], returncode=meta.get('returncode', ''), reason=meta.get('reason', ''),
                         csv=str((file.parent / 'result.csv').relative_to(out)),
                         log=str((file.parent / 'run.log').relative_to(out)), updated_utc=meta['updated_utc']))
    rows.sort(key=lambda r: (r['series'], r['bits'], r['mc']))
    atomic_csv(out / 'statuses.csv', STATUS_FIELDS, rows)


def rebuild_outputs(out):
    """Only validated PASS configurations enter public CSVs; logs include all attempts."""
    manifest = load_json(out / 'manifest.json')
    layout = manifest.get('output_layout', 'legacy')
    buckets = {}
    for path, meta in stored_metadata(out):
        params = meta['parameters']; series = meta['series']; bit = params['bits']; mc = params['mc']
        if layout == 'by-bit' and series == 'grid':
            # One CSV/log per bit length, including 2048 bit, directly in --out.
            destination = out
            stem = f'serial_{bit}bit'
        else:
            destination = out / series
            if series == 'grid' and bit == 2048:
                destination /= '2048bit'
            stem = f'serial_{bit}bit' if series == 'grid' and bit != 2048 else f'serial_{bit}bit_mc{mc}'
        bucket = buckets.setdefault(destination / stem, [])
        bucket.append((path, meta))
    for stem, entries in buckets.items():
        entries.sort(key=lambda item: item[1]['parameters']['mc'])
        headers = None; merged = []; logs = []
        for path, meta in entries:
            params = meta['parameters']
            fields = None; rows = []; error = None
            if meta['status'] == 'PASS':
                fields, rows, error = read_valid_csv(path.parent / 'result.csv', params)
                if not error and meta.get('csv_sha256') != sha256(path.parent / 'result.csv'):
                    error = 'CSV content differs from recorded successful result'
                if error:
                    meta.update(status='FAIL', original_status='PASS', reason='stored CSV is invalid: ' + error,
                                updated_utc=now())
                    atomic_json(path, meta)
            logs.append(f"\nCONFIGURATION {meta['series']} bits={params['bits']} mc={params['mc']} status={meta['status']}\n".encode())
            log_path = path.parent / 'run.log'
            if log_path.exists():
                logs.append(log_path.read_bytes())
            if meta['status'] != 'PASS':
                continue
            if headers is not None and headers != fields:
                raise ValueError('inconsistent CSV header within merged length: ' + str(stem))
            headers = fields; merged.extend(rows)
        if merged:
            atomic_csv(stem.with_suffix('.csv'), headers, merged)
        else:
            stem.with_suffix('.csv').unlink(missing_ok=True)
        atomic_bytes(stem.with_suffix('.log'), b''.join(logs))
    write_statuses(out)
    summarize_directory(out)


def run_job(out, series, params, binary, binary_hash, timeout, resume):
    state = out / '.runs' / job_key(series, params['bits'], params['mc'])
    metadata_path = state / 'metadata.json'
    previous = load_json(metadata_path)
    if resume and valid_resume(previous, params, binary_hash, state):
        print(f"RESUME PASS {series} bits={params['bits']} mc={params['mc']}", flush=True)
        return 'PASS'
    if state.exists() and any(state.iterdir()):
        archive = out / '.attempts' / (state.name + '-' + stamp())
        archive.parent.mkdir(parents=True, exist_ok=True)
        state.rename(archive)
    state.mkdir(parents=True, exist_ok=True)
    raw_csv = state / 'result.csv'; log_path = state / 'run.log'
    command = [str(binary), '--bits', str(params['bits']), '--mc', str(params['mc']),
               '--range', str(params['range']), '--slot', str(params['slot']),
               '--batch', str(params['batch']), '--repeat', str(params['repeat']), '--csv', str(raw_csv)]
    if params['seed'] is not None:
        command.extend(['--seed', params['seed']])
    meta = dict(series=series, parameters=params, binary_sha256=binary_hash, status='RUNNING',
                command=command, timeout_seconds=timeout, started_utc=now(), updated_utc=now(), reason='')
    atomic_json(metadata_path, meta)
    environment = os.environ.copy(); environment.update(SERIAL_ENV)
    interrupted = False; process = None; started = time.monotonic()
    print(f"RUN {series} bits={params['bits']} mc={params['mc']} repeat={params['repeat']}", flush=True)
    with log_path.open('wb') as log:
        try:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       cwd=ROOT, env=environment, start_new_session=True)
            returncode = process.wait(timeout=timeout)
            terminate_group(process)
            meta['returncode'] = returncode
            headers, rows, error = read_valid_csv(raw_csv, params)
            transcript = log_path.read_text(encoding='utf-8', errors='replace')
            passes = len(re.findall(r'VERIFICATION=PASS\b', transcript))
            fails = len(re.findall(r'VERIFICATION=FAIL\b', transcript))
            if returncode != 0:
                error = f'child exited with code {returncode}'
            elif passes != params['repeat'] or fails:
                error = f'stdout verification mismatch: PASS={passes}, FAIL={fails}'
            meta.update(status='FAIL' if error else 'PASS', reason=error or '', csv_rows=len(rows))
            if not error:
                meta['csv_sha256'] = sha256(raw_csv)
        except subprocess.TimeoutExpired:
            terminate_group(process)
            meta.update(status='TIMEOUT', reason=f'per-configuration timeout ({timeout} seconds)', returncode=process.returncode)
        except KeyboardInterrupt:
            if process is not None:
                terminate_group(process)
            meta.update(status='INTERRUPTED', reason='interrupted by Ctrl-C', returncode=process.returncode if process else None)
            interrupted = True
        except OSError as error:
            if process is not None:
                terminate_group(process)
            meta.update(status='FAIL', reason=str(error), returncode=process.returncode if process else None)
    meta.update(updated_utc=now(), elapsed_process_seconds=time.monotonic() - started)
    atomic_json(metadata_path, meta)
    rebuild_outputs(out)
    print(f"{meta['status']} {series} bits={params['bits']} mc={params['mc']} {meta['reason']}", flush=True)
    if interrupted:
        raise KeyboardInterrupt
    return meta['status']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=['long512', 'grid', 'both', 'smoke'], default='smoke')
    parser.add_argument('--bits', nargs='+', help='custom lengths, separated by spaces or commas')
    parser.add_argument('--mc', nargs='+', help='custom message counts, separated by spaces or commas')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--out', type=Path, help='default ROOT/results/serial-UTC-timestamp')
    parser.add_argument('--layout', choices=['legacy', 'by-bit'], default='legacy',
                        help='by-bit: grid only; merge all MCs, including 2048 bit, directly into --out/serial_Xbit.csv and .log')
    parser.add_argument('--binary', type=Path, default=ROOT / 'build/serial_benchmark')
    parser.add_argument('--timeout', type=float, help='optional timeout per configuration; default unlimited')
    parser.add_argument('--seed', help='nonnegative decimal seed; omitted means fresh randomness')
    parser.add_argument('--resume', action='store_true')
    stop = parser.add_mutually_exclusive_group()
    stop.add_argument('--keep-going', dest='keep_going', action='store_true', default=True)
    stop.add_argument('--no-keep-going', '--stop-on-error', dest='keep_going', action='store_false')
    args = parser.parse_args(argv)
    lock_stream = None
    try:
        if args.layout == 'by-bit' and args.suite != 'grid':
            raise ValueError('--layout by-bit requires --suite grid')
        if args.repeat < 1 or (args.timeout is not None and (args.timeout <= 0 or not math.isfinite(args.timeout))):
            raise ValueError('repeat and optional timeout must be positive')
        if args.seed is not None:
            if not args.seed.isascii() or not args.seed.isdecimal() or len(args.seed) > 4096:
                raise ValueError('seed must be a nonnegative ASCII decimal integer (up to 4096 digits)')
            args.seed = str(int(args.seed))
        jobs = configurations(args)
        binary = args.binary.expanduser().resolve()
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError('benchmark executable not found or not executable: ' + str(binary))
        binary_hash = sha256(binary)
        build_info = binary.parent / 'build-info.json'
        runtime, runtime_hash = runtime_identity(binary, build_info)
        out = (args.out or ROOT / 'results' / ('serial-' + stamp())).expanduser().resolve()
        if out.exists() and not out.is_dir():
            raise ValueError('output path exists and is not a directory')
        out.mkdir(parents=True, exist_ok=True)
        lock_stream = (out / '.experiment.lock').open('a+')
        try:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another experiment is already running in this --out directory')
        nonempty = any(path.name != '.experiment.lock' for path in out.iterdir())
        if nonempty and not args.resume:
            raise ValueError('output directory is not empty; use --resume or a new --out')
        previous = load_json(out / 'manifest.json')
        protocol = dict(range=128, slot=4, batch=15, repeat=args.repeat, seed=args.seed)
        if args.resume and nonempty:
            if (not previous or previous.get('protocol_parameters') != protocol or
                    previous.get('binary_sha256') != binary_hash or previous.get('runtime_fingerprint') != runtime_hash or
                    previous.get('output_layout', 'legacy') != args.layout):
                raise ValueError('resume manifest parameters, output layout, executable or runtime libraries differ; choose a new --out')
        manifest = previous or dict(format_version=1, created_utc=now(), protocol_parameters=protocol,
                                     binary=str(binary), binary_sha256=binary_hash,
                                     runtime_fingerprint=runtime_hash, output_layout=args.layout, invocations=[])
        manifest['invocations'].append(dict(utc=now(), suite=args.suite, jobs=[list(job) for job in jobs], resume=args.resume, timeout=args.timeout, keep_going=args.keep_going))
        atomic_json(out / 'manifest.json', manifest)
        env = dict(recorded_utc=now(), platform=platform.platform(), uname=list(platform.uname()),
                   python=sys.version, cpu_count=os.cpu_count(), child_environment_overrides=SERIAL_ENV,
                   binary=str(binary), binary_sha256=binary_hash,
                   build_info_path=str(build_info), build_info=load_json(build_info),
                   runtime_identity=runtime, runtime_fingerprint=runtime_hash)
        atomic_json(out / 'environment.json', env)
        atomic_json(out / 'build.json', dict(binary=str(binary), runtime_identity=runtime,
                                            runtime_fingerprint=runtime_hash, build_info=load_json(build_info)))
        failed = False
        try:
            for series, bits, mc in jobs:
                parameters = dict(bits=bits, mc=mc, **protocol)
                status = run_job(out, series, parameters, binary, binary_hash, args.timeout, args.resume)
                if status != 'PASS':
                    failed = True
                    if not args.keep_going:
                        break
        except KeyboardInterrupt:
            rebuild_outputs(out)
            print('INTERRUPTED. Saved completed results and interrupted logs.', file=sys.stderr)
            return 130
        rebuild_outputs(out)
        print('Results: ' + str(out))
        return 1 if failed else 0
    except (ValueError, argparse.ArgumentTypeError) as error:
        parser.error(str(error))
    finally:
        if lock_stream is not None:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
            lock_stream.close()


if __name__ == '__main__':
    raise SystemExit(main())
