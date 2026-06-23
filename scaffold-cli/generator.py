"""
generator.py  (v3 " env-per-folder, variables as declarations only)
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
Output structure:
  .infra/
    provider.tf          terraform + provider blocks + locals
    networking.tf        VPC module
    main.tf              compute resources (EKS, Lambda, ECS, EC2)
    data.tf              databases, caches, queues, storage
    iam.tf               IAM roles + policies
    observability.tf     CloudWatch, X-Ray
    output.tf            Terraform outputs
    variables.tf         Variable DECLARATIONS only " no hardcoded defaults
    env/
      {env}/
        backend.tf               S3 backend config pointing to env state file
        terraform.tfvars         Actual variable values for this environment
        terraform.tfvars.example Checked-in example with placeholder comments
    cicd/
      pipeline.yml
    secrets/
      secrets-policy.yml

Variables flow:
  variables.tf   declarations (name + type + description, no default)
  env/{env}/terraform.tfvars  actual values per environment
    Base vars  : project_name, region, owner, environment, vpc_cidr, cost_centre
    Static vars: well-known per-service vars (eks_instance_type, db_instance_class)
    Dynamic vars: Claude API returns variables[] alongside terraform_hcl
"""

from pathlib import Path
from typing import Any
import typer
from jinja2 import Environment, FileSystemLoader

import sys, importlib.util
from pathlib import Path as _Path
_dg_path = _Path(__file__).parent / "dynamic_generator.py"
if "dynamic_generator" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("dynamic_generator", _dg_path)
    dg    = importlib.util.module_from_spec(_spec)
    sys.modules["dynamic_generator"] = dg
    _spec.loader.exec_module(dg)
else:
    dg = sys.modules["dynamic_generator"]


# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
# Base variables " always present in every stack
# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
BASE_VARS: list[dict] = [
    {"name": "project_name", "type": "string",
     "description": "Project name used in resource naming and tags"},
    {"name": "region",       "type": "string",
     "description": "AWS region to deploy resources into"},
    {"name": "owner",        "type": "string",
     "description": "Team or individual owning this infrastructure"},
    {"name": "environment",  "type": "string",
     "description": "Deployment environment (dev, staging, prod, uat)"},
    {"name": "vpc_cidr",     "type": "string",
     "description": "CIDR block for the VPC (e.g. 10.0.0.0/16)"},
    {"name": "cost_centre",  "type": "string",
     "description": "Cost centre code for billing and tagging"},
]

# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
# Well-known per-service variables for static-template services
# Each entry has per-environment recommended values (dev / staging / prod).
# The fuzzy env mapper in dynamic_generator._env_value_for handles aliases
# like "uat" ' staging, "live" ' prod automatically.
# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
STATIC_SERVICE_VARS: dict[str, list[dict]] = {
    "eks": [
        {"name": "eks_node_count",      "type": "number",
         "description": "Number of EKS worker nodes",
         "dev": 1, "staging": 2, "prod": 3},
        {"name": "eks_instance_type",   "type": "string",
         "description": "EC2 instance type for EKS worker nodes",
         "dev": "t3.medium", "staging": "m5.large", "prod": "m5.xlarge"},
        {"name": "eks_cluster_version", "type": "string",
         "description": "Kubernetes version for the EKS cluster",
         "dev": "1.33", "staging": "1.33", "prod": "1.33"},
    ],
    "lambda": [
        {"name": "lambda_memory_size", "type": "number",
         "description": "Lambda function memory in MB",
         "dev": 256, "staging": 512, "prod": 1024},
        {"name": "lambda_timeout",     "type": "number",
         "description": "Lambda function timeout in seconds",
         "dev": 30, "staging": 30, "prod": 30},
        {"name": "lambda_s3_bucket",   "type": "string",
         "description": "S3 bucket containing the Lambda deployment package",
         "dev": "REPLACE_WITH_DEPLOY_BUCKET", "staging": "REPLACE_WITH_DEPLOY_BUCKET",
         "prod": "REPLACE_WITH_DEPLOY_BUCKET"},
        {"name": "lambda_s3_key",      "type": "string",
         "description": "S3 key path to the Lambda deployment zip",
         "dev": "lambda/app.zip", "staging": "lambda/app.zip", "prod": "lambda/app.zip"},
    ],
    "ecs-fargate": [
        {"name": "ecs_task_cpu",      "type": "number",
         "description": "ECS task CPU units (256 = 0.25 vCPU)",
         "dev": 256, "staging": 512, "prod": 1024},
        {"name": "ecs_task_memory",   "type": "number",
         "description": "ECS task memory in MiB",
         "dev": 512, "staging": 1024, "prod": 2048},
        {"name": "ecs_desired_count", "type": "number",
         "description": "Desired number of running ECS tasks",
         "dev": 1, "staging": 2, "prod": 3},
    ],
    "ec2": [
        {"name": "ec2_instance_type",  "type": "string",
         "description": "EC2 instance type",
         "dev": "t3.micro", "staging": "t3.small", "prod": "m5.large"},
        {"name": "ec2_instance_count", "type": "number",
         "description": "Number of EC2 instances",
         "dev": 1, "staging": 2, "prod": 3},
    ],
    "postgres": [
        {"name": "db_instance_class",    "type": "string",
         "description": "RDS instance class",
         "dev": "db.t3.micro", "staging": "db.t3.small", "prod": "db.m5.large"},
        {"name": "db_allocated_storage", "type": "number",
         "description": "RDS allocated storage in GB",
         "dev": 20, "staging": 50, "prod": 100},
        {"name": "db_multi_az",          "type": "bool",
         "description": "Enable RDS Multi-AZ for high availability",
         "dev": False, "staging": False, "prod": True},
    ],
    "mysql": [
        {"name": "db_instance_class",    "type": "string",
         "description": "RDS instance class",
         "dev": "db.t3.micro", "staging": "db.t3.small", "prod": "db.m5.large"},
        {"name": "db_allocated_storage", "type": "number",
         "description": "RDS allocated storage in GB",
         "dev": 20, "staging": 50, "prod": 100},
        {"name": "db_multi_az",          "type": "bool",
         "description": "Enable RDS Multi-AZ for high availability",
         "dev": False, "staging": False, "prod": True},
    ],
    "aurora-postgres": [
        {"name": "aurora_instance_class", "type": "string",
         "description": "Aurora instance class",
         "dev": "db.t3.medium", "staging": "db.r5.large", "prod": "db.r5.xlarge"},
        {"name": "aurora_instance_count", "type": "number",
         "description": "Number of Aurora cluster instances",
         "dev": 1, "staging": 2, "prod": 3},
    ],
    "aurora-mysql": [
        {"name": "aurora_instance_class", "type": "string",
         "description": "Aurora instance class",
         "dev": "db.t3.medium", "staging": "db.r5.large", "prod": "db.r5.xlarge"},
        {"name": "aurora_instance_count", "type": "number",
         "description": "Number of Aurora cluster instances",
         "dev": 1, "staging": 2, "prod": 3},
    ],
    "redis": [
        {"name": "redis_node_type",  "type": "string",
         "description": "ElastiCache node type for Redis",
         "dev": "cache.t3.micro", "staging": "cache.t3.small", "prod": "cache.m5.large"},
        {"name": "redis_num_nodes",  "type": "number",
         "description": "Number of Redis cache nodes",
         "dev": 1, "staging": 1, "prod": 2},
    ],
    "dynamodb": [
        {"name": "dynamodb_billing_mode", "type": "string",
         "description": "DynamoDB billing mode: PROVISIONED or PAY_PER_REQUEST",
         "dev": "PAY_PER_REQUEST", "staging": "PAY_PER_REQUEST", "prod": "PROVISIONED"},
    ],
    "alb": [
        {"name": "alb_idle_timeout", "type": "number",
         "description": "ALB connection idle timeout in seconds",
         "dev": 60, "staging": 60, "prod": 60},
    ],
    "opensearch": [
        {"name": "opensearch_instance_type",  "type": "string",
         "description": "OpenSearch instance type",
         "dev": "t3.small.search", "staging": "m5.large.search", "prod": "m5.xlarge.search"},
        {"name": "opensearch_instance_count", "type": "number",
         "description": "Number of OpenSearch data nodes",
         "dev": 1, "staging": 2, "prod": 3},
    ],
    "api-gateway": [
        {"name": "api_gateway_stage", "type": "string",
         "description": "API Gateway deployment stage name",
         "dev": "dev", "staging": "staging", "prod": "prod"},
    ],
}


# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
# Jinja2 environments
# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

def _make_env(templates_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _make_cicd_env(templates_dir: Path) -> Environment:
    """Custom delimiters so GitHub Actions ${{ }} syntax passes through unchanged."""
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        variable_start_string="<<",  variable_end_string=">>",
        block_start_string="<%",     block_end_string="%>",
        comment_start_string="<#",   comment_end_string="#>",
        trim_blocks=True,
        lstrip_blocks=True,
    )


# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
# Implicit connection inference
# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

def _implicit_connections(services: list, compute_target: str) -> set:
    s = set(services)
    conns = set()
    if "eventbridge" in s and "sqs" in s:
        conns.add("eventbridge->sqs")
    if "eventbridge" in s and "sqs" not in s and compute_target == "lambda":
        conns.add("eventbridge->lambda")
    if "sqs" in s and compute_target == "lambda":
        conns.add("sqs->lambda")
    if "sqs" in s and compute_target == "ecs-fargate":
        conns.add("sqs->ecs-fargate")
    if "alb" in s and compute_target in ("ecs-fargate", "eks"):
        conns.add(f"alb->{compute_target}")
    if "api-gateway" in s and compute_target == "lambda":
        conns.add("api-gateway->lambda")
    for store in ("postgres", "mysql", "redis", "dynamodb", "s3",
                  "opensearch", "kinesis", "msk"):
        if store in s:
            conns.add(f"{compute_target}->{store}")
    return conns


# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
# Render helpers
# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

def _render(env: Environment, template_path: str, output_path: Path, ctx: dict) -> None:
    try:
        content = env.get_template(template_path).render(**ctx)
        output_path.write_text(content + "\n", encoding="utf-8")
        typer.secho(f"  + {output_path.name}  [{template_path}]", fg=typer.colors.GREEN)
    except Exception as e:
        typer.secho(f"  ! failed {template_path}: {e}", fg=typer.colors.YELLOW)


def _render_combined(env: Environment, template_paths: list, output_path: Path,
                     ctx: dict, labels: list = None) -> None:
    blocks = []
    for i, tp in enumerate(template_paths):
        label = (labels[i] if labels else None) or tp
        try:
            blocks.append(env.get_template(tp).render(**ctx))
            typer.secho(f"  + {output_path.name}  [{label}]", fg=typer.colors.GREEN)
        except Exception as e:
            typer.secho(f"  ! failed {tp}: {e}", fg=typer.colors.YELLOW)
    if blocks:
        output_path.write_text("\n".join(blocks) + "\n", encoding="utf-8")


