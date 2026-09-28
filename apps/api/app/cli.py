"""Offline operations: ``python -m app.cli ingest|train|status``."""

from __future__ import annotations

import argparse
import json

from app.core.data_pipeline import current_mart, ingest
from app.core.real_data import DataUnavailable, ModelNotReady, load_runtime, train_or_load


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("command", choices=("ingest", "train", "status"))
    parser.add_argument("--force", action="store_true", help="rebuild even when source identity is unchanged")
    arguments = parser.parse_args()
    if arguments.command == "ingest":
        output = ingest(force=arguments.force)
    elif arguments.command == "train":
        state = train_or_load(force_train=arguments.force)
        output = state["metadata"]
    else:
        _, mart = current_mart()
        try:
            runtime = load_runtime()
            model = runtime["metadata"]
            output = {
                "processed_data": {"status": "available", "version": mart["dataset_version"]},
                "model": {"status": "ready", "version": model["model_version"]},
                "dataset_fingerprint": mart["source_fingerprint"],
            }
        except (DataUnavailable, ModelNotReady) as error:
            output = {
                "processed_data": {"status": "available", "version": mart["dataset_version"]},
                "model": {"status": "not_ready", "reason": str(error)},
                "dataset_fingerprint": mart["source_fingerprint"],
            }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
