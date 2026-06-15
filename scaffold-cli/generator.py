from pathlib import Path
import typer
from jinja2 import Environment, FileSystemLoader


# ---------------------------------------------------------------------------
# Service → template mappings
# ---------------------------------------------------------------------------

COMPUTE_SERVICES = {"lambda", "ecs-fargate", "eks"}

# One compute template per compute target
COMPUTE_TEMPLATE = {
    "lambda":      "iac/compute/lambda.tf.j2",
    "ecs-fargate": "iac/compute/ecs-fargate.tf.j2",
    "eks":         "iac/compute/eks.tf.j2",
}

# IAM template(s) per compute target — EKS needs two (pod role + cluster/node role)
IAM_TEMPLATES = {
    "lambda":      ["iac/iam/lambda.tf.j2"],
    "ecs-fargate": ["iac/iam/ecs.tf.j2"],
    "eks":         ["iac/iam/ecs.tf.j2", "iac/iam/eks.tf.j2"],
}

# Data / messaging / auth / AI service → (template, extra_vars)
DATA_TEMPLATE = {
    "postgres":    ("iac/data/rds.tf.j2",         {"db_engine": "postgres"}),
    "mysql":       ("iac/data/rds.tf.j2",         {"db_engine": "mysql"}),
    "redis":       ("iac/data/redis.tf.j2",       {}),
    "s3":          ("iac/data/s3.tf.j2",          {}),
    "dynamodb":    ("iac/data/dynamodb.tf.j2",    {}),
    "sqs":         ("iac/data/sqs.tf.j2",         {}),
    "eventbridge": ("iac/data/eventbridge.tf.j2", {}),
    "cognito":     ("iac/data/cognito.tf.j2",     {}),
    "kms":         ("iac/data/kms.tf.j2",         {}),
    # AI/ML — no Terraform resource; only IAM policies are generated in iam/lambda.tf.j2
    "bedrock":     ("iac/data/bedrock.tf.j2",     {}),
    "polly":       ("iac/data/polly.tf.j2",       {}),
    # Frontend CDN — CloudFront + S3 static hosting
    "static-site": ("iac/compute/static-site.tf.j2", {}),
}

# Ingress add-ons — appended to compute.tf, only valid for certain compute targets
INGRESS_TEMPLATE = {
    "alb":         ("iac/compute/alb.tf.j2",         {"ecs-fargate", "eks"}),
    "api-gateway": ("iac/compute/api-gateway.tf.j2", {"lambda", "ecs-fargate", "eks"}),
}


# ---------------------------------------------------------------------------
# Jinja2 environments
# ---------------------------------------------------------------------------

def _make_env(templates_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _make_cicd_env(templates_dir: Path) -> Environment:
    """Custom delimiters so GitHub Actions ${{ }} passes through unchanged."""
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        variable_start_string="<<",  variable_end_string=">>",
        block_start_string="<%",     block_end_string="%>",
        comment_start_string="<#",   comment_end_string="#>",
        trim_blocks=True,
        lstrip_blocks=True,
    )


# ---------------------------------------------------------------------------
# Implicit connection inference (fallback when connections: not declared)
# ---------------------------------------------------------------------------

def _implicit_connections(services: list, compute_target: str) -> set:
    """
    Infer standard wiring from service co-presence.
    Used only when infra.yaml has no connections: section.
    """
    s = set(services)
    conns = set()

    # Messaging chains
    if "eventbridge" in s and "sqs" in s:
        conns.add("eventbridge->sqs")
    if "eventbridge" in s and "sqs" not in s and compute_target == "lambda":
        conns.add("eventbridge->lambda")
    if "sqs" in s and compute_target == "lambda":
        conns.add("sqs->lambda")
    if "sqs" in s and compute_target == "ecs-fargate":
        conns.add("sqs->ecs-fargate")

    # Ingress
    if "alb" in s and compute_target in ("ecs-fargate", "eks"):
        conns.add(f"alb->{compute_target}")
    if "api-gateway" in s and compute_target == "lambda":
        conns.add("api-gateway->lambda")

    # Compute → data stores (write access)
    for store in ("postgres", "mysql", "redis", "dynamodb", "s3"):
        if store in s:
            conns.add(f"{compute_target}->{store}")

    return conns


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def _render(env: Environment, template_path: str, output_path: Path, ctx: dict) -> None:
    try:
        content = env.get_template(template_path).render(**ctx)
        output_path.write_text(content + "\n")
        typer.secho(f"  + {output_path.name}  [{template_path}]", fg=typer.colors.GREEN)
    except Exception as e:
        typer.secho(f"  ! failed {template_path}: {e}", fg=typer.colors.YELLOW)