def _render_per_label(env: Environment, template_paths: list,
                      ctx: dict, labels: list = None) -> dict[str, str]:
    """Render each template and return a dict of label -> rendered HCL."""
    result: dict[str, str] = {}
    for i, tp in enumerate(template_paths):
        label = (labels[i] if labels else None) or tp
        try:
            result[label] = env.get_template(tp).render(**ctx)
        except Exception as e:
            typer.secho(f"  ! failed {tp}: {e}", fg=typer.colors.YELLOW)
    return result


# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
# Main entry point
# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

def generate_scaffold(
    config: dict,
    catalog: dict,
    output_dir: str = ".infra",
    templates_dir: str = "../parent-repo/templates",
) -> None:
    base     = Path(output_dir)
    tmpl_dir = Path(templates_dir)

    # "" Unpack config """"""""""""""""""""""""""""""""""""""""""""""""""""""
    project      = config["project"]
    project_name = project["name"]
    region       = project["region"]
    owner        = project["owner"]
    services     = config.get("services", [])
    cicd_cfg     = config.get("cicd", {})
    environments = config.get("environments", {})
    flows        = config.get("flows", {})
    env_names    = list(environments.keys()) if environments else ["dev", "staging", "prod"]

    # "" Resolve services from catalog """"""""""""""""""""""""""""""""""""""
    compute_service_names = dg.get_compute_services(catalog)
    compute_list          = [s for s in services if s in compute_service_names]
    compute_target        = compute_list[0]
    other_services        = [s for s in services if s not in compute_service_names]

    catalog_services = catalog.get("services", {})
    ingress_svcs     = {
        name: entry
        for name, entry in catalog_services.items()
        if entry.get("valid_compute_targets")
    }
    ingress_keys  = set(ingress_svcs.keys())
    data_stores   = [s for s in other_services if s not in ingress_keys]
    auth_required = "cognito" in services

    auto_deploy = cicd_cfg.get("auto_deploy", ["dev"])
    cicd_envs   = "auto-dev-staging" if "staging" in auto_deploy else "auto-dev"

    raw_connections = config.get("connections", [])
    if raw_connections:
        connections = {
            f"{c['from']}->{c['to']}"
            for c in raw_connections
            if "from" in c and "to" in c
        }
    else:
        connections = _implicit_connections(services, compute_target)

    # "" Jinja2 environments """"""""""""""""""""""""""""""""""""""""""""""""
    jinja_env = _make_env(tmpl_dir)
    cicd_env  = _make_cicd_env(tmpl_dir)

    # -- Output directory structure -------------------------------------------
    for sub in ["cicd", "secrets"]:
        (base / sub).mkdir(parents=True, exist_ok=True)
    for env_name in env_names:
        (base / "env" / env_name).mkdir(parents=True, exist_ok=True)

    # Clean up stale per-service .tf files from previous runs so renamed/removed
    # services don't leave orphan files. Keep fixed-name files managed elsewhere.
    _FIXED_TF_FILES = {
        "provider.tf", "networking.tf", "main.tf", "iam.tf",
        "observability.tf", "output.tf", "variables.tf",
    }
    for tf_file in base.glob("*.tf"):
        if tf_file.name not in _FIXED_TF_FILES:
            tf_file.unlink()  # removes stale service files AND the old data.tf

    # modules/ -- one sub-folder per local reusable module
    _write_modules_scaffold(base, services)

    # Observability config
    observability  = config.get("observability", {})
    log_retention  = observability.get("log_retention_days", 30)
    enable_xray    = observability.get("xray", False)
    enable_metrics = observability.get("metrics", True)

    # Shared template context
    ctx = {
        "project_name":   project_name,
        "project_type":   config.get("project", {}).get("type", "web-api"),
        "region":         region,
        "owner":          owner,
        "compute_target": compute_target,
        "compute":        compute_list,   # list of compute services e.g. ["lambda", "eks"]
        "services":       services,       # all services including non-compute
        "data_stores":    data_stores,
        "auth_required":  auth_required,
        "connections":    connections,
        "flows":          flows,
        "environments":   environments,
        "cicd_envs":      cicd_envs,
        "log_retention":  log_retention,
        "enable_xray":    enable_xray,
        "enable_metrics": enable_metrics,
    }

    # "" Collect variables from all sources """""""""""""""""""""""""""""""""
    # All dynamic_vars are accumulated here; written to variables.tf + tfvars at end.
    dynamic_vars: list[dict] = []

    for svc in compute_list + list(other_services):
        if svc in STATIC_SERVICE_VARS:
            dynamic_vars.extend(STATIC_SERVICE_VARS[svc])

    # "" provider.tf """""""""""""""""""""""""""""""""""""""""""""""""""""""
    _write_provider_tf(base, project_name, region, owner, catalog)

    # "" networking.tf (VPC) """""""""""""""""""""""""""""""""""""""""""""""
    vpc_hcl = dg.generate_vpc_layer(catalog, project_name, region, owner, services)
    (base / "networking.tf").write_text(vpc_hcl, encoding="utf-8")
    typer.secho("  + networking.tf  [vpc module]", fg=typer.colors.GREEN)

    # "" main.tf (compute) " from catalog templates """""""""""""""""""""""""
    compute_templates = []
    compute_labels    = []

    modules_dir = base / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)

    # Rendered HCL per compute service (to place in modules/<svc>/main.tf)
    rendered_hcl: dict[str, str] = {}

    for c in compute_list:
        entry    = catalog_services.get(c, {})
        template = entry.get("template")
        if template:
            compute_templates.append(template)
            compute_labels.append(c)
        else:
            result = dg.generate_terraform_dynamically(
                c, entry, project_name, region, owner,
                services, env_names,
            )
            if result:
                hcl, svc_vars = result
                rendered_hcl[c] = hcl
                dynamic_vars.extend(svc_vars)

    # Append ingress add-ons that apply to the current compute targets
    for ingress_svc, i_entry in ingress_svcs.items():
        if ingress_svc not in services:
            continue
        allowed = set(i_entry.get("valid_compute_targets", []))
        if not any(c in allowed for c in compute_list):
            continue
        i_template = i_entry.get("template")
        if i_template:
            compute_templates.append(i_template)
            compute_labels.append(ingress_svc)

    # Render catalog templates -- collect HCL per label so we can split into modules
    if compute_templates:
        rendered_per_label = _render_per_label(jinja_env, compute_templates, ctx, labels=compute_labels)
        for label, hcl in rendered_per_label.items():
            rendered_hcl[label] = hcl

    # Write each compute service into modules/<name>/ and collect module call blocks
    root_main_blocks: list[str] = [
        "# Root main.tf -- calls per-service modules.\n"
        "# Resources live in modules/<name>/main.tf; values come from env/*/terraform.tfvars.\n"
    ]
    for svc, hcl in rendered_hcl.items():
        mod_name = _SVC_TO_MODULE.get(svc, svc.replace("-", "_"))
        svc_var_names = [v["name"] for v in dynamic_vars if v.get("service") == svc]
        _write_module_dir(modules_dir, mod_name, hcl, svc_var_names)
        call = _module_call_block(mod_name, svc_var_names)

        # Wire cross-module connections: pass outputs from upstream modules as inputs
        call = _inject_connection_wiring(call, mod_name, services, connections)
        root_main_blocks.append(call)

    if root_main_blocks:
        root_content = "\n".join(root_main_blocks)
        (base / "main.tf").write_text(root_content, encoding="utf-8")

    # "" iam.tf """"""""""""""""""""""""""""""""""""""""""""""""""""""""""""
    iam_templates = []
    seen_iam      = set()
    for c in compute_list:
        entry = catalog_services.get(c, {})
        for t in entry.get("iam_templates", []):
            if t not in seen_iam:
                seen_iam.add(t)
                iam_templates.append(t)

    iam_blocks = []
    for t in iam_templates:
        try:
            iam_blocks.append(jinja_env.get_template(t).render(**ctx))
            typer.secho(f"  + iam.tf  [{t}]", fg=typer.colors.GREEN)
        except Exception as e:
            typer.secho(f"  ! failed IAM template {t}: {e}", fg=typer.colors.YELLOW)

    connected_data_services = [
        s for s in data_stores
        if f"{compute_target}->{s}" in connections or any(
            f"{c}->{s}" in connections for c in compute_list
        )
    ]
    if connected_data_services:
        dynamic_iam = dg.generate_iam_policy_block(
            catalog=catalog,
            compute_service=compute_target,
            connected_services=connected_data_services,
            project_name=project_name,
            region=region,
        )
        if dynamic_iam:
            iam_blocks.append(dynamic_iam)
            typer.secho(
                f"  + iam.tf  [dynamic policy: {', '.join(connected_data_services)}]",
                fg=typer.colors.GREEN,
            )

    if iam_blocks:
        (base / "iam.tf").write_text("\n".join(iam_blocks) + "\n", encoding="utf-8")

    # "" Per-service .tf files (one file per service, not a single data.tf) ""
    # Each service gets its own <service-name>.tf at root, e.g. sqs.tf, ecr.tf.
    wrote_any_service = False
    for svc in other_services:
        if svc in ingress_keys:
            continue

        entry    = catalog_services.get(svc, {})
        template = entry.get("template")
        tf_file  = base / f"{svc}.tf"

        if template:
            extra_vars = entry.get("extra_vars", {})
            merged     = {**ctx, **extra_vars}
            try:
                hcl = jinja_env.get_template(template).render(**merged)
                tf_file.write_text(hcl + "\n", encoding="utf-8")
                typer.secho(f"  + {svc}.tf", fg=typer.colors.GREEN)
                wrote_any_service = True
            except Exception as e:
                typer.secho(f"  ! failed {svc} ({template}): {e}", fg=typer.colors.YELLOW)
        else:
            fallback_entry = entry if svc in catalog_services else {
                "terraform_resource": f"aws_{svc.replace('-', '_')}",
                "category": "unknown",
                "iam_actions": [],
            }
            if svc not in catalog_services:
                typer.secho(
                    f"  ~ '{svc}' not in catalog -- attempting AI generation...",
                    fg=typer.colors.BLUE,
                )
            result = dg.generate_terraform_dynamically(
                svc, fallback_entry, project_name, region, owner,
                services, env_names,
            )
            if result:
                hcl, svc_vars = result
                tf_file.write_text(hcl + "\n", encoding="utf-8")
                typer.secho(f"  + {svc}.tf  [ai-generated]", fg=typer.colors.CYAN)
                dynamic_vars.extend(svc_vars)
                wrote_any_service = True

    # Auto-add kms.tf when KMS is needed but not explicitly listed
    if wrote_any_service and "kms" not in services:
        kms_entry    = catalog_services.get("kms", {})
        kms_template = kms_entry.get("template")
        if kms_template:
            try:
                hcl = jinja_env.get_template(kms_template).render(**ctx)
                (base / "kms.tf").write_text(hcl + "\n", encoding="utf-8")
                typer.secho("  + kms.tf  [auto-added]", fg=typer.colors.GREEN)
            except Exception as e:
                typer.secho(f"  ! failed kms: {e}", fg=typer.colors.YELLOW)

    # "" observability.tf """"""""""""""""""""""""""""""""""""""""""""""""""
    _render(jinja_env, "iac/observability.tf.j2", base / "observability.tf", ctx)

    # "" output.tf """""""""""""""""""""""""""""""""""""""""""""""""""""""""
    _render(jinja_env, "iac/outputs.tf.j2", base / "output.tf", ctx)

    # "" variables.tf " declarations only, no hardcoded defaults """"""""""
    _write_variables_tf(base, dynamic_vars)

    # "" env/{env}/ " backend.tf, terraform.tfvars, terraform.tfvars.example
    _write_env_files(base, project_name, region, owner, environments or {}, dynamic_vars)

    # "" CI/CD pipeline """"""""""""""""""""""""""""""""""""""""""""""""""""
    _render(cicd_env, "cicd/pipeline.yml.j2", base / "cicd/pipeline.yml", ctx)

    # "" Secrets policy """"""""""""""""""""""""""""""""""""""""""""""""""""
    _write_secrets_policy(base, project_name, data_stores)

    # "" .gitignore """"""""""""""""""""""""""""""""""""""""""""""""""""""""
    _write_gitignore(base)

    typer.secho("\n> Scaffold complete.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Output: {base.absolute()}")


