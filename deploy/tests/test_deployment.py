from pathlib import Path
import re
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
                                       ("POSTGRES_DB", "db; DROP DATABASE"), ("S3_BUCKET", ""),
                                       ("BUSINESS_API_ENABLED", "yes"), ("BUSINESS_API_ENABLED", "true\nLOCAL_DEVELOPMENT=true")])
def test_bad_runtime_config_is_rejected(tmp_path, key, value):
    config = payload()
    config[key] = value
    with pytest.raises(ValueError):
        render(config, tmp_path)
    assert not (tmp_path / "backend.env").exists()


def test_runtime_config_cannot_enable_local_mode(tmp_path):
    config = payload()
    config["LOCAL_DEVELOPMENT"] = "true"
    with pytest.raises(ValueError):
        render(config, tmp_path)


@pytest.mark.parametrize("enabled", [None, "false", "true"])
def test_runtime_api_switch_and_legacy_config(tmp_path, enabled):
    config = payload()
    if enabled is not None:
        config["BUSINESS_API_ENABLED"] = enabled
    render(config, tmp_path)
    assert f'BUSINESS_API_ENABLED={enabled or "false"}\n' in (tmp_path / "backend.env").read_text()
    assert "BUSINESS_API_ENABLED" not in (tmp_path / "db.env").read_text()


def test_fake_device_is_absent_unless_requested(tmp_path):
    config = payload()
    render(config, tmp_path)
    assert "DEV_FAKE_DEVICE" not in (tmp_path / "backend.env").read_text()
    config["DEV_FAKE_DEVICE"] = "true"
    render(config, tmp_path)
    assert "DEV_FAKE_DEVICE=true" in (tmp_path / "backend.env").read_text()


def test_admin_credentials_are_optional_and_only_reach_backend(tmp_path):
    config = payload()
    render(config, tmp_path)
    assert "ADMIN_" not in (tmp_path / "backend.env").read_text()
    config.update(ADMIN_USERNAME="test-operator", ADMIN_PASSWORD='test$with#"quotes')
    render(config, tmp_path)
    backend = (tmp_path / "backend.env").read_text()
    assert f'ADMIN_USERNAME={config["ADMIN_USERNAME"]}\n' in backend
    assert f'ADMIN_PASSWORD={config["ADMIN_PASSWORD"]}\n' in backend
    assert "ADMIN_" not in (tmp_path / "db.env").read_text()


def test_pi_settings_are_optional_and_render_when_given(tmp_path):
    config = payload()
    config["COMMAND_TTL_SECONDS"] = "30"
    config["PI_TIMEOUT_SECONDS"] = "20"
    config["PI_DEVICES"] = '{"kiosk-1":{"capture_url":"http://100.1.1.1:8000","capture_api_key":"ck",' \
                            '"locker_url":"http://100.1.1.2:8000","locker_api_key":"lk"}}'
    render(config, tmp_path)
    backend = (tmp_path / "backend.env").read_text()
    assert "COMMAND_TTL_SECONDS=30\n" in backend
    assert "PI_TIMEOUT_SECONDS=20\n" in backend
    assert f'PI_DEVICES={config["PI_DEVICES"]}\n' in backend
    assert "PI_DEVICES" not in (tmp_path / "db.env").read_text()


@pytest.mark.parametrize("key,value", [
    ("COMMAND_TTL_SECONDS", "0"), ("COMMAND_TTL_SECONDS", "-5"), ("COMMAND_TTL_SECONDS", "thirty"),
    ("PI_DEVICES", "not json"), ("PI_DEVICES", "[]"), ("PI_DEVICES", "{}"),
    ("PI_DEVICES", '{"kiosk-1":{"capture_url":"http://x:8000"}}'),
])
def test_bad_pi_settings_are_rejected(tmp_path, key, value):
    config = payload()
    config[key] = value
    with pytest.raises(ValueError):
        render(config, tmp_path)
    assert not (tmp_path / "backend.env").exists()


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


@pytest.mark.parametrize("name", ["nginx.http.conf", "nginx.https.conf.template"])
def test_nginx_serves_frontend_and_keeps_api_routes(name):
    conf = (ROOT / "runtime" / name).read_text()
    assert "location /api/ { proxy_pass http://backend:8000; proxy_read_timeout 120s; }" in conf
    assert "root /usr/share/nginx/html;" in conf
    assert "try_files $uri $uri/ /index.html;" in conf
    assert "return 503" not in conf


