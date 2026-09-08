"""Explicit deployment/development preparation. The server never imports this script."""

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

import httpx


def prepare(destination: Path, *, with_example: bool) -> None:
    root = Path(__file__).resolve().parents[1]
    artifacts = json.loads((root / "app/inference/artifacts.json").read_text())
    destination.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        for name, artifact in artifacts.items():
            if name == "fig1.jpg" and not with_example:
                continue
            target = destination / name
            if target.is_file():
                with target.open("rb") as source:
                    if hashlib.file_digest(source, "sha256").hexdigest() == artifact["sha256"]:
                        print(f"Verified {name}")
                        continue
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=destination, delete=False) as output:
                    temporary = Path(output.name)
                    digest = hashlib.sha256()
                    size = 0
                    with client.stream("GET", artifact["url"]) as response:
                        response.raise_for_status()
                        for block in response.iter_bytes(1024 * 1024):
                            size += len(block)
                            if size > artifact["bytes"]:
                                raise RuntimeError(f"Artifact too large: {name}")
                            digest.update(block)
                            output.write(block)
                if size != artifact["bytes"] or digest.hexdigest() != artifact["sha256"]:
                    raise RuntimeError(f"Artifact integrity check failed: {name}")
                os.replace(temporary, target)
                print(f"Prepared and verified {name}")
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(".models"))
    parser.add_argument(
        "--with-example", action="store_true", help="Download the author's test image"
    )
    args = parser.parse_args()
    prepare(args.directory, with_example=args.with_example)
