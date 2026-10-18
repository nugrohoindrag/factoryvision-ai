from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command

from app.config import settings
from app.db.build_local import alembic_config
from app.db.engine import database_url
from app.db.models import PUBSUB_WRITTEN_TABLES, SCHEMA, TABLES, Base


def load(seed_dir: Path) -> None:
    from google.cloud import bigquery

    command.upgrade(alembic_config(database_url("bigquery")), "head")
    client = bigquery.Client(project=settings.project)
    for table in Base.metadata.sorted_tables:
        if table.name in PUBSUB_WRITTEN_TABLES:
            continue
        job = client.load_table_from_file(
            open(seed_dir / f"{table.name}.csv", "rb"),
            f"{settings.project}.{SCHEMA}.{table.name}",
            job_config=bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.CSV, skip_leading_rows=1, write_disposition="WRITE_TRUNCATE",
                schema=[bigquery.SchemaField(c, t) for c, t in TABLES[table.name]],
            ),
        )
        job.result()
        print(f"loaded {table.name}: {job.output_rows} rows")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", default=str(settings.seed_dir))
    load(Path(ap.parse_args().seed_dir))


if __name__ == "__main__":
    main()