def test_frontend_build_is_shipped_and_mounted_read_only():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY --from=frontend /frontend/dist /opt/deploy/frontend" in dockerfile
    compose = (ROOT / "runtime" / "compose.yaml").read_text()
    assert "./frontend:/usr/share/nginx/html:ro" in compose
    deploy = (ROOT / "runtime" / "deploy.sh").read_text()
    assert '[[ -f "$release/frontend/index.html" ]]' in deploy


@pytest.mark.parametrize("timeout", ["5", "90"])
def test_photo_timeout_reaches_backend_only(tmp_path, timeout):
    config = {**payload(), "IMAGE_ANALYSIS_TIMEOUT_SECONDS": timeout}
    render(config, tmp_path)
    assert f"IMAGE_ANALYSIS_TIMEOUT_SECONDS={timeout}\n" in (tmp_path / "backend.env").read_text()
    assert "IMAGE_ANALYSIS_TIMEOUT_SECONDS" not in (tmp_path / "db.env").read_text()


@pytest.mark.parametrize("timeout", ["0", "4", "91", "120", "-5", "90.0", "bad"])
def test_photo_timeout_fits_proxy_deadline(tmp_path, timeout):
    with pytest.raises(ValueError):
        render({**payload(), "IMAGE_ANALYSIS_TIMEOUT_SECONDS": timeout}, tmp_path)
    assert not (tmp_path / "backend.env").exists()


@pytest.mark.parametrize(("key", "value"), [("OWNERSHIP_SEARCH_BUDGET_SECONDS", "40"),
                                            ("OWNERSHIP_SYNC_WAIT_SECONDS", "0"),
                                            ("OWNERSHIP_SEARCH_CONCURRENCY", "5"),
                                            ("OWNERSHIP_MAX_CONCURRENT_SEARCHES", "8"),
                                            ("ANALYSIS_SYNC_WAIT_SECONDS", "5"),
                                            ("MAX_CONCURRENT_ANALYSES", "3")])
def test_job_tuning_reaches_backend_only(tmp_path, key, value):
    render({**payload(), key: value}, tmp_path)
    assert f"{key}={value}\n" in (tmp_path / "backend.env").read_text()
    assert key not in (tmp_path / "db.env").read_text()


@pytest.mark.parametrize(("key", "value"), [("OWNERSHIP_SEARCH_BUDGET_SECONDS", "4"),
                                            ("OWNERSHIP_SEARCH_BUDGET_SECONDS", "120"),
                                            ("OWNERSHIP_SEARCH_BUDGET_SECONDS", "25.0"),
                                            ("OWNERSHIP_SYNC_WAIT_SECONDS", "31"),
                                            ("OWNERSHIP_SEARCH_CONCURRENCY", "0"),
                                            ("OWNERSHIP_MAX_CONCURRENT_SEARCHES", "17"),
                                            ("OWNERSHIP_MAX_CONCURRENT_SEARCHES", "bad"),
                                            ("ANALYSIS_SYNC_WAIT_SECONDS", "31"),
                                            ("MAX_CONCURRENT_ANALYSES", "0"),
                                            ("MAX_CONCURRENT_ANALYSES", "9")])
def test_job_tuning_outside_its_range_is_rejected(tmp_path, key, value):
    with pytest.raises(ValueError):
        render({**payload(), key: value}, tmp_path)
    assert not (tmp_path / "backend.env").exists()


def test_every_published_setting_is_passed_by_the_workflow():
    """publish.py reads os.environ, so a setting the workflow never maps is silently skipped.

    Adding a key to publish.py alone leaves it absent from SSM and from backend.env, with no
    error anywhere. This compares the two lists so the omission fails here instead of on EC2.
    """
    source = (ROOT / "publish.py").read_text(encoding="utf-8")
    optional = set(re.findall(r'"([A-Z][A-Z0-9_]+)"', source.split("payload[\"BUSINESS_API_ENABLED\"]")[1]))
    workflow = (ROOT.parent / ".github/workflows/backend.yml").read_text(encoding="utf-8")
    mapped = set(re.findall(r"^\s+([A-Z][A-Z0-9_]+):\s+\$\{\{\s*(?:vars|secrets)\.", workflow, re.M))
    missing = sorted(key for key in optional if key not in mapped)
    assert missing == [], f"워크플로 env에 없는 설정: {missing}"
