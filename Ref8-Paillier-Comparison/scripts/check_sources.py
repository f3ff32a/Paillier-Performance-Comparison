"""Verify untouched vendor files and adapted-source hashes against the manifest."""
from pathlib import Path
import hashlib
import json
import sys

root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/"provenance.json").read_text(encoding="utf-8"))
failed=[]
entries=manifest["vendor_files"]+manifest.get("adapted_files",[])
for entry in entries:
    path=root/entry["path"]
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=entry["sha256"]:
        failed.append(entry["path"])
if failed:
    print("SOURCE_INTEGRITY=FAILED:","\n".join(failed),file=sys.stderr)
    raise SystemExit(1)
print(f"SOURCE_INTEGRITY=PASS ({len(entries)} files)")
