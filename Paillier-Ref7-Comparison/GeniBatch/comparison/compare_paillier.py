#!/usr/bin/env python3
"""Stable entry point for the comparison package runner."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_adapter import main

if __name__ == "__main__":
    raise SystemExit(main())