# -----------------------------------------------------------------------------
# modules/ scaffold
# -----------------------------------------------------------------------------

# Variables that each known module exposes to the root (for module call wiring).
# Format: var_name -> (type, description, rhs_in_root_call)
# rhs_in_root_call is the Terraform expression used in the root main.tf module call.
_MODULE_VARS: dict[str, list[tuple[str, str, str]]] = {
    "lambda": [
        ("name_prefix",          "string",      '"${var.project_name}-${var.environment}"'),
        # Deployment package
        ("lambda_s3_bucket",     "string",      "var.lambda_s3_bucket"),
        ("lambda_s3_key",        "string",      "var.lambda_s3_key"),
        # Sizing (from STATIC_SERVICE_VARS, passed through)
        ("lambda_memory_size",   "number",      "var.lambda_memory_size"),
        ("lambda_timeout",       "number",      "var.lambda_timeout"),
        # Cross-module deps — live in root iam.tf / data.tf
        ("lambda_exec_role_arn", "string",      "aws_iam_role.lambda_exec.arn"),
        ("kms_key_arn",          "string",      "try(aws_kms_key.main.arn, null)"),
        ("log_retention_days",   "number",      "30"),
        # Universal
        ("environment",          "string",      "var.environment"),
        ("region",               "string",      "var.region"),
        ("cost_centre",          "string",      "var.cost_centre"),
        ("tags",                 "map(string)", "local.common_tags"),
    ],
    "eks": [
        ("name_prefix",           "string",       '"${var.project_name}-${var.environment}"'),
        # Sizing
        ("eks_node_count",        "number",       "var.eks_node_count"),
        ("eks_instance_type",     "string",       "var.eks_instance_type"),
        ("eks_cluster_version",   "string",       "var.eks_cluster_version"),
        # Cross-module deps — live in root iam.tf
        ("eks_cluster_role_arn",  "string",       "aws_iam_role.eks_cluster.arn"),
        ("eks_node_role_arn",     "string",       "aws_iam_role.eks_node.arn"),
        # Cross-module deps — live in root networking.tf (module.vpc)
        ("subnet_private_ids",    "list(string)", "module.vpc.private_subnets"),
        ("subnet_public_ids",     "list(string)", "module.vpc.public_subnets"),
        ("security_group_id",     "string",       "aws_security_group.app.id"),
        # Universal
        ("environment",           "string",       "var.environment"),
        ("region",                "string",       "var.region"),
        ("cost_centre",           "string",       "var.cost_centre"),
        ("tags",                  "map(string)",  "local.common_tags"),
    ],
    "ecs": [
        ("name_prefix",   "string",      '"${var.project_name}-${var.environment}"'),
        ("environment",   "string",      "var.environment"),
        ("region",        "string",      "var.region"),
        ("cost_centre",   "string",      "var.cost_centre"),
        ("tags",          "map(string)", "local.common_tags"),
    ],
    "rds": [
        ("name_prefix",   "string",      '"${var.project_name}-${var.environment}"'),
        ("db_name",       "string",      "var.db_name"),
        ("db_username",   "string",      "var.db_username"),
        ("environment",   "string",      "var.environment"),
        ("region",        "string",      "var.region"),
        ("cost_centre",   "string",      "var.cost_centre"),
        ("tags",          "map(string)", "local.common_tags"),
    ],
}

