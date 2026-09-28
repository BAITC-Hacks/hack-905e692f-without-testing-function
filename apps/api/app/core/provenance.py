from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PROFILE = ROOT / "docs/data_profile.json"


def data_sources(metadata: dict[str, object] | None = None) -> list[dict[str, object]]:
    profiles = json.loads(PROFILE.read_text(encoding="utf-8")) if PROFILE.exists() else []
    result = []
    for item in profiles:
        path = ROOT / item["file"]
        is_training_source = metadata and path.name == metadata.get("source_file")
        status = "available" if is_training_source else "inventory_only"
        freshness = "Проверенная offline ingestion" if is_training_source else "Не входит в текущую витрину"
        result.append(
            {
                "id": path.stem,
                "source": f"Файловая выгрузка: {path.stem}",
                "dataset": path.name,
                "status": status,
                "records_processed": metadata.get("raw_records_used") if is_training_source else item.get("rows"),
                "last_successful_ingestion": metadata.get("created_at") if is_training_source else None,
                "dataset_period": metadata.get("date_range") if is_training_source else None,
                "schema_version": "CSV columns: " + ", ".join(item.get("columns", [])),
                "freshness": freshness,
                "validation_status": "validated" if is_training_source else "inventory_only",
                "warnings": (["Исключён из production pipeline: синтетический набор"] if "synthetic" in path.name else ["Пакетная выгрузка / real-time integration не подключена"]),
            }
        )
    return result


def data_health(sources: list[dict[str, object]]) -> dict[str, object]:
    available = sum(source["status"] == "available" for source in sources)
    return {
        "status": "healthy" if available == len(sources) else "degraded",
        "available_sources": available,
        "total_sources": len(sources),
        "real_time_integration": False,
        "message": "Локальные пакетные выгрузки доступны" if available else "Источники данных недоступны",
    }
