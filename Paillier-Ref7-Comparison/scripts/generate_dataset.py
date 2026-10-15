#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.fixture import generate, write_new


def main():
    parser = argparse.ArgumentParser(description="Generate identical Paillier inputs for both implementations")
    parser.add_argument("--bits", type=int, default=512)
    parser.add_argument("--messages", type=int, default=100)
    parser.add_argument("--profile", choices=("full-domain", "zkp-slots", "small-integers"), default="zkp-slots")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = generate(args.bits, args.messages, args.profile)
    write_new(args.output, data)
    print(f"DATASET=PASS id={data['dataset_id']} bits={args.bits} ciphertexts={args.messages}")


if __name__ == "__main__":
    main()