# Modules that depend on root-level IAM policy attachments being applied first.
# These become depends_on blocks in the root main.tf module call.
_MODULE_DEPENDS_ON: dict[str, list[str]] = {
    "lambda": [
        "aws_iam_role_policy_attachment.lambda_basic",
    ],
    "eks": [
        "aws_iam_role_policy_attachment.eks_cluster_policy",
        "aws_iam_role_policy_attachment.eks_worker_node",
        "aws_iam_role_policy_attachment.eks_cni",
        "aws_iam_role_policy_attachment.eks_ecr_read",
    ],
}

# Map service name -> module folder name
_SVC_TO_MODULE: dict[str, str] = {
    "lambda":      "lambda",
    "eks":         "eks",
    "ecs-fargate": "ecs",
    "rds":         "rds",
    "aurora":      "rds",
}


def _write_module_dir(
    modules_dir: Path,
    mod_name: str,
    resource_hcl: str,
    svc_var_names: list[str],
) -> None:
    """Write modules/<mod_name>/{main.tf, variables.tf, outputs.tf}."""
    mod_dir = modules_dir / mod_name
    mod_dir.mkdir(parents=True, exist_ok=True)

    # main.tf -- actual resource definitions (moved from root main.tf)
    header = (
        f'# Module: {mod_name}\n'
        f'# Called from root main.tf via: module "{mod_name}" {{ source = "./modules/{mod_name}" }}\n'
        f'# Ref: https://registry.terraform.io/browse/modules?provider=aws\n\n'
    )
    (mod_dir / "main.tf").write_text(header + resource_hcl, encoding="utf-8")

    # variables.tf -- one variable block per input the module accepts
    # Include ALL vars defined in _MODULE_VARS (full module interface) plus any
    # extra dynamic vars. Use ordered-dict trick to preserve declaration order.
    known = {v[0]: v for v in _MODULE_VARS.get(mod_name, [])}
    seen: set[str] = set()
    all_vars: list[str] = []
    for vname in list(known.keys()) + svc_var_names:
        if vname not in seen:
            seen.add(vname)
            all_vars.append(vname)

    # Vars that can legally be null (passed as try(..., null) from root)
    nullable_vars = {"kms_key_arn", "security_group_id"}

    var_blocks: list[str] = []
    for vname in all_vars:
        if vname in known:
            _, vtype, _ = known[vname]
        else:
            vtype = "string"
        if vname in nullable_vars:
            var_blocks.append(
                f'variable "{vname}" {{\n'
                f'  type    = {vtype}\n'
                f'  default = null\n'
                f'}}\n'
            )
        else:
            var_blocks.append(
                f'variable "{vname}" {{\n'
                f'  type = {vtype}\n'
                f'}}\n'
            )

    (mod_dir / "variables.tf").write_text("\n".join(var_blocks), encoding="utf-8")

    # outputs.tf -- re-export key attributes so root module can reference them
    outputs = _default_module_outputs(mod_name)
    (mod_dir / "outputs.tf").write_text(outputs, encoding="utf-8")

    typer.secho(f"  + modules/{mod_name}/  [main.tf, variables.tf, outputs.tf]",
                fg=typer.colors.CYAN)


