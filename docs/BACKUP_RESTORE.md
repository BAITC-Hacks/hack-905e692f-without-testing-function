# Backup and restore

Back up three independent classes: application SQLite state, published processed/model versions plus their current pointers, and authorized raw sources. Encrypt backups and copy them off-host.

```bash
mkdir -p backups
docker compose -f docker-compose.prod.yml exec api python -c "import sqlite3; source=sqlite3.connect('/app/state/app.sqlite3'); target=sqlite3.connect('/app/state/backup.sqlite3'); source.backup(target); target.close(); source.close()"
docker compose -f docker-compose.prod.yml cp api:/app/state/backup.sqlite3 backups/app.sqlite3
docker compose -f docker-compose.prod.yml exec api python -c "from pathlib import Path; Path('/app/state/backup.sqlite3').unlink(missing_ok=True)"
tar czf backups/published-analytics.tgz data/processed/current_mart.json data/processed/mart_versions ml/artifacts/current_model.json ml/artifacts/versions
sha256sum backups/app.sqlite3 backups/published-analytics.tgz > backups/SHA256SUMS
```

The command uses SQLite's online backup API rather than copying a live database file. Restore into a new volume/directory, verify checksums and file ownership, run `python -m app.cli status`, then switch services. Never overwrite the only working copy. Raw-source restore must preserve access controls and provenance; rerun ingestion and training after verification.
