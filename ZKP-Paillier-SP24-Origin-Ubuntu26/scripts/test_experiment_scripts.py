#!/usr/bin/env python3
"""End-to-end CLI tests using a mock executable, never long real experiments."""
import csv
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parent
RUNNER = SCRIPTS / 'run_experiments.py'

MOCK = r'''
import argparse, csv, json, os, subprocess, sys, time
from pathlib import Path
p=argparse.ArgumentParser()
for name in ('bits','mc','range','slot','batch','repeat'):
 p.add_argument('--'+name,type=int,required=True)
p.add_argument('--csv',required=True);p.add_argument('--seed',default='')
a=p.parse_args()
with open(os.environ['MOCK_CALLS'],'a') as f:f.write(json.dumps(vars(a))+'\n')
if str(a.mc)==os.environ.get('MOCK_SLEEP_MC'):
 child=subprocess.Popen([sys.executable,'-c','import time,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)',a.csv])
 print('MOCK_SLEEP grandchild='+str(child.pid),flush=True)
 time.sleep(60)
fields=['message count','byte length(bit)','message/batch','slot size(byte)','range proof count','byte length(byte)','encryption time','circuit create time','value assign time','commit time','prove time','verify time','attach time-V(s)','proof size/message','wall_seconds','verification','run_index','keygen_seconds','seed','execution_backend','ntl_threads']
with open(a.csv,'w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
 for i in range(1,a.repeat+1):
  status='FAIL' if str(a.mc)==os.environ.get('MOCK_FAIL_MC') and i==a.repeat else 'PASS'
  r={'message count':a.mc,'byte length(bit)':a.bits,'message/batch':a.batch,'slot size(byte)':a.slot,'range proof count':a.range,'byte length(byte)':a.bits//8,'encryption time':i,'circuit create time':0,'value assign time':0,'commit time':.5*i,'prove time':.3*i,'verify time':.2*i,'attach time-V(s)':0,'proof size/message':1000/a.mc,'wall_seconds':3*i,'verification':status,'run_index':i,'keygen_seconds':.1,'seed':a.seed,'execution_backend':'serial','ntl_threads':1}
  w.writerow(r);f.flush()
  print('VERIFICATION='+status+' run='+str(i)+' WALL_SECONDS='+str(3*i),flush=True)
if str(a.mc)==os.environ.get('MOCK_EXIT_MC'):sys.exit(7)
'''