def _default_module_outputs(mod_name: str) -> str:
    templates = {
        "lambda": (
            'output "function_name" {\n'
            '  description = "Lambda function name"\n'
            '  value       = aws_lambda_function.app.function_name\n'
            '}\n\n'
            'output "function_arn" {\n'
            '  description = "Lambda function ARN"\n'
            '  value       = aws_lambda_function.app.arn\n'
            '}\n\n'
            'output "invoke_arn" {\n'
            '  description = "Lambda invoke ARN (used by API Gateway integration)"\n'
            '  value       = aws_lambda_function.app.invoke_arn\n'
            '}\n'
        ),
        "eks": (
            'output "cluster_name" {\n'
            '  description = "EKS cluster name (use with kubectl and aws eks update-kubeconfig)"\n'
            '  value       = aws_eks_cluster.main.name\n'
            '}\n\n'
            'output "cluster_endpoint" {\n'
            '  description = "EKS API server endpoint"\n'
            '  value       = aws_eks_cluster.main.endpoint\n'
            '}\n\n'
            'output "cluster_ca" {\n'
            '  description = "EKS cluster certificate authority (base64)"\n'
            '  value       = aws_eks_cluster.main.certificate_authority[0].data\n'
            '  sensitive   = true\n'
            '}\n\n'
            'output "node_group_name" {\n'
            '  description = "EKS managed node group name"\n'
            '  value       = aws_eks_node_group.main.node_group_name\n'
            '}\n'
        ),
        "ecs": (
            'output "cluster_id" {\n'
            '  value = aws_ecs_cluster.main.id\n'
            '}\n\n'
            'output "cluster_name" {\n'
            '  value = aws_ecs_cluster.main.name\n'
            '}\n'
        ),
        "rds": (
            'output "db_endpoint" {\n'
            '  value     = aws_db_instance.main.endpoint\n'
            '  sensitive = true\n'
            '}\n\n'
            'output "db_name" {\n'
            '  value = aws_db_instance.main.db_name\n'
            '}\n'
        ),
    }
    return templates.get(
        mod_name,
        f'# Add outputs that root main.tf needs from the {mod_name} module.\n',
    )


def _inject_connection_wiring(call_block: str, mod_name: str,
                              services: list[str], connections: list[str]) -> str:
    """
    Append cross-module output references to a module call block based on
    the connections declared in infra.yaml.

    For example, if connections contains "api-gateway->lambda", we add:
      lambda_invoke_arn    = module.lambda.invoke_arn
      lambda_function_name = module.lambda.function_name
    to the api_gateway module call.

    Also wires cognito outputs into api_gateway when cognito is in services.
    """
    # Map: (upstream_svc, downstream_mod) -> extra lines to inject before closing }
    WIRE_RULES: list[tuple[str, str, list[str]]] = [
        # api-gateway needs lambda outputs when api-gateway->lambda connection exists
        ("lambda", "api_gateway", [
            '  lambda_invoke_arn     = module.lambda.invoke_arn',
            '  lambda_function_name  = module.lambda.function_name',
        ]),
        # eks needs ecr output when eks->ecr connection exists
        ("ecr", "eks", [
            '  # ecr_repository_url available at: data.tf output ecr_repository_url',
        ]),
    ]

    # Cognito wiring: if cognito is in services, api_gateway needs its outputs
    STATIC_WIRES: list[tuple[str, list[str]]] = [
        ("api_gateway", "cognito", [
            '  cognito_client_id     = aws_cognito_user_pool_client.app.id',
            '  cognito_issuer_url    = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.main.id}"',
        ]),
    ]

    extra_lines: list[str] = []

    for upstream_svc, downstream_mod, lines in WIRE_RULES:
        if mod_name != downstream_mod:
            continue
        # Check if the connection exists in infra.yaml connections
        conn_exists = any(
            upstream_svc in c and mod_name.replace("_", "-") in c
            for c in connections
        ) or upstream_svc in services
        if conn_exists:
            extra_lines.extend(lines)

    for target_mod, dep_svc, lines in STATIC_WIRES:
        if mod_name == target_mod and dep_svc in services:
            extra_lines.extend(lines)

    if not extra_lines:
        return call_block

    # Insert extra_lines before the closing }
    block_lines = call_block.rstrip().split("\n")
    return "\n".join(block_lines[:-1] + extra_lines + [block_lines[-1]]) + "\n"


