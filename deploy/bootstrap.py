"""One-time AWS/GitHub setup. Requires explicit operator AWS and GitHub credentials.

Existing S3 permissions are preserved. A password is generated only when the
production GitHub secret does not yet exist; its value is never printed or saved.
"""
import argparse
import json
import secrets
import subprocess

import boto3


def gh(*args, body=None):
    command = ["gh", "api", *args]
    if body is not None:
        command += ["--input", "-"]
    result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def setup(region, instance, bucket, repository):
    session = boto3.Session(region_name=region)
    account = session.client("sts").get_caller_identity()["Account"]
    iam, ecr, ssm = (session.client(service) for service in ("iam", "ecr", "ssm"))
    details = session.client("ec2").describe_instances(InstanceIds=[instance])["Reservations"][0]["Instances"][0]
    profile = details["IamInstanceProfile"]["Arn"].rsplit("/", 1)[1]
    instance_role = iam.get_instance_profile(InstanceProfileName=profile)["InstanceProfile"]["Roles"][0]["RoleName"]
    ecr_name = "smart-lost-found/backend"
    parameter = "/smart-lost-found/production/config"
    ecr_arn = f"arn:aws:ecr:{region}:{account}:repository/{ecr_name}"
    parameter_arn = f"arn:aws:ssm:{region}:{account}:parameter{parameter}"
    provider = f"arn:aws:iam::{account}:oidc-provider/token.actions.githubusercontent.com"
    # Bootstrap does not replace or edit an account-wide OIDC provider.
    iam.get_open_id_connect_provider(OpenIDConnectProviderArn=provider)
    try:
        ecr.describe_repositories(repositoryNames=[ecr_name])
    except ecr.exceptions.RepositoryNotFoundException:
        ecr.create_repository(repositoryName=ecr_name, imageTagMutability="IMMUTABLE",
                              imageScanningConfiguration={"scanOnPush": True})
    oidc = gh(f"repos/{repository}/actions/oidc/customization/sub")
    if not oidc.get("use_default"):
        raise RuntimeError("Custom repository OIDC subject requires manual trust-policy review")
    prefix = oidc.get("sub_claim_prefix", f"repo:{repository}")
    role_name = "smart-lost-found-github-deploy"
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Federated": provider},
             "Action": "sts:AssumeRoleWithWebIdentity", "Condition": {"StringEquals": {
                 "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                 "token.actions.githubusercontent.com:sub": f"{prefix}:environment:production"}}}]}
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
    except iam.exceptions.NoSuchEntityException:
        role = iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=json.dumps(trust),
                               Description="GitHub production deployment for CloudAIoT/smart-lost-found")["Role"]
    else:
        if role["AssumeRolePolicyDocument"] != trust:
            raise RuntimeError("Existing deploy role has a different trust policy; review it before proceeding")
    def policy(statements):
        return json.dumps({"Version": "2012-10-17", "Statement": statements})
    iam.put_role_policy(RoleName=role_name, PolicyName="SmartLostFoundDeploy", PolicyDocument=policy([
        {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
        {"Effect": "Allow", "Action": ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
         "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages"], "Resource": ecr_arn},
        {"Effect": "Allow", "Action": "ssm:PutParameter", "Resource": parameter_arn},
        {"Effect": "Allow", "Action": "ssm:SendCommand", "Resource": [f"arn:aws:ec2:{region}:{account}:instance/{instance}",
         f"arn:aws:ssm:{region}::document/AWS-RunShellScript"]},
        {"Effect": "Allow", "Action": "ssm:GetCommandInvocation", "Resource": "*"},
    ]))
    iam.put_role_policy(RoleName=instance_role, PolicyName="SmartLostFoundDeploymentRuntime", PolicyDocument=policy([
        {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
        {"Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], "Resource": ecr_arn},
        {"Effect": "Allow", "Action": "ssm:GetParameter", "Resource": parameter_arn},
    ]))
    env_path = f"repos/{repository}/environments/production"
    gh(env_path, "--method", "PUT", body={"wait_timer": 0, "reviewers": [], "deployment_branch_policy": {
        "protected_branches": False, "custom_branch_policies": True}})
    policies = gh(f"{env_path}/deployment-branch-policies")["branch_policies"]
    unexpected = [p for p in policies if p["name"] != "main" or p.get("type", "branch") != "branch"]
    if unexpected:
        raise RuntimeError("Unexpected production branch policy; review manually before enabling deployment")
    if not policies:
        gh(f"{env_path}/deployment-branch-policies", "--method", "POST", body={"name": "main", "type": "branch"})
    variables = {
        "AWS_REGION": region, "AWS_ROLE_ARN": role["Arn"], "EC2_INSTANCE_ID": instance,
        "ECR_REPOSITORY": ecr_name, "CONFIG_PARAMETER": parameter,
        "POSTGRES_DB": "smart_lost_found", "POSTGRES_USER": "smart_lost_found", "S3_BUCKET": bucket,
    }
    existing = {v["name"]: v["value"] for v in gh(f"{env_path}/variables")["variables"]}
    for name, value in variables.items():
        if name in existing and existing[name] != value:
            raise RuntimeError(f"Existing GitHub variable differs: {name}; review before overwriting")
        if name not in existing:
            gh(f"{env_path}/variables", "--method", "POST", body={"name": name, "value": value})
    existing_secrets = {s["name"] for s in gh(f"{env_path}/secrets")["secrets"]}
    if "POSTGRES_PASSWORD" not in existing_secrets:
        try:
            ssm.get_parameter(Name=parameter, WithDecryption=False)
        except ssm.exceptions.ParameterNotFound:
            pass
        else:
            raise RuntimeError("Existing runtime configuration must be reconciled before generating a new password")
        password = secrets.token_urlsafe(48)
        subprocess.run(["gh", "secret", "set", "POSTGRES_PASSWORD", "--repo", repository, "--env", "production"],
                       input=password, text=True, capture_output=True, check=True)
        # Initial runtime copy; future updates are performed by the Actions workflow.
        payload = {"POSTGRES_DB": variables["POSTGRES_DB"], "POSTGRES_USER": variables["POSTGRES_USER"],
                   "POSTGRES_PASSWORD": password, "S3_BUCKET": bucket, "S3_REGION": region}
        ssm.put_parameter(Name=parameter, Type="SecureString", Value=json.dumps(payload), Overwrite=False,
                          Description="Runtime copy managed by GitHub production Environment")
        print("Created GitHub POSTGRES_PASSWORD secret and encrypted runtime copy; value not displayed.")
    print(json.dumps({"role_arn": role["Arn"], "image_repository": f"{account}.dkr.ecr.{region}.amazonaws.com/{ecr_name}",
                      "environment": "production", "instance": instance}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    try:
        setup(args.region, args.instance, args.bucket, args.repository)
    except Exception:
        raise SystemExit("Bootstrap failed. Inspect resource permissions and existing configuration; secret values were not logged.") from None