def _render_combined(env: Environment, template_paths: list, output_path: Path,
                     ctx: dict, labels: list = None) -> None:
    """Render multiple templates, join them, write to a single output file."""
    blocks = []
    for i, tp in enumerate(template_paths):
        label = (labels[i] if labels else None) or tp
        try:
            blocks.append(env.get_template(tp).render(**ctx))
            typer.secho(f"  + {output_path.name}  [{label}]", fg=typer.colors.GREEN)
        except Exception as e:
            typer.secho(f"  ! failed {tp}: {e}", fg=typer.colors.YELLOW)
    if blocks:
        output_path.write_text("\n".join(blocks) + "\n")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_scaffold(config: dict, output_dir: str = ".infra",
                      templates_dir: str = "../parent-repo/templates") -> None:
    base     = Path(output_dir)
    tmpl_dir = Path(templates_dir)

    # --- Unpack config ---
    project      = config["project"]
    project_name = project["name"]
    region       = project["region"]
    owner        = project["owner"]
    services     = config.get("services", [])
    cicd_cfg     = config.get("cicd", {})

    compute_list   = [s for s in services if s in COMPUTE_SERVICES]
    compute_target = compute_list[0]          # primary (used in IAM/connection checks)
    other_services = [s for s in services if s not in COMPUTE_SERVICES]
    environments   = config.get("environments", {})
    flows          = config.get("flows", {})

    # data_stores = non-compute, non-ingress services (used by IAM conditional blocks)
    ingress_keys  = set(INGRESS_TEMPLATE)
    data_stores   = [s for s in other_services if s in DATA_TEMPLATE and s not in ingress_keys]
    auth_required = "cognito" in services

    # CI/CD gate: auto-deploy dev only unless staging is also in auto_deploy
    auto_deploy = cicd_cfg.get("auto_deploy", ["dev"])
    cicd_envs   = "auto-dev-staging" if "staging" in auto_deploy else "auto-dev"

    # --- Parse connections into a set of "from->to" strings ---
    # Templates check:  {% if 'sqs->lambda' in connections %}
    raw_connections = config.get("connections", [])
    if raw_connections:
        connections = {
            f"{c['from']}->{c['to']}"
            for c in raw_connections
            if "from" in c and "to" in c
        }
    else:
        # No connections declared — build implicit ones from service co-presence
        connections = _implicit_connections(services, compute_target)

    # Template context — shared across all templates
    ctx = {
        "project_name":     project_name,
        "project_type":     "web-api",      # kept for template compatibility
        "region":           region,
        "owner":            owner,
        "compute_target":   compute_target,
        "services":         services,
        "data_stores":      data_stores,
        "auth_required":    auth_required,
        "connections":      connections,
        "flows":            flows,
        "vpc_cidr":         "10.0.0.0/16",
        "multi_az":         False,
        "backup_retention": 7,
        "cicd_envs":        cicd_envs,
        "log_retention":    {"dev": 30, "staging": 90, "prod": 365},
    }

    # --- Create output directories ---
    for d in ["iac", "cicd", "environments", "secrets"]:
        (base / d).mkdir(parents=True, exist_ok=True)

    jinja_env = _make_env(tmpl_dir)
    cicd_env  = _make_cicd_env(tmpl_dir)

    typer.echo(f"\n> Scaffolding '{project_name}' -> {base.absolute()}")
    typer.echo(f"  compute     = {' + '.join(compute_list)}")
    typer.echo(f"  services    = {', '.join(other_services) or 'none'}")
    typer.echo(f"  connections = {', '.join(sorted(connections)) or 'none'}")
    if environments:
        typer.echo(f"  envs        = {', '.join(environments.keys())}")
    typer.secho("\n> Generating templates...", fg=typer.colors.BLUE)

    # -----------------------------------------------------------------------
    # Always-rendered files
    # -----------------------------------------------------------------------
    _write_providers(base, project_name, region, owner, tmpl_dir)
    _render(jinja_env, "iac/variables.tf.j2",     base / "iac/variables.tf",     ctx)
    _render(jinja_env, "iac/outputs.tf.j2",       base / "iac/outputs.tf",       ctx)
    _render(jinja_env, "iac/networking.tf.j2",    base / "iac/networking.tf",    ctx)
    _render(jinja_env, "iac/observability.tf.j2", base / "iac/observability.tf", ctx)

    # -----------------------------------------------------------------------
    # compute.tf — all compute targets + ingress add-ons
    # -----------------------------------------------------------------------
    compute_templates = [COMPUTE_TEMPLATE[c] for c in compute_list]
    compute_labels    = list(compute_list)

    for ingress_svc, (ingress_tmpl, allowed_compute) in INGRESS_TEMPLATE.items():
        if ingress_svc in services and any(c in allowed_compute for c in compute_list):
            compute_templates.append(ingress_tmpl)
            compute_labels.append(ingress_svc)

    _render_combined(jinja_env, compute_templates, base / "iac/compute.tf",
                     ctx, labels=compute_labels)

    # -----------------------------------------------------------------------
    # iam.tf — merge IAM templates for all compute targets (deduplicated)
    # -----------------------------------------------------------------------
    iam_templates = []
    seen_iam = set()
    for c in compute_list:
        for t in IAM_TEMPLATES[c]:
            if t not in seen_iam:
                seen_iam.add(t)
                iam_templates.append(t)
    _render_combined(jinja_env, iam_templates, base / "iac/iam.tf", ctx)

    # -----------------------------------------------------------------------
    # data.tf — one block per declared data / messaging / auth service
    # -----------------------------------------------------------------------
    data_blocks = []
    for svc in other_services:
        if svc in DATA_TEMPLATE:
            tmpl_path, extra_vars = DATA_TEMPLATE[svc]
            merged = {**ctx, **extra_vars}
            try:
                data_blocks.append(jinja_env.get_template(tmpl_path).render(**merged))
                typer.secho(f"  + iac/data.tf  [{svc}]", fg=typer.colors.GREEN)
            except Exception as e:
                typer.secho(f"  ! failed {svc} ({tmpl_path}): {e}", fg=typer.colors.YELLOW)

    # Auto-add KMS whenever any data service is present (unless KMS already listed)
    if data_blocks and "kms" not in services:
        try:
            data_blocks.append(jinja_env.get_template("iac/data/kms.tf.j2").render(**ctx))
            typer.secho("  + iac/data.tf  [kms — auto-added]", fg=typer.colors.GREEN)
        except Exception as e:
            typer.secho(f"  ! failed kms: {e}", fg=typer.colors.YELLOW)

    if data_blocks:
        (base / "iac/data.tf").write_text("\n".join(data_blocks) + "\n")
    else:
        (base / "iac/data.tf").write_text("# No data services configured\n")
        typer.secho("  + iac/data.tf  [empty]", fg=typer.colors.WHITE)

    # -----------------------------------------------------------------------
    # CI/CD pipeline
    # -----------------------------------------------------------------------
    _render(cicd_env, "cicd/pipeline.yml.j2", base / "cicd/pipeline.yml", ctx)

    # -----------------------------------------------------------------------
    # Config files (not Terraform)
    # -----------------------------------------------------------------------
    _write_tfvars(base, project_name, owner, environments)
    _write_secrets_policy(base, project_name, data_stores)
    _write_gitignore(base)

    typer.secho("\n> Scaffold complete.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Output: {base.absolute()}")


