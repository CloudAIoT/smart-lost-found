"""Validate Compose without reading or printing any real deployment secret."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from runtime.render_config import render

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    render({"POSTGRES_DB": "test_db", "POSTGRES_USER": "test_user", "POSTGRES_PASSWORD": "test-only-not-a-secret-" * 2,
            "S3_BUCKET": "test-bucket", "S3_REGION": "ap-northeast-2"}, root)
    shutil.copyfile(Path(__file__).parent / "runtime/compose.yaml", root / "compose.yaml")
    env = {**os.environ, "BACKEND_IMAGE": "smart-lost-found:ci", "COMPOSE_DISABLE_ENV_FILE": "1"}
    subprocess.run(["docker", "compose", "-f", str(root / "compose.yaml"), "config", "--quiet"], env=env, check=True)
print("Production Compose configuration validated with test-only settings.")