def read_csv(path):
    with Path(path).open(newline='') as stream:
        return list(csv.DictReader(stream))


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.mock = self.root / 'mock_benchmark'
        self.mock.write_text('#!' + sys.executable + '\n' + MOCK)
        self.mock.chmod(0o755)
        self.calls = self.root / 'calls.jsonl'
        self.out = self.root / 'results'
        self.env = dict(os.environ, MOCK_CALLS=str(self.calls))

    def tearDown(self):
        self.temp.cleanup()

    def command(self, *args):
        return [sys.executable, str(RUNNER), '--binary', str(self.mock), '--out', str(self.out), *args]

    def run_cli(self, *args, expected=0, env=None):
        result = subprocess.run(self.command(*args), capture_output=True, text=True,
                                env=env or self.env, timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def call_count(self):
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def test_merge_and_sample_statistics(self):
        self.run_cli('--suite', 'grid', '--bits', '64,128', '--mc', '1', '2')
        self.assertEqual(self.call_count(), 4)
        for bits in [64, 128]:
            rows = read_csv(self.out / 'grid' / f'serial_{bits}bit.csv')
            self.assertEqual(len(rows), 6)
            self.assertEqual([int(r['message count']) for r in rows], [1]*3 + [2]*3)
        summaries = read_csv(self.out / 'summary.csv')
        self.assertEqual(len(summaries), 4)
        self.assertEqual(float(summaries[0]['wall_median_s']), 6)
        self.assertEqual(float(summaries[0]['wall_cv_pct']), 50)
        self.assertEqual(float(summaries[0]['csv_sum_median_s']), 4)
        self.assertEqual(float(summaries[0]['csv_sum_cv_pct']), 50)
        self.assertEqual(float(summaries[0]['proof_per_message_median_bytes']), 1000)
        self.assertTrue((self.out / 'environment.json').exists())
        self.assertTrue((self.out / 'build.json').exists())

    def test_resume_requires_valid_metadata_count_and_pass(self):
        args = ['--suite', 'grid', '--bits', '64', '--mc', '1', '2']
        self.run_cli(*args)
        self.run_cli(*args, '--resume')
        self.assertEqual(self.call_count(), 2)
        state = self.out / '.runs/grid-64bit-mc1'
        csv_path = state / 'result.csv'
        lines = csv_path.read_text().splitlines()
        csv_path.write_text('\n'.join(lines[:-1]) + '\n')
        self.run_cli(*args, '--resume')
        self.assertEqual(self.call_count(), 3)
        meta_path = state / 'metadata.json'
        meta = json.loads(meta_path.read_text());meta['parameters']['repeat'] = 2
        meta_path.write_text(json.dumps(meta))
        self.run_cli(*args, '--resume')
        self.assertEqual(self.call_count(), 4)
        lines = csv_path.read_text().replace(',PASS,', ',FAIL,')
        csv_path.write_text(lines)
        self.run_cli(*args, '--resume')
        self.assertEqual(self.call_count(), 5)
        self.assertEqual(len(list((self.out / '.attempts').iterdir())), 3)
        lines = csv_path.read_text().splitlines()
        lines[-1] += ',unexpected-tail-column'
        csv_path.write_text('\n'.join(lines) + '\n')
        self.run_cli(*args, '--resume')
        self.assertEqual(self.call_count(), 6)
        self.run_cli(*args, '--resume', '--repeat', '2', expected=2)
        self.assertEqual(self.call_count(), 6)

    def test_build_timestamp_change_allows_resume(self):
        info = self.root / 'build-info.json'
        info.write_text('{"generated_utc":"fixture-1","compiler":"fixture","libraries":[]}')
        self.run_cli('--suite', 'smoke')
        info.write_text('{"generated_utc":"fixture-2","compiler":"fixture","libraries":[]}')
        self.run_cli('--suite', 'smoke', '--resume')
        self.assertEqual(self.call_count(), 1)

    def test_protocol_parameters_and_positive_measurements_are_checked(self):
        from serial_experiment_utils import read_valid_csv
        self.run_cli('--suite', 'smoke')
        state = self.out / '.runs/smoke-64bit-mc1'
        path = state / 'result.csv';original = read_csv(path)
        parameters = json.loads((state / 'metadata.json').read_text())['parameters']
        for column, value in [('message/batch', '16'), ('slot size(byte)', '8'),
                              ('range proof count', '127'), ('byte length(byte)', '64'),
                              ('wall_seconds', '0'), ('proof size/message', '0')]:
            changed = [dict(row) for row in original];changed[0][column] = value
            with path.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(changed[0]));writer.writeheader();writer.writerows(changed)
            self.assertIsNotNone(read_valid_csv(path, parameters)[2], column)

    def test_actual_library_change_rejects_resume(self):
        tools = self.root / 'tools';tools.mkdir()
        library = self.root / 'library-fixture.so';library.write_bytes(b'library-version-1')
        ldd = tools / 'ldd'
        ldd.write_text('#!' + sys.executable + '\nprint(' + repr('fixture => ' + str(library) + ' (0x0001)') + ')\n')
        ldd.chmod(0o755)
        env = dict(self.env, PATH=str(tools) + os.pathsep + self.env['PATH'])
        self.run_cli('--suite', 'smoke', env=env)
        library.write_bytes(b'library-version-2')
        self.run_cli('--suite', 'smoke', '--resume', expected=2, env=env)
        self.assertEqual(self.call_count(), 1)

    def test_corruption_outside_requested_resume_configs_does_not_merge(self):
        self.run_cli('--suite', 'grid', '--bits', '64', '--mc', '1', '2', '3')
        corrupt_meta = self.out / '.runs/grid-64bit-mc1/metadata.json'
        corrupt_meta.write_text('{"status":"PASS"}')
        data = self.out / '.runs/grid-64bit-mc3/result.csv'
        rows = read_csv(data)
        rows[0]['encryption time'] = '10000'
        with data.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        self.run_cli('--suite', 'grid', '--bits', '64', '--mc', '2', '--resume')
        self.assertEqual(self.call_count(), 3)
        self.assertEqual([r['status'] for r in read_csv(self.out / 'statuses.csv')], ['FAIL', 'PASS', 'FAIL'])
        self.assertEqual([r['message count'] for r in read_csv(self.out / 'grid/serial_64bit.csv')], ['2']*3)
        self.assertTrue(list(corrupt_meta.parent.glob('metadata.invalid-*.json')))

    def test_ascii_input_and_seed_length_match_cpp(self):
        self.run_cli('--bits', '６４', expected=2)
        self.run_cli('--seed', '١٢٣', expected=2)
        self.run_cli('--seed', '1' * 4097, expected=2)
        self.assertEqual(self.call_count(), 0)

    def test_failed_validation_excluded_and_keep_going(self):
        env = dict(self.env, MOCK_FAIL_MC='2')
        self.run_cli('--suite', 'grid', '--bits', '64', '--mc', '1', '2', '3', expected=1, env=env)
        statuses = read_csv(self.out / 'statuses.csv')
        self.assertEqual([r['status'] for r in statuses], ['PASS', 'FAIL', 'PASS'])
        rows = read_csv(self.out / 'grid/serial_64bit.csv')
        self.assertEqual([int(r['message count']) for r in rows], [1]*3 + [3]*3)
        failed = [r for r in read_csv(self.out / 'summary.csv') if r['status'] == 'FAIL'][0]
        self.assertEqual(failed['n'], '0')
        self.assertEqual(failed['wall_median_s'], '')
        self.assertNotIn('inf', failed['wall_median_s'])
        self.assertTrue((self.out / '.runs/grid-64bit-mc2/result.csv').exists())

    def test_exit_failure_and_stop_on_error(self):
        self.run_cli('--suite', 'grid', '--bits', '64', '--mc', '1', '2', '3',
                     '--no-keep-going', expected=1, env=dict(self.env, MOCK_EXIT_MC='2'))
        self.assertEqual(self.call_count(), 2)
        self.assertEqual([r['status'] for r in read_csv(self.out / 'statuses.csv')], ['PASS', 'FAIL'])

    def test_timeout_kills_child_group_and_continues(self):
        self.run_cli('--suite', 'grid', '--bits', '64', '--mc', '1', '2', '3',
                     '--timeout', '0.5', expected=1, env=dict(self.env, MOCK_SLEEP_MC='2'))
        self.assertEqual([r['status'] for r in read_csv(self.out / 'statuses.csv')], ['PASS', 'TIMEOUT', 'PASS'])
        self.assert_grandchild_stopped(self.out / '.runs/grid-64bit-mc2/run.log')

    def assert_grandchild_stopped(self, log_path):
        import re
        pid = int(re.search(r'grandchild=(\d+)', log_path.read_text()).group(1))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            stat = Path(f'/proc/{pid}/stat')
            try:
                command = Path(f'/proc/{pid}/cmdline').read_bytes()
                state = stat.read_text().split()[2]
            except FileNotFoundError:
                return
            # A PID may be reused by another concurrent test or benchmark.
            if str(log_path.parent / 'result.csv').encode() not in command or state == 'Z':
                return
            time.sleep(.02)
        self.fail('mock grandchild remained running after group termination')

    def test_interrupt_preserves_logs_and_results(self):
        process = subprocess.Popen(self.command('--suite', 'grid', '--bits', '64', '--mc', '1', '2', '3'),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env=dict(self.env, MOCK_SLEEP_MC='2'))
        log = self.out / '.runs/grid-64bit-mc2/run.log'
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if log.exists() and 'MOCK_SLEEP' in log.read_text():
                    break
                if process.poll() is not None:
                    self.fail('runner exited before interruption point')
                time.sleep(.02)
            else:
                self.fail('mock did not enter interruptible run')
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 130, stdout + stderr)
            self.assertEqual([r['status'] for r in read_csv(self.out / 'statuses.csv')], ['PASS', 'INTERRUPTED'])
            self.assertEqual(len(read_csv(self.out / 'grid/serial_64bit.csv')), 3)
            self.assertIn('MOCK_SLEEP', log.read_text())
            self.assert_grandchild_stopped(log)
        finally:
            if process.poll() is None:
                process.kill();process.wait()

    def test_same_output_directory_cannot_run_concurrently(self):
        process = subprocess.Popen(self.command('--suite', 'smoke'), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True,
                                   env=dict(self.env, MOCK_SLEEP_MC='1'))
        log = self.out / '.runs/smoke-64bit-mc1/run.log'
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if log.exists() and 'MOCK_SLEEP' in log.read_text():
                    break
                time.sleep(.02)
            else:
                self.fail('first runner did not acquire its output directory')
            result = self.run_cli('--suite', 'smoke', '--resume', expected=2)
            self.assertIn('already running', result.stderr)
            self.assertEqual(self.call_count(), 1)
            process.send_signal(signal.SIGINT)
            process.communicate(timeout=8)
            self.assertEqual(process.returncode, 130)
            self.assert_grandchild_stopped(log)
            self.run_cli('--suite', 'smoke', '--resume')
            self.assertEqual(self.call_count(), 2)
        finally:
            if process.poll() is None:
                process.kill();process.wait()

    def test_long_and_grid_2048_outputs_are_separate(self):
        self.run_cli('--suite', 'both', '--bits', '512', '2048', '--mc', '1', '--repeat', '1', '--seed', '00042')
        self.assertEqual(self.call_count(), 3)
        self.assertTrue((self.out / 'long512/serial_512bit_mc1.csv').exists())
        self.assertTrue((self.out / 'grid/serial_512bit.csv').exists())
        self.assertTrue((self.out / 'grid/2048bit/serial_2048bit_mc1.csv').exists())
        self.assertEqual([r['seed'] for r in read_csv(self.out / 'long512/serial_512bit_mc1.csv')], ['42'])
        self.assertEqual([r['wall_cv_pct'] for r in read_csv(self.out / 'summary.csv')], ['', '', ''])


if __name__ == '__main__':
    unittest.main(verbosity=2)