# ---------------------------------------------------------------------------
# Config file writers (static content, not templated)
# ---------------------------------------------------------------------------

def _write_providers(base: Path, project_name: str, region: str, owner: str,
                     tmpl_dir: Path) -> None:
    tmpl_path = tmpl_dir / "providers.tf.j2"
    if tmpl_path.exists():
        try:
            env     = Environment(loader=FileSystemLoader(str(tmpl_dir)))
            content = env.get_template("providers.tf.j2").render(
                project=project_name, region=region, owner=owner
            )
            (base / "iac/providers.tf").write_text(content + "\n")
            typer.secho("  + iac/providers.tf  [providers.tf.j2]", fg=typer.colors.GREEN)
            return
        except Exception:
            pass

    # Inline fallback
    (base / "iac/providers.tf").write_text(
        f'terraform {{\n'
        f'  required_version = ">= 1.5.0"\n\n'
        f'  required_providers {{\n'
        f'    aws = {{\n'
        f'      source  = "hashicorp/aws"\n'
        f'      version = "~> 5.0"\n'
        f'    }}\n'
        f'  }}\n\n'
        f'  backend "s3" {{\n'
        f'    encrypt        = true\n'
        f'    dynamodb_table = "terraform-locks"\n'
        f'  }}\n'
        f'}}\n\n'
        f'provider "aws" {{\n'
        f'  region = var.region\n\n'
        f'  default_tags {{\n'
        f'    tags = {{\n'
        f'      Project   = "{project_name}"\n'
        f'      Owner     = "{owner}"\n'
        f'      ManagedBy = "devops-scaffold-tool"\n'
        f'    }}\n'
        f'  }}\n'
        f'}}\n'
    )
    typer.secho("  + iac/providers.tf  [inline fallback]", fg=typer.colors.YELLOW)


