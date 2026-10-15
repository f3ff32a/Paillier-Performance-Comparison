#!/usr/bin/env python3
"""Summarize validated configurations, retaining failed statuses with blank metrics."""
import argparse
from pathlib import Path
import statistics
from serial_experiment_utils import PHASES, atomic_csv, load_json, read_valid_csv, sha256

FIELDS = [
    'series', 'bits', 'mc', 'status', 'n', 'csv_sum_median_s', 'csv_sum_cv_pct',
    'wall_median_s', 'wall_cv_pct', 'wall_per_message_ms', 'messages_per_second',
    'commit_median_s', 'prove_median_s', 'verify_median_s',
    'proof_per_message_median_bytes', 'csv_source', 'reason',
]


def cv(values):
    if len(values) < 2 or statistics.mean(values) == 0:
        return ''
    return statistics.stdev(values) / statistics.mean(values) * 100


def summarize_directory(directory, output=None):
    directory = Path(directory)
    summaries = []
    for path in sorted((directory / '.runs').glob('*/metadata.json')):
        meta = load_json(path)
        if (not isinstance(meta, dict) or not isinstance(meta.get('parameters'), dict) or
                meta.get('series') not in ('long512', 'grid', 'smoke') or not isinstance(meta.get('status'), str)):
            continue
        params = meta['parameters']
        if not all(isinstance(params.get(key), int) and params[key] > 0 for key in ('bits', 'mc', 'repeat')):
            continue
        row = {key: '' for key in FIELDS}
        row.update(series=meta['series'], bits=params['bits'], mc=params['mc'],
                   status=meta['status'], n=0, reason=meta.get('reason', ''),
                   csv_source=str((path.parent / 'result.csv').relative_to(directory)))
        if meta['status'] == 'PASS':
            _, observations, error = read_valid_csv(path.parent / 'result.csv', params)
            if not error and meta.get('csv_sha256') != sha256(path.parent / 'result.csv'):
                error = 'CSV content differs from recorded successful result'
            if error:
                row.update(status='INVALID_RESULT', reason=error)
            else:
                sums = [sum(float(r[key]) for key in PHASES) for r in observations]
                walls = [float(r['wall_seconds']) for r in observations]
                wall = statistics.median(walls)
                row.update(n=len(observations), csv_sum_median_s=statistics.median(sums),
                    csv_sum_cv_pct=cv(sums), wall_median_s=wall, wall_cv_pct=cv(walls),
                    wall_per_message_ms=wall * 1000 / params['mc'],
                    messages_per_second=params['mc'] / wall if wall else '',
                    commit_median_s=statistics.median(float(r['commit time']) for r in observations),
                    prove_median_s=statistics.median(float(r['prove time']) for r in observations),
                    verify_median_s=statistics.median(float(r['verify time']) for r in observations),
                    proof_per_message_median_bytes=statistics.median(float(r['proof size/message']) for r in observations))
        summaries.append(row)
    summaries.sort(key=lambda r: (r['series'], r['bits'], r['mc']))
    target = Path(output) if output else directory / 'summary.csv'
    atomic_csv(target, FIELDS, summaries)
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='output directory created by run_experiments.py')
    parser.add_argument('--out', type=Path, help='summary CSV path (default DIRECTORY/summary.csv)')
    args = parser.parse_args()
    if not (args.directory / 'manifest.json').is_file():
        parser.error('directory does not contain an experiment manifest.json')
    rows = summarize_directory(args.directory, args.out)
    print(f'Summarized {len(rows)} configurations; failed/missing metrics remain blank.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