def _module_call_block(mod_name: str, svc_var_names: list[str]) -> str:
    """Generate the module {} call block for root main.tf."""
    known = {v[0]: v for v in _MODULE_VARS.get(mod_name, [])}
    # Include ALL known module vars (in declaration order) + any extra dynamic vars
    seen: set[str] = set()
    all_vars: list[str] = []
    for vname in list(known.keys()) + svc_var_names:
        if vname not in seen:
            seen.add(vname)
            all_vars.append(vname)

    lines = [
        f'module "{mod_name}" {{',
        f'  source = "./modules/{mod_name}"',
        '',
    ]
    for vname in all_vars:
        if vname in known:
            _, _, rhs = known[vname]
        else:
            rhs = f"var.{vname}"
        lines.append(f"  {vname:<26} = {rhs}")

    # Add depends_on for root-level IAM policy attachments this module needs
    deps = _MODULE_DEPENDS_ON.get(mod_name, [])
    if deps:
        lines.append('')
        lines.append('  depends_on = [')
        for d in deps:
            lines.append(f'    {d},')
        lines.append('  ]')

    lines.append("}")
    return "\n".join(lines) + "\n"


def _write_modules_scaffold(base: Path, services: list[str]) -> None:
    """Create modules/ directory; actual content is filled by _write_module_dir calls."""
    modules_dir = base / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    # Individual module dirs are written by _write_module_dir when HCL is available.


# -----------------------------------------------------------------------------
# provider.tf writer
# -----------------------------------------------------------------------------

def _write_provider_tf(base: Path, project_name: str, region: str,
                       owner: str, catalog: dict) -> None:
    tf_cfg           = dg.get_terraform_config(catalog)
    tf_version       = tf_cfg.get("required_version", ">= 1.5.0")
    provider_version = tf_cfg.get("aws_provider_version", "~> 6.0")

    content = (
        "# Terraform configuration -- generated by devops-scaffold-tool\n"
        "# Naming convention: {project}-{env}-{resource-type}\n"
        "# Ref: https://registry.terraform.io/browse/modules?provider=aws\n"
        "\n"
        f'terraform {{\n'
        f'  required_version = "{tf_version}"\n'
        f'\n'
        f'  required_providers {{\n'
        f'    aws = {{\n'
        f'      source  = "hashicorp/aws"\n'
        f'      version = "{provider_version}"\n'
        f'    }}\n'
        f'  }}\n'
        f'}}\n'
        f'\n'
        f'provider "aws" {{\n'
        f'  region = var.region\n'
        f'\n'
        f'  default_tags {{\n'
        f'    tags = {{\n'
        f'      Project     = var.project_name\n'
        f'      Owner       = var.owner\n'
        f'      Environment = var.environment\n'
        f'      ManagedBy   = "devops-scaffold-tool"\n'
        f'    }}\n'
        f'  }}\n'
        f'}}\n'
        f'\n'
        f'locals {{\n'
        f'  # Naming prefix: {{project}}-{{env}}-{{resource-type}}\n'
        f'  name_prefix = "${{var.project_name}}-${{var.environment}}"\n'
        f'\n'
        f'  common_tags = {{\n'
        f'    Project     = var.project_name\n'
        f'    Owner       = var.owner\n'
        f'    Environment = var.environment\n'
        f'    ManagedBy   = "devops-scaffold-tool"\n'
        f'  }}\n'
        f'}}\n'
    )
    (base / "provider.tf").write_text(content, encoding="utf-8")
    typer.secho("  + provider.tf", fg=typer.colors.GREEN)


# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
# variables.tf writer " declarations only, never hardcoded defaults
# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

def _write_variables_tf(base: Path, service_vars: list[dict]) -> None:
    lines = [
        "# ------------------------------------------------------------------------------",
        "# Variable declarations -- generated by devops-scaffold-tool",
        "# Values are set per-environment in env/{env}/terraform.tfvars",
        "# ------------------------------------------------------------------------------",
        "",
    ]

    # Deduplicate by name (first occurrence wins)
    seen: set[str] = set()
    all_vars = BASE_VARS + service_vars
    for var in all_vars:
        name = var["name"]
        if name in seen:
            continue
        seen.add(name)
        lines.append(f'variable "{name}" {{')
        lines.append(f'  description = "{var["description"]}"')
        lines.append(f'  type        = {var["type"]}')
        lines.append("}")
        lines.append("")

    (base / "variables.tf").write_text("\n".join(lines), encoding="utf-8")
    typer.secho("  + variables.tf  [declarations only -- no defaults]", fg=typer.colors.GREEN)


# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
# env/{env}/ writer
# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