def _write_tfvars(base: Path, project_name: str, owner: str,
                  environments: dict = None) -> None:
    """
    Generate one .tfvars.example per environment.
    If environments: section exists in infra.yaml, use those env names and
    include their specific overrides as commented variable hints.
    Falls back to dev / staging / prod if no environments declared.
    Stale .tfvars.example files from previous runs are removed first.
    """
    env_dir = base / "environments"
    for stale in env_dir.glob("*.tfvars.example"):
        stale.unlink()

    env_names = list(environments.keys()) if environments else ["dev", "staging", "prod"]

    for env_name in env_names:
        env_overrides = (environments or {}).get(env_name, {})

        lines = [
            f'environment = "{env_name}"',
            f'region      = "REPLACE_WITH_REGION"',
            f'cost_centre = "REPLACE_WITH_COST_CENTRE"',
            f"",
            f'# Tags set automatically in providers.tf:',
            f'#   Project   = "{project_name}"',
            f'#   Owner     = "{owner}"',
            f'#   ManagedBy = "devops-scaffold-tool"',
        ]

        # Emit environment-specific overrides as concrete variables
        if env_overrides:
            lines.append("")
            lines.append(f"# --- {env_name}-specific settings ---")

            eks_cfg = env_overrides.get("eks", {})
            if eks_cfg.get("node_count"):
                lines.append(f'eks_node_count    = {eks_cfg["node_count"]}')
            if eks_cfg.get("instance_type"):
                lines.append(f'eks_instance_type = "{eks_cfg["instance_type"]}"')

            ddb_cfg = env_overrides.get("dynamodb", {})
            if ddb_cfg.get("billing_mode"):
                lines.append(f'dynamodb_billing_mode = "{ddb_cfg["billing_mode"]}"')

            if "multi_az" in env_overrides:
                lines.append(f'multi_az = {str(env_overrides["multi_az"]).lower()}')

        (base / f"environments/{env_name}.tfvars.example").write_text(
            "\n".join(lines) + "\n"
        )


def _write_secrets_policy(base: Path, project_name: str, data_stores: list) -> None:
    content = (
        "# Secrets structure\n"
        "# Use AWS Secrets Manager or SSM Parameter Store.\n"
        "# NEVER hardcode values — this file defines paths/structure only.\n\nsecrets:\n"
    )
    if "postgres" in data_stores or "mysql" in data_stores:
        content += (
            f'  - path: "/{project_name}/{{environment}}/db/password"\n'
            f'    service: "AWS Secrets Manager"\n'
            f'    description: "RDS master password — auto-rotated"\n'
        )
    if "redis" in data_stores:
        content += (
            f'  - path: "/{project_name}/{{environment}}/redis/auth-token"\n'
            f'    service: "AWS Secrets Manager"\n'
            f'    description: "ElastiCache auth token"\n'
        )
    content += (
        f'  - path: "/{project_name}/{{environment}}/app/secret-key"\n'
        f'    service: "AWS SSM Parameter Store"\n'
        f'    description: "Application secret key"\n'
    )
    (base / "secrets/secrets-policy.yml").write_text(content)


def _write_gitignore(base: Path) -> None:
    (base / ".gitignore").write_text(
        "**/.terraform/*\n*.tfstate\n*.tfstate.*\n"
        "crash.log\ncrash.*.log\n.terraform.tfvars\n"
        "*.tfvars\n!*.tfvars.example\n"
    )
