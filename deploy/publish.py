"""GitHub Actions entry point: synchronize configuration and invoke a pinned release."""
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time

import boto3

sys.path.insert(0, str(Path(__file__).parent / "runtime"))
from render_config import render


def required(name: str, pattern: str) -> str:
    value = os.environ.get(name, "")
    if not re.fullmatch(pattern, value):
        raise ValueError(f"Missing or invalid configuration: {name}")
    return value


def build_commands(image: str, region: str, parameter: str, release_id: str, domain: str, email: str) -> list[str]:
    registry = image.split("/")[0]
    root = "/opt/smart-lost-found"
    release = f"{root}/releases/{release_id}"
    container = f"slf-release-{release_id}"
    q = shlex.quote
    return [
        "set -eu",
        "umask 077",
        "command -v aws >/dev/null || { echo 'Install AWS CLI on this EC2 first.' >&2; exit 1; }",
        "DOCKER_CONFIG=$(mktemp -d /tmp/slf-ecr.XXXXXX); export DOCKER_CONFIG",
        f"cleanup() {{ docker rm -f {q(container)} >/dev/null 2>&1 || true; rm -rf -- \"$DOCKER_CONFIG\"; }}",
        "trap cleanup EXIT",
        f"aws ecr get-login-password --region {q(region)} | docker login --username AWS --password-stdin {q(registry)} >/dev/null",
        f"docker pull {q(image)}",
        f"mkdir -p {q(release)}",
        f"docker create --name {q(container)} {q(image)} >/dev/null",
        f"docker cp {q(container + ':/opt/deploy/.')} {q(release)}",
        f"docker rm {q(container)} >/dev/null",
        f"bash {q(release + '/deploy.sh')} {q(image)} {q(region)} {q(parameter)} {q(domain)} {q(email)}",
    ]


def main():
    region = required("AWS_REGION", r"[a-z]{2}-[a-z]+-\d")
    instance = required("EC2_INSTANCE_ID", r"i-[0-9a-f]{17}")
    parameter = required("CONFIG_PARAMETER", r"/smart-lost-found/[a-z0-9/_-]+")
    image = required("BACKEND_IMAGE", r"\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9/_-]+@sha256:[a-f0-9]{64}")
    release_id = required("RELEASE_ID", r"[a-f0-9]{40}-\d+-\d+")
    domain, email = os.environ.get("API_DOMAIN", ""), os.environ.get("TLS_EMAIL", "")
    if domain:
        required("API_DOMAIN", r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}")
        required("TLS_EMAIL", r"[^\s@]+@[^\s@]+\.[^\s@]+")
    payload = {key: os.environ.get(key, "") for key in
               ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "S3_BUCKET", "S3_REGION")}
    # Validate before publishing. Temporary files contain secrets and are removed immediately.
    import tempfile
    with tempfile.TemporaryDirectory() as temp:
        render(payload, Path(temp))
    serialized = json.dumps(payload, ensure_ascii=True)
    if len(serialized.encode()) > 4096:
        raise ValueError("Runtime configuration exceeds Standard Parameter Store limit")
    ssm = boto3.client("ssm", region_name=region)
    ssm.put_parameter(Name=parameter, Value=serialized, Type="SecureString", Overwrite=True,
                      Description="Runtime copy managed by GitHub production Environment")
    response = ssm.send_command(
        InstanceIds=[instance], DocumentName="AWS-RunShellScript", TimeoutSeconds=900,
        Parameters={"commands": build_commands(image, region, parameter, release_id, domain, email),
                    "executionTimeout": ["1500"]},
        Comment=f"Smart Lost Found deploy {release_id}",
    )
    command_id = response["Command"]["CommandId"]
    print(f"SSM command: {command_id}", flush=True)
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        try:
            result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance)
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(5)
            continue
        status = result["Status"]
        if status == "Success":
            print("Deployment completed. Business APIs remain disabled.")
            return
        if status in {"Cancelled", "TimedOut", "Failed", "Cancelling"}:
            # SSM logs are inspected in AWS; do not copy arbitrary container output to Actions.
            raise RuntimeError(f"SSM deployment {status}; inspect command {command_id} in AWS")
        time.sleep(10)
    raise RuntimeError(f"SSM wait deadline reached; command {command_id} may still be running. Inspect before retrying.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never print SDK requests, configuration values, or environment dumps.
        if isinstance(error, (ValueError, RuntimeError)):
            raise SystemExit(str(error)) from None
        raise SystemExit("Deployment failed; inspect AWS permissions and SSM command status.") from None
