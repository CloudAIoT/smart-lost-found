from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "runtime"))
from render_config import render
from publish import build_commands, required


def payload():
    return {"POSTGRES_DB": "smart_lost_found", "POSTGRES_USER": "smart_lost_found",
            "POSTGRES_PASSWORD": 'a$secret#with"quotes' * 3, "S3_BUCKET": "private-bucket", "S3_REGION": "ap-northeast-2"}


def test_raw_config_preserves_special_characters_and_separates_db(tmp_path):
    config = payload()
    render(config, tmp_path)
    backend = (tmp_path / "backend.env").read_text()
    assert f'POSTGRES_PASSWORD={config["POSTGRES_PASSWORD"]}\n' in backend
    assert "S3_BUCKET=" not in (tmp_path / "db.env").read_text()
    assert "LOCAL_DEVELOPMENT=" not in backend


@pytest.mark.parametrize("key,value", [("POSTGRES_PASSWORD", "short"), ("POSTGRES_PASSWORD", "x" * 32 + "\nLOCAL_DEVELOPMENT=true"),
                                       ("POSTGRES_DB", "db; DROP DATABASE"), ("S3_BUCKET", "")])
def test_bad_runtime_config_is_rejected(tmp_path, key, value):
    config = payload()
    config[key] = value
    with pytest.raises(ValueError):
        render(config, tmp_path)
    assert not (tmp_path / "backend.env").exists()


def test_runtime_config_cannot_enable_business_api(tmp_path):
    config = payload()
    config["LOCAL_DEVELOPMENT"] = "true"
    with pytest.raises(ValueError):
        render(config, tmp_path)


def test_ssm_command_contains_no_secret_values():
    image = "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/backend@sha256:" + "a" * 64
    commands = build_commands(image, "ap-northeast-2", "/smart-lost-found/production/config", "a" * 40 + "-1-1", "", "")
    joined = "\n".join(commands)
    assert "POSTGRES_PASSWORD" not in joined
    assert "/smart-lost-found/production/config" in joined
    assert image in joined
    assert "docker cp" in joined


def test_command_arguments_are_shell_quoted():
    commands = build_commands("image", "region", "/path", "release", "example.com", "name'@example.com")
    assert "'\"'\"'" in commands[-1]


def test_invalid_deployment_identifier_reports_name_only(monkeypatch):
    monkeypatch.setenv("EC2_INSTANCE_ID", "secret-invalid-value")
    with pytest.raises(ValueError) as caught:
        required("EC2_INSTANCE_ID", r"i-[0-9a-f]{17}")
    assert "secret-invalid-value" not in str(caught.value)
