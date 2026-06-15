import typer
import yaml
import re
from pathlib import Path

INFRA_DIR = Path(".infra")

COMPUTE_SERVICES = {"lambda", "ecs-fargate", "eks"}

VALID_SERVICES = {
    # compute
    "lambda", "ecs-fargate", "eks",
    # frontend / CDN
    "static-site",
    # ingress
    "alb", "api-gateway",
    # relational / cache
    "postgres", "mysql", "redis",
    # NoSQL / object
    "dynamodb", "s3",
    # messaging / events
    "sqs", "eventbridge",
    # security / auth
    "cognito", "kms",
    # AI / ML (generates IAM permissions on Lambda — no Terraform resource needed)
    "bedrock", "polly",
}

app = typer.Typer(help="DevOps Greenfield Scaffold Generator")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: str = "infra.yaml") -> dict:
    p = Path(path)
    if not p.exists():
        typer.secho(f"ERROR: {path} not found.", fg=typer.colors.RED, bold=True)
        typer.secho(
            "  Create an infra.yaml with project name, region, owner, and services.\n"
            "  Example:\n"
            "    project:\n"
            "      name: my-app\n"
            "      region: us-east-1\n"
            "      owner: platform-team\n"
            "    services:\n"
            "      - ecs-fargate\n"
            "      - alb\n"
            "      - postgres\n",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(1)
    typer.echo(f"> Reading {path}...")
    return yaml.safe_load(p.read_text()) or {}


def _validate_name(name: str):
    if not re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$', name) or len(name) > 20:
        typer.secho(
            f"ERROR: project.name '{name}' is invalid.\n"
            f"  Must be lowercase letters and hyphens only, max 20 chars.\n"
            f"  Example: payments-api, event-processor, my-service",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)


def _detect_existing(base: Path) -> tuple[str, list[str]]:
    """Return (old_compute_label, old_data_services) from existing .infra files."""
    existing_compute = base / "iac" / "compute.tf"
    if not existing_compute.exists():
        return "", []

    old_label = existing_compute.read_text().splitlines()[0].lstrip("# ").strip()

    old_data_tf = base / "iac" / "data.tf"
    old_data = []
    if old_data_tf.exists():
        content = old_data_tf.read_text()
        signatures = [
            ("aws_db_instance",          "rds"),
            ("aws_dynamodb_table",        "dynamodb"),
            ("aws_s3_bucket",            "s3"),
            ("aws_elasticache",          "redis"),
            ("aws_sqs_queue",            "sqs"),
            ("aws_cloudwatch_event_bus", "eventbridge"),
            ("aws_cognito_user_pool",    "cognito"),
        ]
        old_data = [label for sig, label in signatures if sig in content]

    return old_label, old_data


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@app.command()
def init(
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Show what would be generated without writing any files",
    ),
):
    """Generate Terraform infrastructure scaffold from infra.yaml."""

    config = _load_yaml()

    # --- Required fields ---
    project = config.get("project", {})
    name    = project.get("name",   "")
    region  = project.get("region", "")
    owner   = project.get("owner",  "")

    errors = []
    if not name:   errors.append("project.name is required")
    if not region: errors.append("project.region is required")
    if not owner:  errors.append("project.owner is required")
    if errors:
        for e in errors:
            typer.secho(f"ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)

    _validate_name(name)

    # --- Services ---
    services = config.get("services", [])
    if not services:
        typer.secho(
            "ERROR: services list is empty.\n"
            "  Add at least one compute service (lambda | ecs-fargate | eks).",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    unknown = [s for s in services if s not in VALID_SERVICES]
    if unknown:
        typer.secho(f"ERROR: unknown services: {unknown}", fg=typer.colors.RED)
        typer.secho(
            f"  Valid options: {', '.join(sorted(VALID_SERVICES))}",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(1)

    compute = [s for s in services if s in COMPUTE_SERVICES]
    if len(compute) == 0:
        typer.secho(
            "ERROR: no compute target found in services.\n"
            "  Add at least one of: lambda | ecs-fargate | eks",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    # Only one valid multi-compute combination: lambda + eks
    # (serverless functions alongside a dedicated Kubernetes cluster)
    if len(compute) == 2 and set(compute) != {"lambda", "eks"}:
        typer.secho(
            f"ERROR: invalid compute combination: {compute}\n"
            f"  The only supported multi-compute combination is: lambda + eks\n"
            f"  (serverless functions alongside a Kubernetes cluster)",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    if len(compute) > 2:
        typer.secho(
            f"ERROR: too many compute targets: {compute}\n"
            f"  Maximum two allowed (lambda + eks).",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    other_services = [s for s in services if s not in COMPUTE_SERVICES]
    environments   = config.get("environments", {})
    flows          = config.get("flows", {})

    # --- Print config summary ---
    typer.echo(f"  project.name   = {name}")
    typer.echo(f"  project.region = {region}")
    typer.echo(f"  project.owner  = {owner}")
    typer.echo(f"  compute        = {' + '.join(compute)}")
    if other_services:
        typer.echo(f"  services       = {', '.join(other_services)}")
    if flows:
        for flow_name, flow_cfg in flows.items():
            desc = flow_cfg.get("description", "") if isinstance(flow_cfg, dict) else ""
            typer.secho(f"  flow [{flow_name}]  {desc}", fg=typer.colors.CYAN)
    if environments:
        typer.echo(f"  environments   = {', '.join(environments.keys())}")

    # --- Dry run ---
    if dry_run:
        typer.secho("\n=== DRY RUN — no files will be written ===", fg=typer.colors.MAGENTA, bold=True)
        typer.secho("\n  Files that would be generated:", fg=typer.colors.CYAN)
        for f in [
            ".infra/iac/providers.tf",
            ".infra/iac/networking.tf",
            ".infra/iac/compute.tf",
            ".infra/iac/iam.tf",
            ".infra/iac/data.tf",
            ".infra/iac/observability.tf",
            ".infra/iac/variables.tf",
            ".infra/iac/outputs.tf",
            ".infra/cicd/pipeline.yml",
            ".infra/environments/dev.tfvars.example",
            ".infra/environments/staging.tfvars.example",
            ".infra/environments/prod.tfvars.example",
            ".infra/secrets/secrets-policy.yml",
        ]:
            typer.echo(f"    {f}")
        typer.secho("\nDRY RUN COMPLETE.", fg=typer.colors.MAGENTA)
        return

    # --- Overwrite protection ---
    old_label, old_data = _detect_existing(INFRA_DIR)
    if old_label:
        typer.secho("\n! EXISTING SCAFFOLD DETECTED", fg=typer.colors.YELLOW, bold=True)
        typer.secho(f"  Currently: {old_label}", fg=typer.colors.WHITE)
        typer.secho(f"  Old data:  {', '.join(old_data) or 'none'}", fg=typer.colors.WHITE)
        typer.secho(f"\n  Replacing with:", fg=typer.colors.CYAN)
        typer.secho(f"    compute  = {' + '.join(compute)}", fg=typer.colors.CYAN)
        typer.secho(f"    services = {', '.join(other_services) or 'none'}", fg=typer.colors.CYAN)
        typer.secho(f"\n  All .infra/iac/*.tf files will be overwritten.", fg=typer.colors.YELLOW)

        if not typer.confirm("\n  Overwrite existing scaffold?", default=False):
            typer.secho("  Aborted — no files changed.", fg=typer.colors.GREEN)
            raise typer.Exit(0)

    # --- Generate ---
    typer.secho("\n> Generating scaffold...", fg=typer.colors.BLUE, bold=True)
    import generator
    generator.generate_scaffold(config)


if __name__ == "__main__":
    app()
