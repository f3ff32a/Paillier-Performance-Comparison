#!/usr/bin/env python3
"""End-to-end zero-message regression and input/output contract checks."""
import argparse
import csv
from pathlib import Path
import subprocess
import tempfile
import unittest

parser = argparse.ArgumentParser()
parser.add_argument('--binary', default='build/serial_benchmark')
args, remaining = parser.parse_known_args()
BINARY = str(Path(args.binary).resolve())


class CliCheck(unittest.TestCase):
    def call(self, *args):
        return subprocess.run([BINARY, *map(str, args)], capture_output=True,
                              text=True, timeout=60)

    def test_parameters_reject_parallel_and_invalid_sizes(self):
        for options in [('--threads', 2), ('--backend', 'cuda'), ('--bits', 65),
                        ('--mc', 0), ('--repeat', 0), ('--seed', '-1'),
                        ('--bits', 64, '--slot', 1, '--mc', 256),
                        ('--bits', '512oops'), ('--unknown', 1)]:
            with self.subTest(options=options):
                result = self.call(*options)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('VERIFICATION=PASS', result.stdout)

    def test_zero_aggregate_repeat_and_no_overwrite(self):
        with tempfile.TemporaryDirectory(prefix='serial-cli-') as temp:
            output = Path(temp) / 'nested' / 'one.csv'
            result = self.call('--bits', 64, '--mc', 1, '--range', 128,
                               '--slot', 4, '--batch', 15, '--repeat', 2,
                               '--seed', 20261012, '--csv', output)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('OpenMP=OFF CUDA=OFF NTL_THREADS=1', result.stdout)
            self.assertEqual(result.stdout.count('VERIFICATION=PASS'), 2)
            with output.open(newline='') as stream:
                reader = csv.DictReader(stream)
                rows = list(reader)
                self.assertEqual(len(reader.fieldnames), 42)
                self.assertEqual(reader.fieldnames[0], 'message count')
                self.assertEqual(reader.fieldnames[34], 'amo. proof cost (bits)')
            self.assertEqual(len(rows), 2)
            for index, row in enumerate(rows, 1):
                self.assertEqual(row['verification'], 'PASS')
                self.assertEqual(int(row['run_index']), index)
                self.assertEqual(row['execution_backend'], 'serial')
                self.assertEqual(int(row['ntl_threads']), 1)
                self.assertEqual(int(row['byte length(bit)']), 64)
                self.assertEqual(int(row['range proof count']), 128)
                self.assertGreater(float(row['wall_seconds']), 0)
                stages = sum(float(row[name]) for name in [
                    'encryption time', 'circuit create time', 'value assign time',
                    'commit time', 'prove time', 'verify time', 'attach time-V(s)'])
                self.assertAlmostEqual(stages, float(row['phase1 time-P(s)']) +
                                       float(row['phase2 time-V(s)']), places=10)
            prior = output.read_bytes()
            repeat = self.call('--bits', 64, '--csv', output)
            self.assertNotEqual(repeat.returncode, 0)
            self.assertEqual(output.read_bytes(), prior)

    def test_no_csv_runs_and_reports_verification(self):
        result = self.call('--bits', 64, '--mc', 1, '--range', 8, '--seed', 7)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('VERIFICATION=PASS run=1 WALL_SECONDS=', result.stdout)


if __name__ == '__main__':
    unittest.main(argv=['test_cli.py', *remaining])
