#!/usr/bin/env python3
"""Only update a build prerequisite when compiler/options actually change."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--compiler', required=True)
parser.add_argument('--flags', required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
command = shlex.split(args.compiler)
result = subprocess.run(command + ['--version'], capture_output=True, text=True,
                        check=True, timeout=15)
content = json.dumps({'compiler': args.compiler, 'compiler_version': result.stdout,
                      'flags': args.flags}, sort_keys=True, indent=2) + '\n'
if not args.out.is_file() or args.out.read_text() != content:
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temp = args.out.with_name(args.out.name + '.tmp')
    temp.write_text(content)
    temp.replace(args.out)