def _write_env_files(
    base: Path,
    project_name: str,
    region: str,
    owner: str,
    environments: dict,
    service_vars: list[dict],
) -> None:
    env_names   = list(environments.keys()) if environments else ["dev", "staging", "prod"]
    all_svc_vars = service_vars  # already deduplicated in variables.tf logic

    for env_name in env_names:
        env_dir = base / "env" / env_name
        env_dir.mkdir(parents=True, exist_ok=True)

        # "" backend.tf """""""""""""""""""""""""""""""""""""""""""""""""""
        backend_key = f"{env_name}/terraform.tfstate"
        (env_dir / "backend.tf").write_text(
            f'terraform {{\n'
            f'  backend "s3" {{\n'
            f'    bucket         = "REPLACE_WITH_STATE_BUCKET"\n'
            f'    key            = "{backend_key}"\n'
            f'    region         = "{region}"\n'
            f'    dynamodb_table = "REPLACE_WITH_LOCK_TABLE"\n'
            f'    encrypt        = true\n'
            f'  }}\n'
            f'}}\n',
            encoding="utf-8",
        )

        # "" terraform.tfvars """""""""""""""""""""""""""""""""""""""""""""
        tfvars_lines = [
            f'# {env_name} environment " generated by devops-scaffold-tool',
            f'# Do NOT commit secrets. Use AWS Secrets Manager or SSM Parameter Store.',
            "",
            f'project_name = "{project_name}"',
            f'region       = "{region}"',
            f'environment  = "{env_name}"',
            f'owner        = "{owner}"',
            f'vpc_cidr     = "10.0.0.0/16"',
            f'cost_centre  = "REPLACE_WITH_COST_CENTRE"',
        ]

        if all_svc_vars:
            tfvars_lines.append("")
            tfvars_lines.append(f"# --- Service-specific variables ---")
            seen_names: set[str] = set()
            for var in all_svc_vars:
                name = var["name"]
                if name in seen_names:
                    continue
                seen_names.add(name)
                val = dg._env_value_for(var, env_name)
                tfvars_lines.append(_format_tfvar(name, val))

        (env_dir / "terraform.tfvars").write_text("\n".join(tfvars_lines) + "\n", encoding="utf-8")

        # "" terraform.tfvars.example """""""""""""""""""""""""""""""""""""
        example_lines = [
            f'# {env_name}.tfvars.example " copy to terraform.tfvars and fill in values',
            f'# This file IS committed to source control (no secrets here).',
            "",
            '# project_name = "REPLACE_WITH_PROJECT_NAME"',
            '# region       = "REPLACE_WITH_REGION"',
            f'# environment  = "{env_name}"',
            '# owner        = "REPLACE_WITH_OWNER"',
            '# vpc_cidr     = "10.0.0.0/16"',
            '# cost_centre  = "REPLACE_WITH_COST_CENTRE"',
        ]

        if all_svc_vars:
            example_lines.append("")
            example_lines.append("# --- Service-specific variables ---")
            seen_names = set()
            for var in all_svc_vars:
                name = var["name"]
                if name in seen_names:
                    continue
                seen_names.add(name)
                val = dg._env_value_for(var, env_name)
                example_lines.append(f"# {_format_tfvar(name, val)}  # {var['description']}")

        (env_dir / "terraform.tfvars.example").write_text("\n".join(example_lines) + "\n", encoding="utf-8")

        typer.secho(
            f"  + env/{env_name}/  [backend.tf, terraform.tfvars, terraform.tfvars.example]",
            fg=typer.colors.GREEN,
        )


def _format_tfvar(name: str, value: Any) -> str:
    """Format a single tfvars line with correct HCL value quoting."""
    if isinstance(value, bool):
        return f"{name} = {str(value).lower()}"
    if isinstance(value, (int, float)):
        return f"{name} = {value}"
    return f'{name} = "{value}"'


# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
# Secrets policy
# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

def _write_secrets_policy(base: Path, project_name: str, data_stores: list) -> None:
    content = (
        "# Secrets structure\n"
        "# Use AWS Secrets Manager or SSM Parameter Store.\n"
        "# NEVER hardcode values -- this file defines paths/structure only.\n\nsecrets:\n"
    )
    if "postgres" in data_stores or "mysql" in data_stores or \
       "aurora-postgres" in data_stores or "aurora-mysql" in data_stores:
        content += (
            f'  - path: "/{project_name}/{{environment}}/db/password"\n'
            f'    service: "AWS Secrets Manager"\n'
            f'    description: "RDS master password -- auto-rotated"\n'
        )
    if "redis" in data_stores or "memcached" in data_stores:
        content += (
            f'  - path: "/{project_name}/{{environment}}/cache/auth-token"\n'
            f'    service: "AWS Secrets Manager"\n'
            f'    description: "ElastiCache auth token"\n'
        )
    if "opensearch" in data_stores:
        content += (
            f'  - path: "/{project_name}/{{environment}}/opensearch/master-password"\n'
            f'    service: "AWS Secrets Manager"\n'
            f'    description: "OpenSearch master user password"\n'
        )
    if "msk" in data_stores:
        content += (
            f'  - path: "/{project_name}/{{environment}}/msk/sasl-password"\n'
            f'    service: "AWS Secrets Manager"\n'
            f'    description: "MSK SASL/SCRAM credentials"\n'
        )
    content += (
        f'  - path: "/{project_name}/{{environment}}/app/secret-key"\n'
        f'    service: "AWS SSM Parameter Store"\n'
        f'    description: "Application secret key"\n'
    )
    (base / "secrets/secrets-policy.yml").write_text(content, encoding="utf-8")


# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
# .gitignore
# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

def _write_gitignore(base: Path) -> None:
    (base / ".gitignore").write_text(
        "# Terraform state\n"
        "*.tfstate\n"
        "*.tfstate.backup\n"
        ".terraform/\n"
        ".terraform.lock.hcl\n\n"
        "# tfvars contain real values -- never commit\n"
        "*.tfvars\n"
        "!*.tfvars.example\n\n"
        "# Cache\n"
        ".tf-cache/\n",
        encoding="utf-8",
    ) 


