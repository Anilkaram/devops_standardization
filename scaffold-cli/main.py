import sys
import importlib.util
from pathlib import Path

# Load dynamic_generator by absolute path so it works regardless of CWD or sys.path.
_dg_path = Path(__file__).parent / "dynamic_generator.py"
if not _dg_path.exists():
    print(f"ERROR: dynamic_generator.py not found at {_dg_path}")
    sys.exit(1)
_spec = importlib.util.spec_from_file_location("dynamic_generator", _dg_path)
dg    = importlib.util.module_from_spec(_spec)
sys.modules["dynamic_generator"] = dg
_spec.loader.exec_module(dg)

import typer
import yaml
import re

import decisions     as dec
import ai_client     as aic
from config_extractor   import extract_config_from_description, merge_extracted_into_config
from interactive_prompts import fill_missing_fields, is_config_complete

INFRA_DIR = Path(".infra")

app = typer.Typer(
    help="DevOps Greenfield Scaffold Generator",
    add_completion=False,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_yaml(path: str = "infra.yaml") -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    typer.echo(f"> Reading {path}...")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _normalize_services(config: dict) -> None:
    """Flatten service entries to plain strings, in place.

    A service in infra.yaml may be written several ways:
      - plain string:        'ec2'
      - type-keyed mapping:   {type: ec2, instance_type: ...}
      - name-keyed mapping:   {ec2: {instance_type: ...}}
    YAML parses the mapping forms as dicts, which are unhashable and break the
    set-membership checks throughout the CLI. Normalize every form to the
    service-name string; per-service sizing is read from `environments`.
    """
    services = config.get("services")
    if not isinstance(services, list):
        return
    normalized: list[str] = []
    for entry in services:
        if isinstance(entry, str):
            normalized.append(entry)
        elif isinstance(entry, dict) and entry:
            # type-keyed schema ({type: ec2, ...}) → the 'type' value is the name;
            # otherwise it's a name-keyed mapping ({ec2: {...}}) → the key is the name.
            if "type" in entry:
                normalized.append(str(entry["type"]))
            else:
                normalized.append(next(iter(entry)))
        # silently drop None / empty entries
    config["services"] = normalized


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
    existing_compute = base / "main.tf"
    if not existing_compute.exists():
        existing_compute = base / "iac" / "compute.tf"
    if not existing_compute.exists():
        return "", []
    old_label = existing_compute.read_text().splitlines()[0].lstrip("# ").strip()
    old_data_tf = base / "data.tf"
    if not old_data_tf.exists():
        old_data_tf = base / "iac" / "data.tf"
    old_data = []
    if old_data_tf.exists():
        content = old_data_tf.read_text()
        signatures = [
            ("aws_db_instance",           "rds"),
            ("aws_dynamodb_table",        "dynamodb"),
            ("aws_s3_bucket",             "s3"),
            ("aws_elasticache",           "redis"),
            ("aws_sqs_queue",             "sqs"),
            ("aws_cloudwatch_event_bus",  "eventbridge"),
            ("aws_cognito_user_pool",     "cognito"),
            ("aws_opensearch_domain",     "opensearch"),
            ("aws_kinesis_stream",        "kinesis"),
            ("aws_msk_cluster",           "msk"),
        ]
        old_data = [label for sig, label in signatures if sig in content]
    return old_label, old_data


def _normalise_config(config: dict) -> dict:
    """
    Normalise config so generator.py always finds fields in the expected places.
    Handles both the old (services-list) schema and the new (nested) schema.
    """
    # project.region fallback
    if not config.get("project", {}).get("region"):
        region = config.get("cloud", {}).get("region", "")
        if region:
            config.setdefault("project", {})["region"] = region

    # project.owner fallback
    if not config.get("project", {}).get("owner"):
        owner = config.get("owner", "")
        if owner:
            config.setdefault("project", {})["owner"] = owner

    # Normalize services: support both list-of-strings and list-of-dicts with 'type' keys
    raw_services = config.get("services", [])
    if raw_services and isinstance(raw_services[0], dict):
        # New schema: preserve original instances, extract types for generator compatibility
        config["service_instances"] = raw_services
        config["services"] = list(dict.fromkeys(s["type"] for s in raw_services))

    # data.stores → services list integration
    data_stores = config.get("data", {}).get("stores", [])
    if data_stores:
        svcs = config.setdefault("services", [])
        for ds in data_stores:
            if ds not in svcs:
                svcs.append(ds)

    # auth.required → cognito service only when method is not explicitly 'iam'
    auth = config.get("auth", {})
    if auth.get("required") and auth.get("method", "cognito") != "iam":
        svcs = config.setdefault("services", [])
        if "cognito" not in svcs:
            svcs.append("cognito")

    return config


# ─────────────────────────────────────────────────────────────────────────────
# CLI commands
# ─────────────────────────────────────────────────────────────────────────────

@app.command()
def init(
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Show what would be generated without writing any files",
    ),
    describe: str = typer.Option(
        None, "--describe",
        help='Plain-English description of your architecture. AI extracts the config.',
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip interactive prompts and use defaults for any missing fields",
    ),
    ai_provider: str = typer.Option(
        None, "--ai-provider",
        help="Override AI_PROVIDER env var: claude | openai | gemini",
    ),
    ai_model: str = typer.Option(
        None, "--ai-model",
        help="Override AI_MODEL env var for the selected provider",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Overwrite files you edited since the last generation (default: preserve them)",
    ),
):
    """Generate Terraform infrastructure scaffold from infra.yaml."""
    import os
    if ai_provider:
        os.environ["AI_PROVIDER"] = ai_provider
    if ai_model:
        os.environ["AI_MODEL"] = ai_model

    # ── Load catalog ─────────────────────────────────────────────────────────
    catalog          = dg.load_catalog()
    valid_services   = dg.get_all_valid_services(catalog)
    compute_services = dg.get_compute_services(catalog)

    # ── Load infra.yaml (may be empty / not exist) ────────────────────────────
    config = _load_yaml()
    _normalize_services(config)

    # Drop infra-layer names (vpc, iam, security_group) — they are always
    # auto-generated (networking.tf / iam.tf), never per-service modules.
    _infra_layers = dg.get_infra_layer_names(catalog)
    _svcs = config.get("services", [])
    _dropped = [s for s in _svcs if s in _infra_layers]
    if _dropped:
        config["services"] = [s for s in _svcs if s not in _infra_layers]
        typer.secho(
            f"  [i] Skipping auto-generated infra layers: {_dropped} "
            "(VPC/IAM/security groups are always created).",
            fg=typer.colors.BLUE,
        )

    # ── --describe: AI extraction fills in what infra.yaml doesn't have ───────
    if describe:
        typer.secho(
            f"\n> AI provider: {aic.provider_info()}",
            fg=typer.colors.BLUE,
        )
        extracted = extract_config_from_description(describe)
        if extracted:
            config = merge_extracted_into_config(config, extracted)

    # ── Interactive prompts for any missing required fields ───────────────────
    decisions_path = INFRA_DIR / "decisions.md"
    if not yes and not is_config_complete(config):
        config = fill_missing_fields(config, decisions_path=decisions_path)
    elif yes and not is_config_complete(config):
        typer.secho(
            "  [!] --yes passed but config is incomplete. "
            "Some required fields may be missing.",
            fg=typer.colors.YELLOW,
        )

    # ── Normalise config ──────────────────────────────────────────────────────
    config = _normalise_config(config)

    # ── Extract fields ────────────────────────────────────────────────────────
    project      = config.get("project", {})
    name         = project.get("name",   "")
    region       = project.get("region", "")
    owner        = project.get("owner",  "")
    environments = config.get("environments", {})
    services     = config.get("services", [])

    errors = []
    if not name:   errors.append("project.name is required")
    if not region: errors.append("project.region (or cloud.region) is required")
    if not owner:  errors.append("project.owner is required")
    if errors:
        for e in errors:
            typer.secho(f"ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)

    _validate_name(name)

    # ── Services validation ───────────────────────────────────────────────────
    if not services:
        typer.secho(
            "ERROR: no services specified.\n"
            "  Add at least one compute service, or use --describe to let AI infer services.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    unknown = [
        s for s in services
        if s not in valid_services and not dg._base_svc(s, catalog)
    ]
    if unknown:
        typer.secho(
            f"  [!] Services not in catalog: {unknown}\n"
            f"    These will be generated via AI ({aic.provider_info()}).",
            fg=typer.colors.YELLOW,
        )

    compute = dg.resolve_compute_services(services, catalog)
    if not compute:
        # A compute-less stack is valid for static sites (S3 + CloudFront) and
        # data-only stacks — as long as there is at least one real service to build.
        if not services:
            typer.secho(
                f"ERROR: no services to generate.\n"
                f"  Add a compute target ({', '.join(sorted(compute_services))}) "
                f"or at least one service (e.g. s3, cloudfront, static-site).",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)
        typer.secho(
            "  [i] No compute target — generating a compute-less stack "
            "(static site / data-only). No CI/CD deploy pipeline will be created.",
            fg=typer.colors.YELLOW,
        )

    max_compute = dg.get_max_compute_targets(catalog)
    # Count unique base types — ec2-java, ec2-php, ec2-doc all count as one "ec2"
    unique_compute_types = {dg._base_svc(s, catalog) or s for s in compute}
    if len(unique_compute_types) > max_compute:
        typer.secho(
            f"ERROR: too many compute targets: {compute}\n"
            f"  Maximum {max_compute} compute services allowed.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    if len(compute) == 2:
        valid_combos = dg.get_valid_compute_combinations(catalog)
        if sorted(compute) not in [sorted(c) for c in valid_combos]:
            typer.secho(
                f"ERROR: unsupported compute combination: {compute}\n"
                f"  Valid multi-compute combinations:\n"
                + "\n".join(f"    - {c}" for c in valid_combos),
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)

    # Exclude the base-aware compute list so role-suffixed compute (ec2-java)
    # doesn't appear in both the compute line and the services line.
    _compute_set   = set(compute)
    other_services = [s for s in services if s not in _compute_set]
    flows          = config.get("flows", {})

    # ── Print summary ─────────────────────────────────────────────────────────
    typer.echo("")
    typer.secho("> Scaffold summary:", fg=typer.colors.BLUE, bold=True)
    typer.echo(f"  project.name   = {name}")
    typer.echo(f"  project.region = {region}")
    typer.echo(f"  project.owner  = {owner}")
    typer.echo(f"  project.type   = {project.get('type', 'not set')}")
    typer.echo(f"  stage          = {config.get('stage', 'not set')}")
    typer.echo(f"  compute        = {' + '.join(compute)}")
    if other_services:
        known   = [s for s in other_services if s in valid_services or dg._base_svc(s, catalog)]
        dynamic = [s for s in other_services if s not in valid_services and not dg._base_svc(s, catalog)]
        if known:
            typer.echo(f"  services       = {', '.join(known)}")
        if dynamic:
            typer.secho(
                f"  services (AI)  = {', '.join(dynamic)}  [will call {aic.provider_info()}]",
                fg=typer.colors.CYAN,
            )
    if environments:
        typer.echo(f"  environments   = {', '.join(environments.keys())}")
    typer.echo(f"  AI provider    = {aic.provider_info()}")

    # ── Dry run ───────────────────────────────────────────────────────────────
    if dry_run:
        typer.secho("\n=== DRY RUN — no files will be written ===", fg=typer.colors.MAGENTA, bold=True)
        typer.secho("\n  Files that would be generated:", fg=typer.colors.CYAN)
        env_names_preview = list((config.get("environments") or {}).keys()) or ["dev", "staging", "prod"]
        static_files = [
            ".infra/provider.tf",
            ".infra/networking.tf",
            ".infra/main.tf",
            ".infra/iam.tf",
            ".infra/data.tf",
            ".infra/observability.tf",
            ".infra/output.tf",
            ".infra/variables.tf",
            ".infra/cicd/pipeline.yml",
            ".infra/cicd/README.md",
            ".infra/secrets/secrets-policy.yml",
            ".infra/decisions.md",
        ]
        env_files = [
            f".infra/env/{e}/{f}"
            for e in env_names_preview
            for f in ["backend.tf", "terraform.tfvars", "terraform.tfvars.example"]
        ]
        for f in static_files + env_files:
            typer.echo(f"    {f}")
        if unknown:
            typer.secho(
                f"\n  Services requiring AI: {unknown}",
                fg=typer.colors.CYAN,
            )
        typer.secho("\nDRY RUN COMPLETE.", fg=typer.colors.MAGENTA)
        return

    # ── Overwrite protection ──────────────────────────────────────────────────
    old_label, old_data = _detect_existing(INFRA_DIR)
    if old_label:
        typer.secho("\n! EXISTING SCAFFOLD DETECTED", fg=typer.colors.YELLOW, bold=True)
        typer.secho(f"  Currently: {old_label}", fg=typer.colors.WHITE)
        typer.secho(f"  Old data:  {', '.join(old_data) or 'none'}", fg=typer.colors.WHITE)
        typer.secho(f"\n  Replacing with:", fg=typer.colors.CYAN)
        typer.secho(f"    compute  = {' + '.join(compute)}", fg=typer.colors.CYAN)
        typer.secho(f"    services = {', '.join(other_services) or 'none'}", fg=typer.colors.CYAN)
        typer.secho(f"\n  All .infra/*.tf and .infra/env/ files will be overwritten.", fg=typer.colors.YELLOW)
        if not yes and not typer.confirm("\n  Overwrite existing scaffold?", default=False):
            typer.secho("  Aborted — no files changed.", fg=typer.colors.GREEN)
            raise typer.Exit(0)

    # ── Write decisions.md run header ─────────────────────────────────────────
    INFRA_DIR.mkdir(parents=True, exist_ok=True)
    dec.log_run_header(name, path=decisions_path)
    if describe:
        dec.log_decision("input.describe", describe[:200] + ("..." if len(describe) > 200 else ""),
                         "cli flag", "--describe text passed by user", path=decisions_path)

    # Log values that came from infra.yaml (not prompted)
    if config.get("cloud", {}).get("provider"):
        dec.log_decision("cloud.provider", config["cloud"]["provider"],
                         "infra.yaml", "Read from project config file.", path=decisions_path)

    # ── Preserve user edits across re-generation ─────────────────────────────
    user_edits = {} if force else _detect_user_edits(INFRA_DIR)
    if user_edits:
        typer.secho(
            f"  [i] {len(user_edits)} file(s) modified since last generation — "
            "your edits will be preserved (use --force to regenerate them):",
            fg=typer.colors.BLUE,
        )
        for rel in list(user_edits)[:10]:
            typer.echo(f"      {rel}")

    # ── Generate ──────────────────────────────────────────────────────────────
    typer.secho("\n> Generating scaffold...", fg=typer.colors.BLUE, bold=True)
    import generator
    try:
        generator.generate_scaffold(config, catalog)
    except Exception as exc:
        import traceback
        INFRA_DIR.mkdir(exist_ok=True)
        (INFRA_DIR / "generation-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        typer.secho(
            f"\nERROR: generation failed — {type(exc).__name__}: {exc}\n"
            f"  Full traceback saved to {INFRA_DIR / 'generation-error.log'}\n"
            f"  Common causes: malformed infra.yaml (run with no infra.yaml to use prompts),\n"
            f"  or an unsupported service combination — run 'services' to see the catalog.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    # Restore the user's edits on top of the fresh generation.
    for rel, content in user_edits.items():
        (INFRA_DIR / rel).write_bytes(content)
    if user_edits:
        typer.secho(f"  [OK] Restored {len(user_edits)} user-edited file(s).", fg=typer.colors.GREEN)

    # ── Write infra.yaml.example with naming conventions ─────────────────────
    def _write_infra_example(path: Path, project_name: str) -> None:
        content = f"""\
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# infra.yaml — scaffold-cli configuration reference
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# NAMING CONVENTIONS
# ──────────────────
# All generated AWS resource names follow this pattern:
#
#   {{project.name}}-{{environment}}-{{resource-suffix}}
#
# Separator rules:
#   Hyphen   -   resource names       e.g. {project_name}-dev-func
#   Slash    /   path-based names     e.g. {project_name}/dev/app  (Secrets Manager, log groups)
#   Snake    _   Terraform variables  e.g. lambda_timeout, eks_node_count
#
# Resource name examples (using project.name = "{project_name}"):
#
#   Lambda function          {project_name}-dev-func
#   Lambda IAM role          {project_name}-dev-lambda-role
#   EKS cluster              {project_name}-dev-eks
#   EKS cluster IAM role     {project_name}-dev-eks-cluster-role
#   EKS node group           {project_name}-dev-ng
#   ECR repository           {project_name}-dev-app
#   SQS queue                {project_name}-dev-queue
#   SQS dead-letter queue    {project_name}-dev-dlq
#   SNS topic                {project_name}-dev-notifications
#   KMS key alias            alias/{project_name}-dev
#   Secrets Manager (app)    {project_name}/dev/app
#   Secrets Manager (eks)    {project_name}/dev/eks-auth
#   CloudWatch log group     /aws/lambda/{project_name}-dev-func
#   CloudWatch dashboard     {project_name}-dev
#   CloudWatch alarm         {project_name}-dev-lambda-errors
#
# FIELD RULES
# ───────────
#   project.name    Lowercase letters, digits, hyphens only. Max 20 chars.
#                   Pattern: ^[a-z0-9][a-z0-9-]*[a-z0-9]$
#                   Good:  payments-api, ai-assistant, eks-cicd-platform
#                   Bad:   PaymentsAPI, payments_api, my app
#
#   project.region  Standard AWS region format.
#                   Example: us-east-1, eu-west-1, ap-southeast-2
#
#   project.owner   Lowercase letters, digits, hyphens only. Max 30 chars.
#                   Applied as a tag on every generated resource.
#                   Example: platform-team, backend-squad, devops-team
#
#   services        Must match catalog entries exactly (see: scaffold-cli services).
#                   At least one compute service required: lambda, ecs-fargate, eks, ec2.
#
#   connections     Use exact service names from the services list.
#                   Format: {{ from: <service>, to: <service> }}
#
#   environments    Keys become the environment name in all resource names.
#                   Lowercase, hyphens, max 10 chars per name.
#                   Example: dev, uat, prod   (NOT Dev, PROD, production-env)
#
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

project:
  name: your-project-name       # ^[a-z0-9][a-z0-9-]{{1,18}}[a-z0-9]$  max 20 chars
  region: us-east-1             # AWS region — e.g. us-east-1, eu-west-2
  owner: your-team-name         # kebab-case, max 30 chars — used in resource tags
  type: backend                 # backend | frontend | full-stack | chatbot | data-pipeline | ai-service

stage: prototype                # prototype | early | growth | scale

team:
  size: small                   # solo | small | medium | large
  ops_maturity: low             # low | medium | high

runtime:
  language: python              # python | node | go | java | ruby
  containerised: false          # true -> ECS/EKS  |  false -> Lambda/serverless

# ── Services ─────────────────────────────────────────────────────────────────
# List every AWS service to provision. Must match catalog entries exactly.
# Run 'scaffold-cli services' to see all available options.
# At least one compute service is required: lambda, ecs-fargate, eks, ec2
services:
  - lambda                      # compute  — serverless function
  # - ecs-fargate               # compute  — managed containers
  # - eks                       # compute  — Kubernetes cluster
  # - api-gateway               # ingress  — REST/HTTP endpoint
  # - cognito                   # auth     — user pool + app client
  # - dynamodb                  # data     — key-value / document store
  # - s3                        # data     — object storage
  # - rds                       # data     — relational database (postgres/mysql)
  # - sqs                       # queue    — decoupled async messaging
  # - sns                       # notify   — fan-out pub/sub alerts
  # - eventbridge               # events   — event routing and scheduling
  # - kms                       # security — customer-managed encryption keys
  # - secrets-manager           # security — encrypted secret storage
  # - bedrock                   # ai       — foundation models + RAG
  # - cloudwatch                # observe  — logs, alarms, dashboards

# ── Auth ─────────────────────────────────────────────────────────────────────
auth:
  required: false               # true -> adds cognito.tf (unless method: iam)
  method: cognito               # cognito | iam

# ── Connections ──────────────────────────────────────────────────────────────
# Define which services talk to each other.
# Each connection wires: IAM policy on Lambda + env var injected into function.
# Use exact service names from the services list above.
connections:
  - {{ from: api-gateway, to: lambda }}     # adds Lambda invoke permission
  # - {{ from: lambda, to: dynamodb }}      # adds dynamodb:PutItem/GetItem policy + TABLE_NAME env var
  # - {{ from: lambda, to: s3 }}            # adds s3:PutObject/GetObject policy + BUCKET_NAME env var
  # - {{ from: lambda, to: sqs }}           # adds sqs:SendMessage policy + SQS_QUEUE_URL env var
  # - {{ from: sqs, to: lambda }}           # adds SQS event source mapping
  # - {{ from: lambda, to: sns }}           # adds sns:Publish policy + SNS_TOPIC_ARN env var
  # - {{ from: lambda, to: secrets-manager }} # adds secretsmanager:GetSecretValue + ARN env var
  # - {{ from: ecr, to: eventbridge }}      # ECR push rule on default event bus

# ── Environments ─────────────────────────────────────────────────────────────
# Keys must be: lowercase, hyphens only, max 10 chars (they appear in every resource name).
# Values override static defaults — infra.yaml values always win over generated defaults.
environments:
  dev:
    multi_az: false
    lambda:
      memory_mb: 256            # -> lambda_memory_size in env/dev/terraform.tfvars
      timeout_s: 30             # -> lambda_timeout in env/dev/terraform.tfvars
    # eks:
    #   node_count: 1           # -> eks_node_count
    #   instance_type: t3.medium  # -> eks_instance_type

  prod:
    multi_az: true
    lambda:
      memory_mb: 512
      timeout_s: 29             # API Gateway hard limit is 29s

# ── CI/CD ────────────────────────────────────────────────────────────────────
cicd:
  auto_deploy:
    - dev                       # terraform apply runs automatically on push
  manual_deploy:
    - prod                      # requires manual approval in pipeline
"""
        path.write_text(content, encoding="utf-8")

    # ── Full pipeline (replaces simple pipeline.yml from generator) ───────────
    # Skip the deploy pipeline for compute-less stacks (static site / data-only):
    # there is no application to build/deploy, only infrastructure to apply.
    if compute:
        import pipeline_generator as pg
        auto_deploy = config.get("cicd", {}).get("auto_deploy", ["dev"])
        pg.generate_pipeline(
            project_name  = name,
            region        = region,
            compute_list  = compute,
            services      = services,
            environments  = environments or {"dev": {}, "staging": {}, "prod": {}},
            auto_deploy   = auto_deploy,
            output_path   = INFRA_DIR / "cicd" / "pipeline.yml",
            use_ai        = True,
        )
    else:
        typer.secho(
            "  ~ Skipping CI/CD deploy pipeline (no compute target).",
            fg=typer.colors.CYAN,
        )

    _write_infra_example(Path("infra.yaml.example"), name)

    # ── Post-generation: scan tfvars for plaintext secrets ────────────────────
    _scan_tfvars_for_secrets(INFRA_DIR)

    # ── Post-generation: Checkov quality score ────────────────────────────────
    _run_checkov_score(INFRA_DIR)

    typer.secho("\n> Done. Generated files:", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Scaffold       : {INFRA_DIR.absolute()}")
    typer.echo(f"  Decisions      : {decisions_path.absolute()}")

    cost_est_path = INFRA_DIR / "cost-estimate.md"
    if cost_est_path.exists():
        typer.secho(f"  Cost estimate  : {cost_est_path.absolute()}  <-- open this to see monthly costs", fg=typer.colors.CYAN)
    else:
        typer.secho("  Cost estimate  : [not generated — check for errors above]", fg=typer.colors.YELLOW)

    checkov_report = INFRA_DIR / "checkov-report.txt"
    if checkov_report.exists():
        typer.secho(f"  Quality report : {checkov_report.absolute()}", fg=typer.colors.CYAN)

    typer.echo(f"  Example        : infra.yaml.example  (naming conventions + field reference)")

    # Manifest covers everything generated this run (incl. pipeline/cost files).
    # User-edited files keep their previous generated hash so they stay
    # recognized as user-modified on the next re-init.
    _save_manifest(INFRA_DIR, preserve_hashes={
        rel: h for rel, h in _load_manifest(INFRA_DIR).get("files", {}).items() if rel in user_edits
    } if user_edits else {})
    _print_next_steps(INFRA_DIR)


def _resolve_checkov_cmd() -> list:
    """Locate a runnable checkov, or [] if unavailable."""
    import subprocess, shutil, sys

    # Resolve checkov command — four strategies tried in order:
    #   1. python -m checkov      (current interpreter, works when venv is active)
    #   2. venv/Scripts/checkov   (project venv beside scaffold-cli, Windows .cmd or Unix script)
    #   3. PATH binary            (system-wide or CI runner install)
    #   4. not found              (show install hint)
    _checkov_cmd: list = []

    def _probe(cmd: list) -> bool:
        """Return True if cmd runs successfully (returncode 0)."""
        try:
            r = subprocess.run(cmd + ["--version"], capture_output=True, timeout=15)
            return r.returncode == 0
        except Exception:
            return False

    # Strategy 1: current interpreter's module (venv active or checkov in system python)
    if _probe([sys.executable, "-m", "checkov"]):
        _checkov_cmd = [sys.executable, "-m", "checkov"]

    # Strategy 2: scan sibling venv dirs; invoke the checkov script via the venv's own Python.
    # The .cmd wrapper fails when called from outside its venv (wrong sys.path).
    # Instead we find venv/Scripts/python.exe and call: python <venv>/Scripts/checkov
    if not _checkov_cmd:
        _script_dir = Path(__file__).resolve().parent
        _venv_roots = [
            _script_dir.parent / "venv",
            _script_dir.parent / ".venv",
            _script_dir / "venv",
            _script_dir / ".venv",
            Path.cwd() / "venv",
            Path.cwd() / ".venv",
        ]
        for _venv in _venv_roots:
            # Locate the venv's Python interpreter
            _venv_python = None
            for _py in ("Scripts/python.exe", "Scripts/python", "bin/python", "bin/python3"):
                _p = _venv / _py
                if _p.exists():
                    _venv_python = _p
                    break
            if not _venv_python:
                continue
            # Locate the checkov entry-point script (not .cmd — it has an absolute shebang)
            _checkov_script = None
            for _name in ("Scripts/checkov", "Scripts/checkov.exe", "bin/checkov"):
                _s = _venv / _name
                if _s.exists():
                    _checkov_script = _s
                    break
            if _checkov_script and _probe([str(_venv_python), str(_checkov_script)]):
                _checkov_cmd = [str(_venv_python), str(_checkov_script)]
                break

    # Strategy 3: binary somewhere on PATH
    if not _checkov_cmd:
        _binary = shutil.which("checkov")
        if _binary and _probe([_binary]):
            _checkov_cmd = [_binary]

    return _checkov_cmd


def _run_checkov_score(infra_dir: Path) -> None:
    """
    Run Checkov on the generated .infra/ directory and print a quality score.
    Shows passed/failed counts + percentage with colour coding.
    Silently skips if checkov is not installed (prints install hint instead).
    """
    import subprocess, re as _re

    typer.secho("\n> Checkov quality scan...", fg=typer.colors.BLUE, bold=True)

    _checkov_cmd = _resolve_checkov_cmd()
    if not _checkov_cmd:
        typer.secho(
            "  [!] Checkov not found — install it inside your venv:\n"
            "      pip install checkov\n"
            "  Then re-run: python scaffold-cli/main.py init --yes",
            fg=typer.colors.YELLOW,
        )
        return

    try:
        result = subprocess.run(
            _checkov_cmd + [
                "--directory", str(infra_dir),
                "--framework", "terraform",
                "--compact",
                "--quiet",
                "--output", "cli",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        typer.secho("  [!] Checkov timed out (>120s). Run manually: checkov -d .infra", fg=typer.colors.YELLOW)
        return
    except Exception as exc:
        typer.secho(f"  [!] Checkov scan failed: {exc}", fg=typer.colors.YELLOW)
        return

    # Parse: "Passed checks: 45, Failed checks: 8, Skipped checks: 2"
    match = _re.search(
        r"Passed checks:\s*(\d+),\s*Failed checks:\s*(\d+)",
        output,
    )
    if not match:
        typer.secho("  [!] Could not parse Checkov output. Run manually: checkov -d .infra", fg=typer.colors.YELLOW)
        return

    passed = int(match.group(1))
    failed = int(match.group(2))
    total  = passed + failed
    score  = round((passed / total) * 100) if total > 0 else 0

    # Colour thresholds
    if score >= 80:
        color = typer.colors.GREEN
        label = "GOOD"
    elif score >= 60:
        color = typer.colors.YELLOW
        label = "NEEDS WORK"
    else:
        color = typer.colors.RED
        label = "ACTION REQUIRED"

    bar_filled = round(score / 5)   # 20-char bar
    bar = "#" * bar_filled + "-" * (20 - bar_filled)

    typer.secho(
        f"\n  Quality Score  [{bar}]  {score}%  ({label})",
        fg=color, bold=True,
    )
    typer.echo(f"  Passed : {passed}")
    typer.echo(f"  Failed : {failed}")
    typer.echo(f"  Total  : {total} checks")

    if failed > 0:
        # Extract failed check IDs for a quick summary
        failed_ids = _re.findall(r"Check:\s*(CKV[_A-Z0-9]+)", output)
        unique_ids = list(dict.fromkeys(failed_ids))[:8]   # top 8, deduplicated
        if unique_ids:
            typer.secho("\n  Top failed checks (fix these to raise your score):", fg=typer.colors.YELLOW)
            for cid in unique_ids:
                typer.echo(f"    - {cid}  https://docs.bridgecrew.io/docs/{cid.lower()}")

    typer.secho(
        "\n  Full report: checkov -d .infra --framework terraform",
        fg=typer.colors.CYAN,
    )

    # Write score summary to .infra/checkov-report.txt
    report_lines = [
        f"Checkov Quality Score: {score}% ({label})",
        f"Passed : {passed}",
        f"Failed : {failed}",
        f"Total  : {total} checks",
        "",
        "Full CLI output:",
        output,
    ]
    (infra_dir / "checkov-report.txt").write_text("\n".join(report_lines), encoding="utf-8")
    typer.echo(f"  Report saved: {(infra_dir / 'checkov-report.txt').absolute()}")


def _resolve_tfsec_cmd() -> list:
    """Locate a runnable tfsec (or trivy as its successor). Returns [] if absent."""
    import shutil, os

    candidates = []
    binary = shutil.which("tfsec")
    if binary:
        candidates.append([binary])
    # Well-known local install locations (doctor suggests E:\tf-tools on Windows)
    for p in (os.environ.get("TFSEC_PATH", ""), r"E:\tf-tools\tfsec.exe", "/usr/local/bin/tfsec"):
        if p and Path(p).exists():
            candidates.append([p])
    if candidates:
        return candidates[0]
    # Fall back to trivy (tfsec's successor) if present
    trivy = shutil.which("trivy")
    if trivy:
        return [trivy, "config"]
    return []


def _run_tfsec_scan(infra_dir: Path) -> tuple[bool, str]:
    """
    Run tfsec (or trivy config) on the scaffold. Returns (ok, detail).
    ok is True when the scan ran and found no HIGH/CRITICAL issues,
    or when the scanner is not installed (soft-skip with hint).
    """
    import subprocess, re as _re

    typer.secho("\n> tfsec security scan...", fg=typer.colors.BLUE, bold=True)
    cmd = _resolve_tfsec_cmd()
    if not cmd:
        typer.secho(
            "  [!] tfsec not found — install from https://github.com/aquasecurity/tfsec/releases\n"
            "      (or set TFSEC_PATH to the binary). Skipping this gate.",
            fg=typer.colors.YELLOW,
        )
        return True, "not installed — skipped (install tfsec to enable)"

    is_trivy = "trivy" in Path(cmd[0]).name.lower()
    # --exclude-downloaded-modules: registry module internals (.terraform cache)
    # are third-party code — not ours to fix and not part of the gate.
    args = cmd + ([str(infra_dir)] if is_trivy else [str(infra_dir), "--no-color", "--exclude-downloaded-modules"])
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "tfsec timed out (>300s)"
    output = r.stdout + r.stderr

    # tfsec embeds version-pinned doc links (…/tfsec/v1.28.x/checks/…) that 404
    # since the docs moved under /latest/ after the Trivy merge. Rewrite them.
    output = _re.sub(r"(aquasecurity\.github\.io/tfsec/)v[\d.]+/", r"\1latest/", output)

    # tfsec summary: "critical: N high: N medium: N low: N" (in "N potential problems" block)
    counts = dict(_re.findall(r"(critical|high|medium|low)\s*:?\s*(\d+)", output, _re.IGNORECASE))
    crit = int(counts.get("critical", counts.get("CRITICAL", 0)))
    high = int(counts.get("high", counts.get("HIGH", 0)))
    med  = int(counts.get("medium", counts.get("MEDIUM", 0)))
    low  = int(counts.get("low", counts.get("LOW", 0)))
    total = crit + high + med + low

    (infra_dir / "tfsec-report.txt").write_text(output, encoding="utf-8")
    typer.echo(f"  critical: {crit}  high: {high}  medium: {med}  low: {low}")
    typer.echo(f"  Report saved: {(infra_dir / 'tfsec-report.txt').absolute()}")

    if crit or high:
        return False, f"{crit} critical / {high} high findings — see tfsec-report.txt"
    if total:
        return True, f"no critical/high ({med} medium, {low} low — see tfsec-report.txt)"
    return True, "no findings"


def _scan_tfvars_for_secrets(infra_dir: Path) -> int:
    """Warn about plaintext secret patterns in tfvars. Returns finding count."""
    import re as _re
    _SECRET_PATTERNS = [
        # Key may be bare (db_password = "x") or quoted ("DB_PASSWORD" = "x").
        r'(?i)"?(password|passwd|secret|api_key|api-key|token|private_key)"?\s*=\s*"[^"]{6,}"',
        r'(?i)"?(aws_access_key_id|aws_secret_access_key)"?\s*=\s*"[^"]{10,}"',
    ]
    _SAFE_PLACEHOLDERS = {"REPLACE_WITH", "your-", "example", "PLACEHOLDER", "TODO", "changeme"}
    # Metadata keys inside the secrets map whose string values are not credentials.
    _SECRETS_META_KEYS = {"name", "description", "recovery_window_in_days", "kms_key_id"}

    def _is_placeholder(text: str) -> bool:
        return any(p.lower() in text.lower() for p in _SAFE_PLACEHOLDERS)

    def _strip_comment(line: str) -> str:
        # Drop a trailing # comment, but only outside quotes — a # inside a
        # quoted value (e.g. password = "ab#cd") is part of the value.
        in_str = False
        for idx, ch in enumerate(line):
            if ch == '"':
                in_str = not in_str
            elif ch == "#" and not in_str:
                return line[:idx]
        return line

    found = []
    for tfvars in infra_dir.rglob("terraform.tfvars"):
        content = tfvars.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()

        # Block-aware scan: inside the `secrets = {` map, ANY string assigned
        # to a non-metadata key is a credential regardless of the key's name
        # (covers values = { "DB_PASSWORD" = "..." } and friends).
        secrets_block_lines: set[int] = set()
        depth = 0            # brace depth relative to the secrets block
        in_secrets = False
        for i, line in enumerate(lines, 1):
            stripped = _strip_comment(line)
            if not in_secrets:
                if _re.match(r'\s*secrets\s*=\s*\{', stripped):
                    in_secrets = True
                    depth = stripped.count("{") - stripped.count("}")
                continue
            secrets_block_lines.add(i)
            depth += stripped.count("{") - stripped.count("}")
            m = _re.search(r'"?([A-Za-z0-9_\-]+)"?\s*=\s*"([^"]+)"', stripped)
            if m and m.group(1).lower() not in _SECRETS_META_KEYS \
                    and not _is_placeholder(m.group(2)):
                found.append((
                    tfvars.relative_to(infra_dir),
                    f'line {i}: secrets map contains a plaintext {m.group(1)} = "{m.group(2)[:20]}..."',
                ))
            if depth <= 0:
                in_secrets = False

        # Keyword scan for secret-looking keys anywhere else in the file.
        # Lines inside the secrets block were already judged above — skip them
        # so one credential isn't reported twice.
        for pattern in _SECRET_PATTERNS:
            for match in _re.finditer(pattern, content):
                line_no = content.count("\n", 0, match.start()) + 1
                if line_no in secrets_block_lines:
                    continue
                value = match.group(0)
                if not _is_placeholder(value):
                    found.append((tfvars.relative_to(infra_dir), value[:60]))

    if found:
        typer.secho("\n! SECRET SCAN WARNING", fg=typer.colors.RED, bold=True)
        typer.secho(
            "  The following tfvars lines look like plaintext secrets.\n"
            "  NEVER commit real credentials to source control. Instead:\n"
            "    1. keep a placeholder in tfvars (e.g. value = \"changeme\"),\n"
            "    2. apply, then set the real value out-of-band:\n"
            "       aws secretsmanager put-secret-value --secret-id <name> --secret-string '<real value>'\n"
            "    3. add ignore_changes = [secret_string] so Terraform never sees it.\n",
            fg=typer.colors.YELLOW,
        )
        for path, snippet in found:
            typer.secho(f"  {path}: {snippet}...", fg=typer.colors.RED)
    else:
        typer.secho("  [OK] Secret scan: no plaintext secrets detected in tfvars.", fg=typer.colors.GREEN)
    return len(found)


# ─────────────────────────────────────────────────────────────────────────────
# Placeholder scan / next-steps / doctor / validate / manifest
# ─────────────────────────────────────────────────────────────────────────────

_PLACEHOLDER_HINTS = {
    "REPLACE_WITH_STATE_BUCKET":   "S3 bucket for Terraform state — create it with: python scaffold-cli/main.py init-backend",
    "REPLACE_WITH_LOCK_TABLE":     "DynamoDB table for state locking — created by init-backend too",
    "REPLACE_WITH_ACM_CERT_ARN":   "ACM certificate ARN for the ALB HTTPS listener (AWS Console > Certificate Manager)",
    "REPLACE_WITH_ALB_LOG_BUCKET": "S3 bucket that receives ALB access logs",
    "REPLACE_WITH_AMI_ID":         "AMI ID for the EC2/autoscaling instances (e.g. latest Amazon Linux 2023 in your region)",
}

_MANIFEST_NAME = ".scaffold-manifest.json"
_MANIFEST_SKIP = {".terraform", ".terraform.lock.hcl", "checkov-report.txt", "tfsec-report.txt", _MANIFEST_NAME, "generation-error.log", "decisions.md"}


def _scan_placeholders(infra_dir: Path) -> list[tuple[Path, int, str]]:
    """Find unfilled REPLACE_WITH_* placeholders in generated files."""
    import re as _re
    found = []
    for f in sorted(infra_dir.rglob("*")):
        if not f.is_file() or f.suffix not in {".tf", ".tfvars", ".yml", ".yaml", ".json"}:
            continue
        if any(part in _MANIFEST_SKIP for part in f.parts):
            continue
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                for m in _re.finditer(r"REPLACE_WITH_\w+", line):
                    found.append((f.relative_to(infra_dir), i, m.group(0)))
        except OSError:
            continue
    return found


def _print_next_steps(infra_dir: Path) -> None:
    """Print a plain-English checklist of what the user must do before apply."""
    placeholders = _scan_placeholders(infra_dir)
    typer.secho("\n> NEXT STEPS — fill these before terraform plan/apply:", fg=typer.colors.CYAN, bold=True)
    step = 1
    seen: dict[str, list[str]] = {}
    for path, line, ph in placeholders:
        seen.setdefault(ph, []).append(f"{path}:{line}")
    for ph, locations in seen.items():
        hint = _PLACEHOLDER_HINTS.get(ph, "fill in the real value")
        typer.secho(f"  {step}. {ph}", fg=typer.colors.YELLOW, bold=True)
        typer.echo(f"     what : {hint}")
        typer.echo(f"     where: {', '.join(locations[:4])}" + (" …" if len(locations) > 4 else ""))
        step += 1
    if not seen:
        typer.secho("  [OK] No unfilled placeholders — scaffold is plan-ready.", fg=typer.colors.GREEN)
    typer.echo(f"  {step}. Run: python scaffold-cli/main.py validate   (full quality gate)")
    typer.echo(f"  {step+1}. Then: cd .infra && terraform init && terraform plan -var-file=env/dev/terraform.tfvars")


def _hash_file(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _manifest_files(infra_dir: Path) -> list[Path]:
    return [
        f for f in sorted(infra_dir.rglob("*"))
        if f.is_file() and not any(part in _MANIFEST_SKIP for part in f.parts)
    ]


def _load_manifest(infra_dir: Path) -> dict:
    import json
    p = infra_dir / _MANIFEST_NAME
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_manifest(infra_dir: Path, preserve_hashes: dict | None = None) -> None:
    import json
    hashes = {
        str(f.relative_to(infra_dir)).replace("\\", "/"): _hash_file(f)
        for f in _manifest_files(infra_dir)
    }
    # User-edited files keep their last *generated* hash so they remain
    # detected as user-modified on subsequent runs.
    hashes.update(preserve_hashes or {})
    (infra_dir / _MANIFEST_NAME).write_text(
        json.dumps({"version": 1, "files": hashes}, indent=2), encoding="utf-8"
    )


def _detect_user_edits(infra_dir: Path) -> dict[str, bytes]:
    """Return {relpath: content} for files the user changed since last generation."""
    manifest = _load_manifest(infra_dir).get("files", {})
    if not manifest:
        return {}
    edited: dict[str, bytes] = {}
    for rel, old_hash in manifest.items():
        p = infra_dir / rel
        if p.exists() and _hash_file(p) != old_hash:
            edited[rel] = p.read_bytes()
    return edited


@app.command()
def doctor():
    """Diagnose the generated scaffold: placeholders, tools, and secrets."""
    import shutil as _shutil
    typer.secho("\n> Scaffold doctor\n", fg=typer.colors.CYAN, bold=True)
    problems = 0

    if not INFRA_DIR.exists():
        typer.secho("  [X] No .infra directory found. Run 'init' first.", fg=typer.colors.RED)
        raise typer.Exit(1)

    # 1. Required tools
    def _checkov_available() -> bool:
        return bool(_resolve_checkov_cmd())

    for tool, ok, required, why in [
        ("terraform", bool(_shutil.which("terraform")), True, "required to plan/apply"),
        ("checkov", _checkov_available(), True, "required for the security gate (pip install checkov)"),
        ("tfsec", bool(_resolve_tfsec_cmd()), False, "optional second security scanner (github.com/aquasecurity/tfsec/releases)"),
    ]:
        if ok:
            typer.secho(f"  [OK] {tool} available", fg=typer.colors.GREEN)
        else:
            typer.secho(f"  [!]  {tool} not found — {why}", fg=typer.colors.YELLOW)
            if required:
                problems += 1

    # 2. Unfilled placeholders
    placeholders = _scan_placeholders(INFRA_DIR)
    if placeholders:
        problems += 1
        typer.secho(f"\n  [X] {len(placeholders)} unfilled placeholder(s):", fg=typer.colors.RED, bold=True)
        for path, line, ph in placeholders[:12]:
            hint = _PLACEHOLDER_HINTS.get(ph, "fill in the real value")
            typer.echo(f"      {path}:{line}  {ph}")
            typer.echo(f"        -> {hint}")
        if len(placeholders) > 12:
            typer.echo(f"      … and {len(placeholders) - 12} more")
    else:
        typer.secho("  [OK] No unfilled placeholders", fg=typer.colors.GREEN)

    # 3. Secret scan
    if _scan_tfvars_for_secrets(INFRA_DIR):
        problems += 1

    # 4. User-modified files (informational)
    edits = _detect_user_edits(INFRA_DIR)
    if edits:
        typer.secho(f"\n  [i] {len(edits)} file(s) modified since generation (will be preserved on re-init):", fg=typer.colors.BLUE)
        for rel in list(edits)[:10]:
            typer.echo(f"      {rel}")

    if problems:
        typer.secho(f"\n  Result: {problems} issue(s) to fix before apply.", fg=typer.colors.YELLOW, bold=True)
        raise typer.Exit(1)
    typer.secho("\n  Result: scaffold is healthy.", fg=typer.colors.GREEN, bold=True)


@app.command()
def validate(
    plan: bool = typer.Option(False, "--plan", help="Also run 'terraform plan' (needs AWS credentials)"),
):
    """Full quality gate: terraform validate + Checkov + secret & placeholder scans."""
    import subprocess, shutil as _shutil

    if not INFRA_DIR.exists():
        typer.secho("ERROR: no .infra directory. Run 'init' first.", fg=typer.colors.RED)
        raise typer.Exit(1)

    import time as _time
    results: list[tuple[str, bool, str]] = []   # (gate, ok, detail)
    _t0 = _time.time()

    def _gate(n: int, label: str) -> None:
        typer.secho(f"\n> Gate {n}/5: {label}  [{_time.time() - _t0:.0f}s elapsed]",
                    fg=typer.colors.BLUE, bold=True)

    # Gate 1: terraform init + validate
    tf = _shutil.which("terraform")
    if not tf:
        results.append(("terraform validate", False, "terraform not found on PATH"))
    else:
        try:
            # Skip re-init when providers/modules are already installed —
            # `terraform init` is the slowest step (provider download ~600 MB
            # on a cold cache). A fresh generate never deletes .terraform, so
            # reuse is safe; validate falls back to init if reuse fails.
            tf_dir = INFRA_DIR / ".terraform"
            needs_init = not (tf_dir / "providers").exists()
            if needs_init:
                _gate(1, "terraform init (first run — downloading providers, this is the slow one)")
                init_r = subprocess.run(
                    [tf, "init", "-backend=false", "-input=false", "-no-color"],
                    cwd=INFRA_DIR, capture_output=True, text=True, timeout=300,
                )
            else:
                _gate(1, "terraform validate (reusing installed providers)")
                init_r = None
            if init_r is not None and init_r.returncode != 0:
                results.append(("terraform validate", False, "init failed: " + init_r.stderr.strip().splitlines()[-1][:100] if init_r.stderr.strip() else "init failed"))
            else:
                val_r = subprocess.run(
                    [tf, "validate", "-no-color"],
                    cwd=INFRA_DIR, capture_output=True, text=True, timeout=120,
                )
                if val_r.returncode != 0 and not needs_init:
                    # Stale .terraform (e.g. new module added since last init) — init once and retry
                    typer.secho("  ~ cached .terraform is stale — running terraform init...", fg=typer.colors.CYAN)
                    subprocess.run([tf, "init", "-backend=false", "-input=false", "-no-color"],
                                   cwd=INFRA_DIR, capture_output=True, text=True, timeout=300)
                    val_r = subprocess.run([tf, "validate", "-no-color"],
                                           cwd=INFRA_DIR, capture_output=True, text=True, timeout=120)
                detail = "configuration is valid" if val_r.returncode == 0 else (val_r.stderr.strip().splitlines()[-1][:100] if val_r.stderr.strip() else "validate failed")
                results.append(("terraform validate", val_r.returncode == 0, detail))
        except subprocess.TimeoutExpired:
            results.append(("terraform validate", False, "timed out"))

    # Gate 2: Checkov (reuses the scoring routine, which also prints details)
    _gate(2, "Checkov security scan (~30-60s)")
    _run_checkov_score(INFRA_DIR)
    report = INFRA_DIR / "checkov-report.txt"
    if report.exists():
        first = report.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
        ok = "100%" in first or "GOOD" in first
        results.append(("checkov security scan", ok, first))
    else:
        results.append(("checkov security scan", False, "checkov not available"))

    # Gate 3: tfsec (second security scanner — different rule engine than Checkov)
    _gate(3, "tfsec security scan")
    tfsec_ok, tfsec_detail = _run_tfsec_scan(INFRA_DIR)
    results.append(("tfsec security scan", tfsec_ok, tfsec_detail))

    _gate(4, "secret + placeholder scans")
    # Gate 4: secret scan
    n_secrets = _scan_tfvars_for_secrets(INFRA_DIR)
    results.append((
        "tfvars secret scan",
        n_secrets == 0,
        "no plaintext secrets" if n_secrets == 0 else f"{n_secrets} plaintext secret(s) found — see above",
    ))

    # Gate 4: placeholders
    placeholders = _scan_placeholders(INFRA_DIR)
    results.append((
        "placeholder check",
        not placeholders,
        "all values filled" if not placeholders else f"{len(placeholders)} unfilled — run 'doctor' for details",
    ))

    # Gate 5 (optional): terraform plan
    if plan and tf:
        plan_r = subprocess.run(
            [tf, "plan", "-var-file=env/dev/terraform.tfvars", "-input=false", "-no-color", "-lock=false"],
            cwd=INFRA_DIR, capture_output=True, text=True, timeout=600,
        )
        summary = next((l for l in plan_r.stdout.splitlines() if l.startswith("Plan:") or "No changes" in l), "")
        detail = summary or (plan_r.stderr.strip().splitlines()[-1][:100] if plan_r.stderr.strip() else "plan failed")
        results.append(("terraform plan (dev)", plan_r.returncode == 0, detail))

    # Scorecard
    typer.secho("\n> VALIDATION SCORECARD", fg=typer.colors.CYAN, bold=True)
    failed = 0
    for gate, ok, detail in results:
        mark  = "[PASS]" if ok else "[FAIL]"
        color = typer.colors.GREEN if ok else typer.colors.RED
        typer.secho(f"  {mark} {gate:<24} {detail}", fg=color)
        failed += 0 if ok else 1
    if failed:
        typer.secho(f"\n  {failed} gate(s) failed.", fg=typer.colors.RED, bold=True)
        raise typer.Exit(1)
    typer.secho("\n  All gates passed — scaffold is ready.", fg=typer.colors.GREEN, bold=True)


@app.command()
def update(
    services: list[str] = typer.Argument(
        None,
        help="Service template(s) to update (e.g. rds waf). Omit with --all for every template.",
    ),
    all_services: bool = typer.Option(False, "--all", help="Update every template-backed catalog service"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the proposed diff without writing anything"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply verified updates without confirmation"),
):
    """AI-assisted template refresh: deprecated args, provider changes, new best practices.

    The AI only PROPOSES changes — each proposal is verified by regenerating a
    full stack with the updated template and running terraform validate on it.
    Originals are backed up to parent-repo/templates/.backups/ before writing.
    """
    import difflib, shutil as _shutil, subprocess, tempfile, time
    import generator

    catalog          = dg.load_catalog()
    catalog_services = catalog.get("services", {})

    if all_services:
        targets = [s for s, e in catalog_services.items() if e.get("template")]
    elif services:
        targets = list(services)
    else:
        typer.secho("ERROR: name at least one service, or pass --all.", fg=typer.colors.RED)
        raise typer.Exit(1)

    templates_root = Path(generator.__file__).resolve().parent.parent / "parent-repo" / "templates"
    client = aic.get_client(max_tokens=8192)
    typer.secho(f"\n> AI provider: {aic.provider_info()}", fg=typer.colors.BLUE)

    _SYSTEM = (
        "You are a senior Terraform + AWS engineer maintaining Jinja2 templates that render "
        "Terraform HCL. You update templates to current provider best practices."
    )
    _RULES = """\
STRICT RULES:
- Preserve every Jinja2 construct exactly: {{ var }}, {% if %}...{% endif %}, and template variable names.
- Preserve existing #checkov:skip and #tfsec:ignore comments and their justifications.
- Do NOT rename resources or variables (that would break wiring and existing state).
- Only change what is outdated: deprecated arguments, arguments the AWS provider (~> 5.x) now requires,
  missing security best practices (encryption, least privilege, logging).
- If the template is already current, reply with exactly: NO_CHANGES
- Otherwise reply with ONLY the complete updated template content. No explanations, no code fences."""

    updated, skipped, failed = [], [], []

    for svc in targets:
        entry    = catalog_services.get(svc, {})
        template = entry.get("template")
        if not template:
            typer.secho(f"  ~ {svc}: no Jinja template (static hardened HCL) — skipping.", fg=typer.colors.CYAN)
            skipped.append(svc)
            continue
        tmpl_path = templates_root / template
        if not tmpl_path.exists():
            typer.secho(f"  ! {svc}: template {template} not found — skipping.", fg=typer.colors.YELLOW)
            skipped.append(svc)
            continue

        original = tmpl_path.read_text(encoding="utf-8")
        typer.secho(f"\n> {svc}  ({template})", fg=typer.colors.BLUE, bold=True)

        prompt = (
            f"Review this Jinja2 template that renders Terraform HCL for AWS '{svc}'.\n"
            f"Update it per the rules.\n\n{_RULES}\n\n"
            f"--- TEMPLATE ({template}) ---\n{original}"
        )
        response = client.complete(prompt, system=_SYSTEM)
        if not response:
            typer.secho("  ! No AI response — check provider key. Skipping.", fg=typer.colors.YELLOW)
            failed.append(svc)
            continue

        proposed = response.strip()
        if proposed.startswith("```"):
            proposed = "\n".join(proposed.splitlines()[1:]).rsplit("```", 1)[0]
        if proposed == "NO_CHANGES" or proposed.strip() == original.strip():
            typer.secho("  [OK] Already up to date.", fg=typer.colors.GREEN)
            continue

        diff = list(difflib.unified_diff(
            original.splitlines(), proposed.splitlines(),
            fromfile=f"{template} (current)", tofile=f"{template} (proposed)", lineterm="",
        ))
        for line in diff[:80]:
            color = (typer.colors.GREEN if line.startswith("+") else
                     typer.colors.RED if line.startswith("-") else None)
            typer.secho(f"  {line}", fg=color)
        if len(diff) > 80:
            typer.echo(f"  … {len(diff) - 80} more diff lines")

        if dry_run:
            typer.secho("  [dry-run] Not applied.", fg=typer.colors.CYAN)
            continue

        # ── Verify: regenerate a stack with the updated template, terraform validate it ──
        typer.secho("  > Verifying proposal (generate + terraform validate)...", fg=typer.colors.BLUE)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            staged_templates = tmp / "templates"
            _shutil.copytree(templates_root, staged_templates)
            (staged_templates / template).write_text(proposed, encoding="utf-8")

            verify_cfg = {
                "project": {"name": "update-verify", "region": "us-east-1", "owner": "ci"},
                "services": [svc] if svc in ("waf", "cognito") else [svc, "kms"],
                "environments": {"dev": {}},
            }
            stack_dir = tmp / "stack"
            try:
                generator.generate_scaffold(
                    verify_cfg, catalog,
                    templates_dir=str(staged_templates), output_dir=str(stack_dir),
                )
            except Exception as exc:
                typer.secho(f"  [X] Rejected — generation failed with proposal: {exc}", fg=typer.colors.RED)
                failed.append(svc)
                continue

            tf = _shutil.which("terraform")
            if tf:
                init_r = subprocess.run([tf, "init", "-backend=false", "-input=false", "-no-color"],
                                        cwd=stack_dir, capture_output=True, text=True, timeout=300)
                val_r  = subprocess.run([tf, "validate", "-no-color"],
                                        cwd=stack_dir, capture_output=True, text=True, timeout=120)
                if init_r.returncode != 0 or val_r.returncode != 0:
                    err = (val_r.stderr or init_r.stderr).strip().splitlines()
                    typer.secho(f"  [X] Rejected — terraform validate failed: {err[-1] if err else 'unknown'}",
                                fg=typer.colors.RED)
                    reject_path = templates_root / ".backups" / f"{Path(template).name}.rejected.{int(time.time())}"
                    reject_path.parent.mkdir(exist_ok=True)
                    reject_path.write_text(proposed, encoding="utf-8")
                    typer.echo(f"      Proposal saved for review: {reject_path}")
                    failed.append(svc)
                    continue
                typer.secho("  [OK] Verified: terraform validate passes with the update.", fg=typer.colors.GREEN)
            else:
                typer.secho("  [!] terraform not on PATH — verified generation only.", fg=typer.colors.YELLOW)

        if not yes and not typer.confirm(f"  Apply verified update to {template}?"):
            typer.secho("  Skipped by user.", fg=typer.colors.CYAN)
            skipped.append(svc)
            continue

        backup = templates_root / ".backups" / f"{Path(template).name}.{int(time.time())}"
        backup.parent.mkdir(exist_ok=True)
        backup.write_text(original, encoding="utf-8")
        tmpl_path.write_text(proposed, encoding="utf-8")
        typer.secho(f"  [OK] Updated {template}  (backup: {backup.relative_to(templates_root)})",
                    fg=typer.colors.GREEN, bold=True)
        updated.append(svc)

    typer.secho("\n> UPDATE SUMMARY", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  updated : {updated or '—'}")
    typer.echo(f"  skipped : {skipped or '—'}")
    typer.echo(f"  failed  : {failed or '—'}")
    if updated:
        typer.secho(
            "\n  Next: re-run 'scaffold init' in your project to regenerate with the "
            "updated templates, then 'scaffold validate'.",
            fg=typer.colors.CYAN,
        )
    if failed:
        raise typer.Exit(1)


@app.command("init-backend")
def init_backend(
    bucket: str = typer.Option(
        None, "--bucket",
        help="S3 bucket name for Terraform state. Default: <project>-tfstate-<region>",
    ),
    table: str = typer.Option(
        None, "--table",
        help="DynamoDB table name for state locking. Default: <project>-tf-locks",
    ),
    region: str = typer.Option(
        None, "--region",
        help="AWS region. Defaults to project.region from infra.yaml.",
    ),
    profile: str = typer.Option(
        None, "--profile",
        help="AWS CLI profile to use.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print the bootstrap Terraform without applying.",
    ),
):
    """Bootstrap S3 state bucket + DynamoDB lock table (run ONCE before terraform init).

    Creates a dedicated Terraform file that provisions:
      - S3 bucket with versioning, encryption, and block-public-access
      - DynamoDB table for state locking

    After running this, replace REPLACE_WITH_STATE_BUCKET and REPLACE_WITH_LOCK_TABLE
    in env/*/backend.tf with the values printed here.
    """
    config = _load_yaml()
    project = config.get("project", {})
    proj_name  = project.get("name", "myproject")
    proj_region = region or project.get("region", "us-east-1")

    bucket_name = bucket or f"{proj_name}-tfstate-{proj_region}"
    table_name  = table  or f"{proj_name}-tf-locks"

    _profile_line = f'profile = "{profile}"' if profile else '# profile = "YOUR_AWS_PROFILE"  # uncomment if needed'
    bootstrap_hcl = f'''\
# == Terraform State Backend Bootstrap ========================================
# Run ONCE to create S3 bucket + DynamoDB lock table before any other terraform command.
# After apply, update env/*/backend.tf with:
#   bucket         = "{bucket_name}"
#   dynamodb_table = "{table_name}"
# =============================================================================

terraform {{
  required_version = ">= 1.5.0"
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }}
  }}
}}

provider "aws" {{
  region  = "{proj_region}"
  {_profile_line}
}}

# S3 bucket for Terraform state
resource "aws_s3_bucket" "tf_state" {{
  bucket = "{bucket_name}"

  # Deletable by default so test projects can be fully torn down.
  # PRODUCTION: set force_destroy = false and prevent_destroy = true so the
  # state bucket (and its versioned state history) cannot be lost.
  force_destroy = true

  lifecycle {{
    prevent_destroy = false
  }}
}}

resource "aws_s3_bucket_versioning" "tf_state" {{
  bucket = aws_s3_bucket.tf_state.id
  versioning_configuration {{
    status = "Enabled"
  }}
}}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {{
  bucket = aws_s3_bucket.tf_state.id
  rule {{
    apply_server_side_encryption_by_default {{
      sse_algorithm = "AES256"
    }}
  }}
}}

resource "aws_s3_bucket_public_access_block" "tf_state" {{
  bucket                  = aws_s3_bucket.tf_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}}

# DynamoDB table for state locking
resource "aws_dynamodb_table" "tf_locks" {{
  name         = "{table_name}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {{
    name = "LockID"
    type = "S"
  }}

  lifecycle {{
    prevent_destroy = false  # PRODUCTION: set true to protect the lock table
  }}
}}

output "state_bucket" {{
  value = aws_s3_bucket.tf_state.bucket
}}

output "lock_table" {{
  value = aws_dynamodb_table.tf_locks.name
}}
'''

    typer.secho("\n> Bootstrap configuration:", fg=typer.colors.BLUE, bold=True)
    typer.echo(f"  S3 bucket     : {bucket_name}")
    typer.echo(f"  DynamoDB table: {table_name}")
    typer.echo(f"  Region        : {proj_region}")

    bootstrap_dir  = Path("bootstrap-backend")
    bootstrap_file = bootstrap_dir / "main.tf"

    if dry_run:
        typer.secho("\n=== DRY RUN — HCL that would be written ===", fg=typer.colors.MAGENTA)
        typer.echo(bootstrap_hcl)
        typer.secho("=== END DRY RUN ===", fg=typer.colors.MAGENTA)
        return

    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    bootstrap_file.write_text(bootstrap_hcl, encoding="utf-8")

    typer.secho(f"\n  Written: {bootstrap_file.absolute()}", fg=typer.colors.GREEN)
    typer.secho(
        "\n> Apply steps:\n"
        f"  1. cd {bootstrap_dir}\n"
        f"  2. terraform init\n"
        f"  3. terraform apply\n"
        f"\n  After apply, update env/*/backend.tf:\n"
        f'     bucket         = "{bucket_name}"\n'
        f'     dynamodb_table = "{table_name}"\n',
        fg=typer.colors.CYAN,
    )


@app.command()
def services():
    """List all services available in the catalog."""
    catalog = dg.load_catalog()
    by_category: dict[str, list[str]] = {}
    for name, entry in catalog.get("services", {}).items():
        cat = entry.get("category", "other")
        by_category.setdefault(cat, []).append(name)

    typer.secho("\nAvailable services (from services_catalog.yaml):\n", fg=typer.colors.CYAN, bold=True)
    for cat in sorted(by_category):
        typer.secho(f"  {cat}:", fg=typer.colors.YELLOW)
        for svc in sorted(by_category[cat]):
            entry    = catalog["services"][svc]
            has_tmpl = entry.get("template") is not None
            label    = "static template" if has_tmpl else "AI-generated"
            typer.echo(f"    {svc:<25} [{label}]")
    typer.echo("")


@app.command()
def providers():
    """Show configured AI provider and model."""
    typer.secho("\nAI provider status:\n", fg=typer.colors.CYAN, bold=True)
    import os
    issues: list[str] = []
    for name, cfg in aic.PROVIDER_CONFIG.items():
        key_val = os.environ.get(cfg["key_env"], "")
        key_set = bool(key_val)
        model   = (os.environ.get("AI_MODEL") or cfg["default_model"]) if os.environ.get("AI_PROVIDER", "claude") == name else cfg["default_model"]
        active  = "(active)" if os.environ.get("AI_PROVIDER", "claude") == name else ""
        status  = "ready" if key_set else f"{cfg['key_env']} not set"
        color   = typer.colors.GREEN if key_set else typer.colors.YELLOW

        # Detect common key formatting mistakes
        if key_set:
            if key_val.startswith('"') or key_val.endswith('"'):
                status = "KEY HAS QUOTES -- remove the \" characters"
                color  = typer.colors.RED
                issues.append(f"  {cfg['key_env']} starts/ends with quotes. Run: set {cfg['key_env']}={key_val.strip(chr(34))}")
            elif key_val.startswith("'") or key_val.endswith("'"):
                status = "KEY HAS QUOTES -- remove the ' characters"
                color  = typer.colors.RED
                issues.append(f"  {cfg['key_env']} starts/ends with single quotes.")
            elif key_val.startswith(" ") or key_val.endswith(" "):
                status = "KEY HAS SPACES -- remove leading/trailing spaces"
                color  = typer.colors.RED
                issues.append(f"  {cfg['key_env']} has leading/trailing spaces. Run: set {cfg['key_env']}={key_val.strip()}")

        typer.secho(
            f"  {name:<10} {model:<30} [{status}]  {active}",
            fg=color,
        )

    if issues:
        typer.secho("\n  ! Key formatting problems detected:", fg=typer.colors.RED, bold=True)
        for issue in issues:
            typer.secho(issue, fg=typer.colors.RED)
        typer.secho("\n  Fix: set the key WITHOUT quotes or spaces:", fg=typer.colors.YELLOW)
        typer.secho("    CORRECT:   set MOONSHOT_API_KEY=sk-abc123", fg=typer.colors.GREEN)
        typer.secho('    WRONG:     set MOONSHOT_API_KEY="sk-abc123"', fg=typer.colors.RED)

    typer.echo(
        "\n  Set AI_PROVIDER=claude|openai|gemini|kimi  and the matching API key env var.\n"
        "  Set AI_MODEL to override the default model.\n"
        "  Or pass --ai-provider / --ai-model flags to the init command.\n"
        "\n  Provider keys:\n"
        "    claude:  ANTHROPIC_API_KEY   (starts with sk-ant-)\n"
        "    openai:  OPENAI_API_KEY     (starts with sk-)\n"
        "    gemini:  GOOGLE_API_KEY     (starts with AIza)\n"
        "    kimi:    OPENROUTER_API_KEY (from openrouter.ai, starts with sk-or-)\n"
    )


if __name__ == "__main__":
    app()
