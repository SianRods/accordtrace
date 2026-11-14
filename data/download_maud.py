"""Download the publisher's MAUD release: uv run python data/download_maud.py."""

import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
CACHE_DIR = DATA_DIR / ".cache" / "huggingface"
os.environ["HF_HOME"] = str(CACHE_DIR)
os.environ["HF_HUB_CACHE"] = str(CACHE_DIR / "hub")
os.environ["HF_XET_CACHE"] = str(CACHE_DIR / "xet")


def main():
    from huggingface_hub import HfApi, snapshot_download

    repo = "theatticusproject/maud"
    target = DATA_DIR / "maud"
    revision = HfApi().dataset_info(repo).sha
    print(f"Downloading {repo} at {revision}", flush=True)
    snapshot_download(
        repo_id=repo,
        repo_type="dataset",
        revision=revision,
        local_dir=target,
        max_workers=4,
    )

    corpus = target / "MAUD_v1"
    contracts = list((corpus / "contracts").glob("*.txt"))
    if not contracts:
        raise RuntimeError("No contract text files found after download.")
    splits = {}
    for split in ("train", "dev", "test"):
        path = corpus / f"MAUD_{split}.csv"
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if not reader.fieldnames:
                raise RuntimeError(f"Missing CSV header: {path}")
            rows = sum(1 for _ in reader)
        if rows == 0:
            raise RuntimeError(f"Empty annotation file: {path}")
        splits[split] = {
            "rows": rows,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    manifest = {
        "repo_id": repo,
        "revision": revision,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "contract_text_files": len(contracts),
        "splits": splits,
    }
    (target / "download_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"Downloaded to {target}")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
