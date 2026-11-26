#!/usr/bin/env bash
set -euo pipefail

python -c "
from hermetic_agent.config.settings import get_settings
from hermetic_agent.store.object.minio_client import MinioClient

settings = get_settings()
MinioClient(settings).ensure_bucket(settings.minio_bucket_skills)
print('[ensure_bucket] bucket ready:', settings.minio_bucket_skills)
"

exec python -m hermetic_agent.main
