"""Explicit local model training command; never imported by HTTP request handlers."""

from app.core.real_data import train_or_load

if __name__ == "__main__":
    state = train_or_load(force_train=True)
    metadata = state["metadata"]
    print(f"trained {metadata['model_version']} on {metadata['source_fingerprint']}")
    print(metadata["metrics"])
