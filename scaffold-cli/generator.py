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
        {"name": "log_retention_days", "type": "number",
         "description": "CloudWatch log retention in days",
         "dev": 7, "staging": 30, "prod": 90},
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
        {"name": "alb_certificate_arn", "type": "string",
         "description": "ACM certificate ARN for the HTTPS listener (TLS 1.2+)",
         "dev": "REPLACE_WITH_ACM_CERT_ARN", "staging": "REPLACE_WITH_ACM_CERT_ARN",
         "prod": "REPLACE_WITH_ACM_CERT_ARN"},
        {"name": "alb_access_logs_bucket", "type": "string",
         "description": "S3 bucket name for ALB access logs",
         "dev": "REPLACE_WITH_LOG_BUCKET", "staging": "REPLACE_WITH_LOG_BUCKET",
         "prod": "REPLACE_WITH_LOG_BUCKET"},
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
# Catalog entry lookup — supports role-suffixed service names (ec2-java → ec2)
# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

def _catalog_entry(catalog_services: dict, svc: str) -> dict:
    """Return the catalog entry for *svc*, falling back to base name for suffixed names."""
    if svc in catalog_services:
        return catalog_services[svc]
    base = dg._base_svc(svc, {"services": catalog_services})
    return catalog_services.get(base, {}) if base else {}


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
    templates_dir: str = "",
) -> None:
    base     = Path(output_dir)
    # Resolve templates_dir relative to this file so scaffold works regardless of CWD
    if templates_dir:
        tmpl_dir = Path(templates_dir)
    else:
        tmpl_dir = Path(__file__).resolve().parent.parent / "parent-repo" / "templates"

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
    compute_list   = dg.resolve_compute_services(services, catalog)
    compute_target = compute_list[0]
    compute_set    = set(compute_list)
    other_services = [s for s in services if s not in compute_set]

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
            tf_file.unlink()

    # Clean up stale module directories — remove any modules/ subdir whose name
    # does NOT correspond to a service in the current run.  This prevents old
    # s3_java/ s3_php/ directories from lingering after S3 is consolidated.
    _modules_dir = base / "modules"
    if _modules_dir.exists():
        import shutil
        # Build set of module dir names the current run will write
        _expected_mods: set[str] = set()
        for _svc in services:
            _svc_base = dg._base_svc(_svc, catalog) or _svc
            if _svc_base in {"s3"}:                     # consolidated → base name only
                _expected_mods.add(_svc_base.replace("-", "_"))
            else:
                _expected_mods.add(_svc.replace("-", "_"))
        for _mod_dir in _modules_dir.iterdir():
            if _mod_dir.is_dir() and _mod_dir.name not in _expected_mods:
                shutil.rmtree(_mod_dir)

    # modules/ -- one sub-folder per local reusable module
    _write_modules_scaffold(base, services)

    # Observability config
    observability  = config.get("observability", {})
    _ret_raw      = observability.get("log_retention_days", 30)
    log_retention = (
        _ret_raw if isinstance(_ret_raw, dict)
        else {"dev": _ret_raw, "staging": _ret_raw, "prod": _ret_raw}
    )
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
        # Support both exact names ('eks') and role-suffixed names ('ec2-java')
        lookup_key = svc if svc in STATIC_SERVICE_VARS else (
            dg._base_svc(svc, catalog) or svc
        )
        if lookup_key in STATIC_SERVICE_VARS:
            dynamic_vars.extend(STATIC_SERVICE_VARS[lookup_key])

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
        entry    = _catalog_entry(catalog_services, c)
        template = entry.get("template")
        _hcl_key = c if c in _MODULE_MAIN_HCL else (dg._base_svc(c, catalog) or c)
        if _hcl_key in _MODULE_MAIN_HCL:
            # Hardened static template takes priority — guaranteed Checkov compliance
            rendered_hcl[c] = _MODULE_MAIN_HCL[_hcl_key]
        elif template:
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
        typer.secho(f"  + modules/{mod_name}/  [main.tf, variables.tf, outputs.tf]",
                    fg=typer.colors.GREEN)

        # Resolve base type — plain 'ec2' has no suffix so _base_svc returns None
        svc_base = dg._base_svc(svc, catalog) or svc
        if svc_base == "ec2":
            # EC2 (plain or role-suffixed) — each instance gets its own prefixed sizing vars
            call = _ec2_module_call_block(mod_name)
            _inject_ec2_instance_vars(svc, mod_name, environments, dynamic_vars)
        else:
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

    # "" Per-service modules (each service → modules/<svc>/{main,variables,outputs}.tf) ""
    # Root main.tf gets a module call block for each service.
    modules_dir = base / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)

    service_module_calls: list[str] = []   # collected → appended to root main.tf
    wrote_any_service = False

    # Services where multiple role-suffixed instances share ONE consolidated module.
    # e.g. s3-java, s3-php, s3-doc, s3-ui → single modules/s3/ with for_each over buckets map.
    _CONSOLIDATE_BASES: set[str] = {"s3"}
    _consolidated_written: set[str] = set()   # track which base modules already written

    for svc in other_services:
        if svc in ingress_keys:
            continue

        svc_base = dg._base_svc(svc, catalog) or svc

        # ── Consolidated module (all s3-* → one modules/s3/) ────────────────────
        if svc_base in _CONSOLIDATE_BASES:
            if svc_base not in _consolidated_written:
                _consolidated_written.add(svc_base)
                _write_service_module(modules_dir, svc_base, "")
                service_module_calls.append(_service_module_call(svc_base))
                # Build s3_buckets map from all role-suffixed s3 services
                _inject_s3_buckets_var(svc_base, other_services, project_name, environments, dynamic_vars)
                wrote_any_service = True
            continue   # skip per-instance module creation

        entry    = _catalog_entry(catalog_services, svc)
        template = entry.get("template")

        if template:
            extra_vars = entry.get("extra_vars", {})
            merged     = {**ctx, **extra_vars}
            try:
                hcl = jinja_env.get_template(template).render(**merged)
                _write_service_module(modules_dir, svc, hcl)
                service_module_calls.append(_service_module_call(svc))
                wrote_any_service = True
            except Exception as e:
                typer.secho(f"  ! failed {svc} ({template}): {e}", fg=typer.colors.YELLOW)
        else:
            has_base = bool(entry)  # truthy when base-name lookup succeeded
            fallback_entry = entry if has_base else {
                "terraform_resource": f"aws_{svc.replace('-', '_')}",
                "category": "unknown",
                "iam_actions": [],
            }
            if not has_base:
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
                _write_service_module(modules_dir, svc, hcl)
                service_module_calls.append(_service_module_call(svc))
                typer.secho(f"  + modules/{svc.replace('-','_')}/  [ai-generated]",
                            fg=typer.colors.CYAN)
                dynamic_vars.extend(svc_vars)
                wrote_any_service = True

        # Inject scalar sizing vars for this service (kms_deletion_window_days etc.)
        _SCALAR_SVC_VARS: dict[str, list[dict]] = {
            "kms": [
                {"name": "kms_deletion_window_days", "type": "number",
                 "description": "KMS key deletion window in days (7-30)",
                 "dev": 7, "staging": 14, "prod": 30},
            ],
        }
        if svc in _SCALAR_SVC_VARS:
            for v in _SCALAR_SVC_VARS[svc]:
                if not any(d["name"] == v["name"] for d in dynamic_vars):
                    dynamic_vars.append(v)

    # Auto-add kms module when KMS is needed but not explicitly listed
    if wrote_any_service and "kms" not in services:
        kms_entry    = catalog_services.get("kms", {})
        kms_template = kms_entry.get("template")
        if kms_template:
            try:
                hcl = jinja_env.get_template(kms_template).render(**ctx)
                _write_service_module(modules_dir, "kms", hcl)
                service_module_calls.append(_service_module_call("kms"))
                typer.secho("  + modules/kms/  [auto-added]", fg=typer.colors.GREEN)
                # The module call references var.kms_deletion_window_days — declare it.
                if not any(d["name"] == "kms_deletion_window_days" for d in dynamic_vars):
                    dynamic_vars.append(
                        {"name": "kms_deletion_window_days", "type": "number",
                         "description": "KMS key deletion window in days (7-30)",
                         "dev": 7, "staging": 14, "prod": 30}
                    )
            except Exception as e:
                typer.secho(f"  ! failed kms: {e}", fg=typer.colors.YELLOW)

    # Append service module calls to root main.tf
    if service_module_calls:
        main_tf_path = base / "main.tf"
        existing = main_tf_path.read_text(encoding="utf-8") if main_tf_path.exists() else ""
        separator = "\n# ── Service Modules ─────────────────────────────────────────────────────────\n\n"
        main_tf_path.write_text(
            existing.rstrip() + "\n" + separator + "\n".join(service_module_calls),
            encoding="utf-8",
        )

    # "" observability.tf """"""""""""""""""""""""""""""""""""""""""""""""""
    _render(jinja_env, "iac/observability.tf.j2", base / "observability.tf", ctx)

    # "" output.tf """""""""""""""""""""""""""""""""""""""""""""""""""""""""
    _render(jinja_env, "iac/outputs.tf.j2", base / "output.tf", ctx)

    # "" variables.tf " declarations + map(object) types """"""""""""""""""""
    _write_variables_tf(base, dynamic_vars, services)

    # "" locals.tf " cross-module ARN resolution """""""""""""""""""""""""""""
    _write_locals_tf(base, project_name, services, connections)

    # "" env/{env}/ " backend.tf, terraform.tfvars, terraform.tfvars.example
    _write_env_files(base, project_name, region, owner, environments or {}, dynamic_vars, services)

    # "" CI/CD pipeline """"""""""""""""""""""""""""""""""""""""""""""""""""
    _render(cicd_env, "cicd/pipeline.yml.j2", base / "cicd/pipeline.yml", ctx)

    # "" Secrets policy """"""""""""""""""""""""""""""""""""""""""""""""""""
    _write_secrets_policy(base, project_name, data_stores)

    # "" .gitignore """"""""""""""""""""""""""""""""""""""""""""""""""""""""
    _write_gitignore(base)

    # ── cost-estimate.md ──────────────────────────────────────────────────────
    _write_cost_estimate(base, project_name, services, environments or {})

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
        ("lambda_exec_role_arn",    "string",      "aws_iam_role.lambda_exec.arn"),
        ("kms_key_arn",             "string",      "try(module.kms.key_arn, null)"),
        ("log_retention_days",      "number",      "var.log_retention_days"),
        # Optional service module outputs passed in when those services are present
        ("secrets_manager_arn",     "string",      'try(module.secrets_manager.secret_arn, null)'),
        ("sns_topic_arn",           "string",      'try(module.sns.topic_arn, null)'),
        ("sqs_queue_url",           "string",      'try(module.sqs.queue_url, null)'),
        # Universal
        ("environment",             "string",      "var.environment"),
        ("region",                  "string",      "var.region"),
        ("cost_centre",             "string",      "var.cost_centre"),
        ("tags",                    "map(string)", "local.common_tags"),
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
    # ec2 sizing vars are prefixed per-instance at call-site (var.ec2_java_instance_type)
    # so the RHS here uses a placeholder "__PREFIX__" that gets substituted at runtime.
    "ec2": [
        ("name_prefix",           "string",       ""),           # computed from mod_name
        ("instance_type",         "string",       ""),           # prefixed var
        ("ami_id",                "string",       ""),           # prefixed var
        ("subnet_id",             "string",       "module.vpc.private_subnets[0]"),
        ("security_group_id",     "string",       "aws_security_group.app.id"),
        ("kms_key_arn",           "string",       "try(module.kms.key_arn, null)"),
        ("instance_profile_name", "string",       'try(aws_iam_instance_profile.ec2.name, null)'),
        ("tags",                  "map(string)",  "local.common_tags"),
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

    # variables.tf -- use pre-written vars if this module has a static _MODULE_VARS_TF entry
    # Support role-suffixed module names (ec2_java → look up ec2)
    _vars_key = mod_name if mod_name in _MODULE_VARS_TF else (
        dg._base_svc(mod_name.replace("_", "-"), {"services": {k: {} for k in _MODULE_VARS_TF}})
        or mod_name
    )
    if _vars_key in _MODULE_VARS_TF:
        (mod_dir / "variables.tf").write_text(_MODULE_VARS_TF[_vars_key], encoding="utf-8")
        outputs_hcl = _MODULE_OUTPUTS_TF.get(_vars_key, f"# Add outputs for the {mod_name} module.\n")
        (mod_dir / "outputs.tf").write_text(outputs_hcl, encoding="utf-8")
        return

    # Otherwise build variables.tf from _MODULE_VARS + dynamic var names
    known = {v[0]: v for v in _MODULE_VARS.get(mod_name, [])}
    seen: set[str] = set()
    all_vars: list[str] = []
    for vname in list(known.keys()) + svc_var_names:
        if vname not in seen:
            seen.add(vname)
            all_vars.append(vname)

    # Vars that can legally be null (passed as try(..., null) from root)
    nullable_vars = {
        "kms_key_arn", "security_group_id",
        "secrets_manager_arn", "sns_topic_arn", "sqs_queue_url",
    }

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


def _inject_s3_buckets_var(
    base: str,
    all_services: list[str],
    project_name: str,
    environments: dict,
    dynamic_vars: list[dict],
) -> None:
    """Build the s3_buckets map(object) variable from all role-suffixed s3 services.

    Each svc like 's3-java' → bucket key 'java' in the map.
    Per-env bucket names include the environment suffix.
    Reads lifecycle_days and versioning from environments config if present.
    """
    roles = [
        svc.split("-", 1)[1]
        for svc in all_services
        if dg._base_svc(svc, {"services": {base: {}}}) == base
    ]
    if not roles:
        return

    # Build one var dict per environment — stored as an obj_block (not a scalar)
    # We store it as a special type "s3_map" that the tfvars writer renders as HCL block
    env_defaults: dict[str, dict] = {}
    for env_name, env_cfg in environments.items():
        role_cfgs: dict[str, dict] = {}
        for role in roles:
            svc_cfg = env_cfg.get(f"{base}-{role}", {})
            role_cfgs[role] = {
                "name":           f"{project_name}-{env_name}-{role}",
                "versioning":     svc_cfg.get("versioning", True),
                "lifecycle_days": svc_cfg.get("lifecycle_days", 90),
                "force_destroy":  svc_cfg.get("force_destroy", False),
            }
        env_defaults[env_name] = role_cfgs

    var: dict = {
        "name":        "s3_buckets",
        "type":        "s3_map",        # sentinel: rendered as HCL map block by tfvars writer
        "description": f"Map of S3 buckets ({', '.join(roles)}). Key = role, value = bucket config.",
        "_env_values": env_defaults,    # private key used by tfvars writer
    }
    if not any(d["name"] == "s3_buckets" for d in dynamic_vars):
        dynamic_vars.append(var)


def _ec2_module_call_block(mod_name: str) -> str:
    """Generate root main.tf module call for a role-suffixed EC2 fleet.

    Each fleet (ec2_java, ec2_php, ec2_doc) gets its own prefixed sizing variables
    so all three can coexist in the same tfvars file without collision:
      ec2_java_instance_type = "t3.medium"
      ec2_php_instance_type  = "t3.medium"
      ec2_doc_instance_type  = "t3.small"
    """
    # Extract role label (java / php / doc) from mod_name like "ec2_java"
    parts = mod_name.split("_", 1)
    role = parts[1] if len(parts) == 2 else mod_name
    return (
        f'module "{mod_name}" {{\n'
        f'  source = "./modules/{mod_name}"\n'
        f'\n'
        f'  name_prefix           = "${{var.project_name}}-${{var.environment}}-{role}"\n'
        f'  instance_type         = var.{mod_name}_instance_type\n'
        f'  ami_id                = var.{mod_name}_ami_id\n'
        f'  subnet_id             = module.vpc.private_subnets[0]\n'
        f'  security_group_id     = aws_security_group.app.id\n'
        f'  kms_key_arn           = try(module.kms.key_arn, null)\n'
        f'  instance_profile_name = try(aws_iam_instance_profile.ec2.name, null)\n'
        f'  tags                  = local.common_tags\n'
        f'}}\n'
    )


def _inject_ec2_instance_vars(
    svc: str,
    mod_name: str,
    environments: dict,
    dynamic_vars: list[dict],
) -> None:
    """Append per-instance sizing variables for a role-suffixed EC2 service.

    Reads instance_type from environments.<env>.<svc>.instance_type and
    appends prefixed variable dicts (ec2_java_instance_type etc.) to dynamic_vars
    so they are written to both root variables.tf and each env's terraform.tfvars.
    """
    # Determine per-env instance_type values from environments config
    env_types: dict[str, str] = {}
    for env_name, env_cfg in environments.items():
        svc_cfg = env_cfg.get(svc, {})
        env_types[env_name] = svc_cfg.get("instance_type", "t3.medium")

    # instance_type var
    instance_type_var: dict = {
        "name": f"{mod_name}_instance_type",
        "type": "string",
        "description": f"EC2 instance type for the {svc} fleet",
    }
    instance_type_var.update(env_types)  # adds dev/prod/staging keys

    # ami_id var — always a placeholder (AMI IDs are region/account specific)
    ami_id_var: dict = {
        "name": f"{mod_name}_ami_id",
        "type": "string",
        "description": f"AMI ID for the {svc} fleet (e.g. latest Amazon Linux 2023)",
    }
    for env_name in environments:
        ami_id_var[env_name] = "REPLACE_WITH_AMI_ID"

    for v in (instance_type_var, ami_id_var):
        if not any(d["name"] == v["name"] for d in dynamic_vars):
            dynamic_vars.append(v)


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


# =============================================================================
# map(object) module pattern — matches terraform_templates reference
# Each service module accepts a typed map variable so adding resources only
# requires editing tfvars, never the module code itself.
# =============================================================================

# Static HCL for each service module's main.tf (for_each pattern)
# Lambda and EKS are here (not AI-generated) to guarantee all Checkov security checks pass.
_MODULE_MAIN_HCL: dict[str, str] = {
    "lambda": '''\
resource "aws_lambda_function" "app" {
  #checkov:skip=CKV_AWS_272:code_signing_config_arn requires a pre-created signing profile — set var.code_signing_config_arn when ready
  function_name = "${local.name_prefix}-func"
  role          = var.lambda_exec_role_arn

  s3_bucket = var.lambda_s3_bucket
  s3_key    = var.lambda_s3_key
  handler   = "handler.main"
  runtime   = "python3.13"

  architectures = ["arm64"]

  timeout     = var.lambda_timeout
  memory_size = var.lambda_memory_size

  reserved_concurrent_executions = var.reserved_concurrency

  kms_key_arn = var.kms_key_arn

  ephemeral_storage {
    size = 512
  }

  dead_letter_config {
    target_arn = var.dlq_arn
  }

  vpc_config {
    subnet_ids         = var.subnet_private_ids
    security_group_ids = compact([var.security_group_id])
  }

  logging_config {
    log_format            = "JSON"
    application_log_level = "INFO"
    system_log_level      = "WARN"
    log_group             = aws_cloudwatch_log_group.lambda.name
  }

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      ENVIRONMENT = var.environment
    }
  }

  tags = local.common_tags

  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name_prefix}-func"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn

  tags = local.common_tags
}
''',

    "eks": '''\
resource "aws_eks_cluster" "main" {
  name     = "${local.name_prefix}-eks"
  role_arn = var.eks_cluster_role_arn
  version  = var.eks_cluster_version

  vpc_config {
    subnet_ids              = concat(var.subnet_private_ids, var.subnet_public_ids)
    endpoint_private_access = true
    endpoint_public_access  = var.environment != "prod"
    public_access_cidrs     = var.eks_public_access_cidrs
    security_group_ids      = compact([var.security_group_id])
  }

  encryption_config {
    provider {
      key_arn = var.kms_key_arn
    }
    resources = ["secrets"]
  }

  access_config {
    authentication_mode                         = "API_AND_CONFIG_MAP"
    bootstrap_cluster_creator_admin_permissions = true
  }

  kubernetes_network_config {
    ip_family         = "ipv4"
    service_ipv4_cidr = "172.20.0.0/16"
  }

  enabled_cluster_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  upgrade_policy {
    support_type = "EXTENDED"
  }

  tags = local.common_tags
}

resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${local.name_prefix}-ng"
  node_role_arn   = var.eks_node_role_arn
  subnet_ids      = var.subnet_private_ids

  ami_type       = "AL2023_ARM_64_STANDARD"
  instance_types = [var.eks_instance_type]
  capacity_type  = "ON_DEMAND"

  scaling_config {
    desired_size = var.eks_node_count
    max_size     = var.eks_node_count * 3
    min_size     = 1
  }

  update_config {
    max_unavailable = 1
  }

  force_update_version = false

  labels = {
    Environment = var.environment
    NodeGroup   = "main"
  }

  tags = local.common_tags
}
''',

    "sqs": '''\
resource "aws_sqs_queue" "queues" {
  for_each = var.sqs_queues

  name                       = each.value.name
  visibility_timeout_seconds = each.value.visibility_timeout_seconds
  message_retention_seconds  = each.value.message_retention_seconds
  max_message_size           = lookup(each.value, "max_message_size", 1048576)
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[each.value.dlq_key].arn
    maxReceiveCount     = 3
  })

  tags = merge(var.tags, { Name = each.value.name })
}

resource "aws_sqs_queue_redrive_allow_policy" "queues" {
  for_each = var.sqs_queues

  queue_url = aws_sqs_queue.dlq[each.value.dlq_key].id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.queues[each.key].arn]
  })
}

resource "aws_sqs_queue" "dlq" {
  for_each = var.dlq_queues

  name                      = each.value.name
  message_retention_seconds = each.value.message_retention_seconds
  sqs_managed_sse_enabled   = true

  tags = merge(var.tags, { Name = each.value.name })
}
''',

    "sns": '''\
resource "aws_sns_topic" "this" {
  name              = var.sns.name
  kms_master_key_id = var.kms_key_arn

  tags = merge(var.tags, { Name = var.sns.name })
}

resource "aws_sns_topic_subscription" "this" {
  for_each = var.sns.subscriptions

  topic_arn = aws_sns_topic.this.arn
  protocol  = each.value.protocol
  endpoint  = each.value.endpoint
}

resource "aws_sns_topic_policy" "this" {
  arn = aws_sns_topic.this.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowLambdaPublish"
        Effect = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action   = "SNS:Publish"
        Resource = aws_sns_topic.this.arn
      },
      {
        Sid    = "AllowEventBridgePublish"
        Effect = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action   = "SNS:Publish"
        Resource = aws_sns_topic.this.arn
      }
    ]
  })
}
''',

    "kms": '''\
data "aws_caller_identity" "current" {}

resource "aws_kms_key" "main" {
  description             = var.description
  deletion_window_in_days = var.deletion_window_in_days
  enable_key_rotation     = true
  key_usage               = "ENCRYPT_DECRYPT"
  multi_region            = false

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccess"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      }
    ]
  })

  tags = merge(var.tags, { Name = var.description })
}

resource "aws_kms_alias" "main" {
  name          = "alias/${var.key_alias}"
  target_key_id = aws_kms_key.main.key_id
}
''',

    "ecr": '''\
resource "aws_ecr_repository" "repos" {
  for_each = var.ecr_repositories

  name                 = each.value.name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
  }

  tags = merge(var.tags, { Name = each.value.name })
}

resource "aws_ecr_lifecycle_policy" "repos" {
  for_each   = var.ecr_repositories
  repository = aws_ecr_repository.repos[each.key].name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 1 day"
        selection    = { tagStatus = "untagged", countType = "sinceImagePushed", countUnit = "days", countNumber = 1 }
        action       = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep last 30 tagged images"
        selection    = { tagStatus = "tagged", tagPrefixList = ["v"], countType = "imageCountMoreThan", countNumber = 30 }
        action       = { type = "expire" }
      }
    ]
  })
}
''',

    "eventbridge": '''\
locals {
  # Resolve lambda_key -> lambda ARN and scheduler role ARN at runtime
  eventbridge_schedules_resolved = {
    for k, sched in var.eventbridge_schedules :
    k => merge(sched, {
      lambda_arn = var.lambda_arns[sched.lambda_key]
      role_arn   = var.scheduler_role_arn
    })
  }
}

resource "aws_scheduler_schedule" "this" {
  for_each = local.eventbridge_schedules_resolved

  name        = each.value.name
  description = lookup(each.value, "description", null)

  schedule_expression          = each.value.schedule_expression
  schedule_expression_timezone = each.value.timezone

  kms_key_arn = var.kms_key_arn

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = each.value.lambda_arn
    role_arn = each.value.role_arn

    retry_policy {
      maximum_retry_attempts = lookup(each.value, "retry_attempts", 0)
    }
  }
}

resource "aws_cloudwatch_event_rule" "ecr_push" {
  count = var.ecr_push_rule != null ? 1 : 0

  name        = var.ecr_push_rule.name
  description = "Trigger deployment pipeline on ECR image PUSH"
  state       = "ENABLED"

  event_pattern = jsonencode({
    source        = ["aws.ecr"]
    "detail-type" = ["ECR Image Action"]
    detail = {
      "action-type"     = ["PUSH"]
      result            = ["SUCCESS"]
      "repository-name" = [{ prefix = var.ecr_push_rule.repo_prefix }]
    }
  })

  tags = merge(var.tags, { Name = var.ecr_push_rule.name })
}

resource "aws_cloudwatch_event_target" "lambda" {
  count     = var.ecr_push_rule != null && var.lambda_arns != null ? 1 : 0
  rule      = aws_cloudwatch_event_rule.ecr_push[0].name
  target_id = "lambda-target"
  arn       = values(var.lambda_arns)[0]
}

resource "aws_lambda_permission" "eventbridge" {
  count         = var.ecr_push_rule != null && var.lambda_arns != null ? 1 : 0
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = values(var.lambda_function_names)[0]
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ecr_push[0].arn
}
''',

    "secrets-manager": '''\
resource "aws_secretsmanager_secret" "this" {
  for_each = var.secrets

  name                    = each.value.name
  description             = lookup(each.value, "description", null)
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = lookup(each.value, "recovery_window_in_days", 7)

  tags = merge(var.tags, { Name = each.value.name })
}

# Auto-rotation for secrets that declare a rotation_days value.
# Requires a rotation Lambda to be deployed (see secrets/rotation/ for template).
resource "aws_secretsmanager_secret_rotation" "this" {
  #checkov:skip=CKV_AWS_304:rotation_days is capped at 90 via min() — Checkov cannot evaluate dynamic expressions
  for_each = {
    for k, s in var.secrets : k => s
    if lookup(s, "rotation_days", 0) > 0
  }

  secret_id           = aws_secretsmanager_secret.this[each.key].id
  rotation_lambda_arn = var.rotation_lambda_arn

  rotation_rules {
    automatically_after_days = min(each.value.rotation_days, 90)
  }
}
''',

    "cloudwatch": '''\
resource "aws_cloudwatch_log_group" "lambdas" {
  for_each = var.lambda_log_groups

  name              = each.value.name
  retention_in_days = lookup(each.value, "retention_in_days", var.log_retention_days)
  kms_key_id        = var.kms_key_arn

  tags = merge(var.tags, { Name = each.value.name })
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = {
    for k, v in var.cloudwatch_alarms.lambdas : k => v if lookup(v, "enabled", true)
  }

  alarm_name          = "${each.value.name}_errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 5
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = each.value.threshold

  dimensions = { FunctionName = each.value.name }

  alarm_actions = [var.sns_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "sqs_backlog" {
  for_each = {
    for k, v in var.cloudwatch_alarms.sqs : k => v if lookup(v, "enabled", true)
  }

  alarm_name          = "${each.value.name}_backlog"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Average"
  threshold           = each.value.threshold

  dimensions = { QueueName = each.value.name }

  alarm_actions = [var.sns_topic_arn]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = var.dashboard.name
  dashboard_body = jsonencode(var.dashboard.body)
}
''',

    "s3": '''\
resource "aws_s3_bucket" "this" {
  #checkov:skip=CKV_AWS_144:Cross-region replication is optional — enable per bucket after scaffold
  #checkov:skip=CKV2_AWS_62:Event notifications are optional and should be configured post-scaffold
  for_each      = var.buckets
  bucket        = each.value.name
  force_destroy = each.value.force_destroy

  tags = merge(var.tags, { Role = each.key })
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = var.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = var.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = var.kms_key_arn != null ? "aws:kms" : "AES256"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = var.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Access logging — each bucket logs to the designated logging bucket
resource "aws_s3_bucket_logging" "this" {
  for_each = var.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  target_bucket = var.logging_bucket_id != null ? var.logging_bucket_id : aws_s3_bucket.this[each.key].id
  target_prefix = "s3-access-logs/${each.key}/"
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = var.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "expire-old-objects"
    status = each.value.lifecycle_days > 0 ? "Enabled" : "Disabled"

    expiration {
      days = each.value.lifecycle_days > 0 ? each.value.lifecycle_days : 365
    }
  }
}
''',

    "ec2": '''\
#checkov:skip=CKV_TF_1:Registry source uses semver pin; git commit hash format not supported for Terraform Registry modules

module "ec2_instance" {
  #checkov:skip=CKV_TF_1:Registry source uses semver pin; git commit hash format not supported for Terraform Registry modules
  source  = "terraform-aws-modules/ec2-instance/aws"
  version = "~> 5.0"

  name          = var.name_prefix
  instance_type = var.instance_type
  ami           = var.ami_id
  key_name      = var.key_name

  subnet_id              = var.subnet_id
  vpc_security_group_ids = [var.security_group_id]

  # IMDSv2 required — disables IMDSv1 to prevent SSRF-based metadata access
  metadata_options = {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  # EBS root volume — encrypted with CMK
  root_block_device = [
    {
      encrypted   = true
      kms_key_id  = var.kms_key_arn
      volume_type = "gp3"
      volume_size = var.root_volume_size_gb
    }
  ]

  # IAM instance profile for S3/RDS/Secrets access (created in iam.tf)
  iam_instance_profile = var.instance_profile_name

  # Monitoring
  monitoring = true

  tags = var.tags
}
''',
}

# variables.tf content for each service module (map(object) typed)
_MODULE_VARS_TF: dict[str, str] = {
    "lambda": '''\
variable "name_prefix" { type = string }
variable "lambda_s3_bucket" { type = string }
variable "lambda_s3_key" { type = string }
variable "lambda_memory_size" { type = number }
variable "lambda_timeout" { type = number }
variable "lambda_exec_role_arn" { type = string }
variable "log_retention_days" { type = number }
variable "environment" { type = string }
variable "region" { type = string }
variable "cost_centre" { type = string }
variable "tags" { type = map(string) }

variable "kms_key_arn" { type = string; default = null }
variable "secrets_manager_arn" { type = string; default = null }
variable "sns_topic_arn" { type = string; default = null }
variable "sqs_queue_url" { type = string; default = null }
variable "dlq_arn" {
  description = "ARN of the Dead Letter Queue for failed Lambda invocations."
  type        = string
  default     = null
}
variable "subnet_private_ids" {
  description = "Private subnet IDs to run Lambda inside the VPC."
  type        = list(string)
  default     = []
}
variable "security_group_id" { type = string; default = null }
variable "reserved_concurrency" {
  description = "Reserved concurrent executions (-1 = unrestricted)."
  type        = number
  default     = -1
}
''',

    "eks": '''\
variable "name_prefix" { type = string }
variable "eks_node_count" { type = number }
variable "eks_instance_type" { type = string }
variable "eks_cluster_version" { type = string }
variable "eks_cluster_role_arn" { type = string }
variable "eks_node_role_arn" { type = string }
variable "subnet_private_ids" { type = list(string) }
variable "subnet_public_ids" { type = list(string) }
variable "environment" { type = string }
variable "region" { type = string }
variable "cost_centre" { type = string }
variable "tags" { type = map(string) }

variable "kms_key_arn" { type = string; default = null }
variable "security_group_id" { type = string; default = null }
variable "eks_public_access_cidrs" {
  description = "CIDR blocks allowed to reach the EKS public API endpoint."
  type        = list(string)
  default     = ["10.0.0.0/8"]
}
''',

    "sqs": '''\
variable "sqs_queues" {
  description = "Map of SQS queues. Adding a new queue = add one block here, no code change."
  type = map(object({
    name                       = string
    message_retention_seconds  = number
    max_message_size           = optional(number, 1048576)
    visibility_timeout_seconds = number
    dlq_key                    = string
  }))
  default = {}
}

variable "dlq_queues" {
  description = "Map of Dead Letter Queues paired with sqs_queues entries."
  type = map(object({
    name                      = string
    message_retention_seconds = number
  }))
  default = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}
''',

    "sns": '''\
variable "sns" {
  description = "SNS topic configuration with subscriptions map."
  type = object({
    name = string
    subscriptions = map(object({
      protocol = string
      endpoint = string
    }))
  })
}

variable "kms_key_arn" {
  description = "KMS key ARN for SNS topic encryption. null = AWS-managed key."
  type        = string
  default     = null
}

variable "tags" {
  type    = map(string)
  default = {}
}
''',

    "kms": '''\
variable "description" {
  description = "KMS key description (also used as Name tag)."
  type        = string
}

variable "key_alias" {
  description = "KMS alias name (without alias/ prefix)."
  type        = string
}

variable "deletion_window_in_days" {
  description = "Days before permanent key deletion (7-30). Use 7 for non-prod."
  type        = number
  default     = 7
}

variable "tags" {
  type    = map(string)
  default = {}
}
''',

    "ecr": '''\
variable "ecr_repositories" {
  description = "Map of ECR repositories. Add a new repo by adding one block here."
  type = map(object({
    name                 = string
    image_tag_mutability = optional(string, "IMMUTABLE")
    scan_on_push         = optional(bool, true)
  }))
  default = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}
''',

    "eventbridge": '''\
variable "eventbridge_schedules" {
  description = "Map of EventBridge scheduler schedules. lambda_key must match a key in lambda_arns."
  type = map(object({
    name                = string
    description         = optional(string)
    schedule_expression = string
    timezone            = string
    lambda_key          = string
    retry_attempts      = optional(number, 0)
  }))
  default = {}
}

variable "lambda_arns" {
  description = "Map of lambda_key -> function ARN. Populated from module.lambda outputs."
  type        = map(string)
  default     = {}
}

variable "lambda_function_names" {
  description = "Map of lambda_key -> function name."
  type        = map(string)
  default     = {}
}

variable "scheduler_role_arn" {
  description = "IAM role ARN that EventBridge Scheduler uses to invoke Lambda."
  type        = string
  default     = null
}

variable "ecr_push_rule" {
  description = "If set, creates an EventBridge rule triggered by ECR image pushes."
  type = object({
    name        = string
    repo_prefix = string
  })
  default = null
}

variable "kms_key_arn" {
  description = "KMS CMK ARN used to encrypt EventBridge Scheduler schedules."
  type        = string
  default     = null
}

variable "tags" {
  type    = map(string)
  default = {}
}
''',

    "secrets-manager": '''\
variable "secrets" {
  description = "Map of secrets to create. Set rotation_days > 0 to enable auto-rotation."
  type = map(object({
    name                    = string
    description             = optional(string)
    recovery_window_in_days = optional(number, 7)
    rotation_days           = optional(number, 0)
  }))
  default = {}
}

variable "kms_key_arn" {
  description = "KMS key ARN used to encrypt secrets. null = AWS-managed key."
  type        = string
  default     = null
}

variable "rotation_lambda_arn" {
  description = "ARN of the Lambda function that rotates secrets. Required if any secret has rotation_days > 0."
  type        = string
  default     = null
}

variable "tags" {
  type    = map(string)
  default = {}
}
''',

    "cloudwatch": '''\
variable "log_retention_days" {
  description = "Default CloudWatch log retention in days."
  type        = number
  default     = 30
}

variable "sns_topic_arn" {
  description = "SNS topic ARN that receives alarm notifications."
  type        = string
  default     = null
}

variable "lambda_log_groups" {
  description = "Map of Lambda log groups to create."
  type = map(object({
    name              = string
    retention_in_days = optional(number)
  }))
  default = {}
}

variable "cloudwatch_alarms" {
  description = "Alarm definitions for Lambdas, SQS queues, and DLQs."
  type = object({
    lambdas = optional(map(object({ name = string, threshold = number, enabled = optional(bool, true) })), {})
    sqs     = optional(map(object({ name = string, threshold = number, enabled = optional(bool, true) })), {})
    dlq     = optional(map(object({ name = string, threshold = number, enabled = optional(bool, true) })), {})
  })
  default = { lambdas = {}, sqs = {}, dlq = {} }
}

variable "dashboard" {
  description = "CloudWatch dashboard config."
  type = object({
    name = string
    body = any
  })
  default = null
}

variable "kms_key_arn" {
  description = "KMS key ARN used to encrypt CloudWatch log groups."
  type        = string
  default     = null
}

variable "tags" {
  type    = map(string)
  default = {}
}
''',

    "s3": '''\
variable "buckets" {
  type = map(object({
    name           = string
    versioning     = bool
    lifecycle_days = number
    force_destroy  = bool
  }))
  description = "Map of S3 buckets. Key = role label (java, php, doc, ui). Each entry creates one bucket."
}

variable "kms_key_arn" {
  type        = string
  description = "KMS key ARN for SSE-KMS encryption. null = AES256 (server-managed)."
  default     = null
}

variable "logging_bucket_id" {
  type        = string
  description = "S3 bucket ID to receive access logs. null = each bucket logs to itself."
  default     = null
}

variable "tags" {
  type    = map(string)
  default = {}
}
''',

    "ec2": '''\
variable "name_prefix" {
  type        = string
  description = "Name prefix for the EC2 instance."
}

variable "instance_type" {
  type        = string
  description = "EC2 instance type (e.g. t3.medium, m5.large)."
}

variable "ami_id" {
  type        = string
  description = "AMI ID for the EC2 instance."
}

variable "key_name" {
  type        = string
  description = "EC2 key pair name for SSH access."
  default     = null
}

variable "subnet_id" {
  type        = string
  description = "Subnet ID to launch the instance into (private subnet recommended)."
}

variable "security_group_id" {
  type        = string
  description = "Security group ID to attach to the instance."
}

variable "kms_key_arn" {
  type        = string
  description = "KMS key ARN for EBS root volume encryption."
  default     = null
}

variable "root_volume_size_gb" {
  type        = number
  description = "Size of the root EBS volume in GB."
  default     = 20
}

variable "instance_profile_name" {
  type        = string
  description = "IAM instance profile name to attach to the EC2 instance."
  default     = null
}

variable "tags" {
  type    = map(string)
  default = {}
}
''',
}

# outputs.tf content for each service module
_MODULE_OUTPUTS_TF: dict[str, str] = {
    "lambda": '''\
output "function_name" {
  description = "Lambda function name."
  value       = aws_lambda_function.app.function_name
}
output "function_arn" {
  description = "Lambda function ARN."
  value       = aws_lambda_function.app.arn
}
output "invoke_arn" {
  description = "Lambda invoke ARN (for API Gateway integrations)."
  value       = aws_lambda_function.app.invoke_arn
}
''',

    "eks": '''\
output "cluster_name" {
  description = "EKS cluster name."
  value       = aws_eks_cluster.main.name
}
output "cluster_endpoint" {
  description = "EKS cluster API endpoint."
  value       = aws_eks_cluster.main.endpoint
}
output "cluster_arn" {
  description = "EKS cluster ARN."
  value       = aws_eks_cluster.main.arn
}
''',

    "sqs": '''\
output "queues" {
  description = "Map of SQS queues — keys match sqs_queues input. Each value has url and arn."
  value = {
    for k, q in aws_sqs_queue.queues : k => {
      url = q.id
      arn = q.arn
    }
  }
}

output "dlqs" {
  description = "Map of DLQs — keys match dlq_queues input."
  value = {
    for k, q in aws_sqs_queue.dlq : k => {
      url = q.id
      arn = q.arn
    }
  }
}
''',

    "sns": '''\
output "topic_arn" {
  description = "SNS topic ARN — pass to Lambda/EventBridge to publish notifications."
  value       = aws_sns_topic.this.arn
}

output "topic_name" {
  description = "SNS topic name."
  value       = aws_sns_topic.this.name
}
''',

    "kms": '''\
output "key_arn" {
  description = "KMS key ARN — pass to SQS, SNS, Secrets Manager as kms_key_arn."
  value       = aws_kms_key.main.arn
}

output "key_id" {
  description = "KMS key ID."
  value       = aws_kms_key.main.key_id
}

output "alias_arn" {
  description = "KMS alias ARN."
  value       = aws_kms_alias.main.arn
}
''',

    "ecr": '''\
output "repositories" {
  description = "Map of ECR repositories — keys match ecr_repositories input."
  value = {
    for k, r in aws_ecr_repository.repos : k => {
      url = r.repository_url
      arn = r.arn
    }
  }
}
''',

    "eventbridge": '''\
output "schedule_arns" {
  description = "Map of EventBridge schedule ARNs."
  value = {
    for k, s in aws_scheduler_schedule.this : k => s.arn
  }
}

output "ecr_push_rule_arn" {
  description = "ECR push EventBridge rule ARN (null if not configured)."
  value       = length(aws_cloudwatch_event_rule.ecr_push) > 0 ? aws_cloudwatch_event_rule.ecr_push[0].arn : null
}
''',

    "secrets-manager": '''\
output "secrets" {
  description = "Map of secrets — keys match secrets input. Each value has arn and name."
  value = {
    for k, s in aws_secretsmanager_secret.this : k => {
      arn  = s.arn
      name = s.name
    }
  }
}
''',

    "cloudwatch": '''\
output "log_group_names" {
  description = "Map of Lambda log group names."
  value = {
    for k, lg in aws_cloudwatch_log_group.lambdas : k => lg.name
  }
}

output "dashboard_name" {
  description = "CloudWatch dashboard name."
  value       = var.dashboard != null ? aws_cloudwatch_dashboard.main.dashboard_name : null
}
''',

    "s3": '''\
output "bucket_arns" {
  description = "Map of S3 bucket ARNs. Keys match the buckets input variable."
  value       = { for k, b in aws_s3_bucket.this : k => b.arn }
}

output "bucket_names" {
  description = "Map of S3 bucket names. Keys match the buckets input variable."
  value       = { for k, b in aws_s3_bucket.this : k => b.id }
}
''',

    "ec2": '''\
output "instance_id" {
  description = "EC2 instance ID."
  value       = module.ec2_instance.id
}

output "private_ip" {
  description = "Private IP address of the EC2 instance."
  value       = module.ec2_instance.private_ip
}

output "instance_arn" {
  description = "ARN of the EC2 instance."
  value       = module.ec2_instance.arn
}
''',
}


def _write_service_module(modules_dir: Path, svc: str, _hcl_unused: str = "") -> None:
    """
    Write modules/<svc>/{main.tf, variables.tf, outputs.tf} using the
    map(object) + for_each pattern matching terraform_templates reference.
    The Jinja2-rendered HCL is replaced by static, reusable module templates.
    """
    mod_name = svc.replace("-", "_")
    mod_dir  = modules_dir / mod_name
    mod_dir.mkdir(parents=True, exist_ok=True)

    header = (
        f'# Module: {svc}\n'
        f'# source = "./modules/{mod_name}"\n'
        f'#\n'
        f'# MODULE REGISTRY — to share this module across projects, push to a Git repo\n'
        f'# and change the source in root main.tf to a versioned Git URL:\n'
        f'#\n'
        f'#   source = "git::https://github.com/YOUR_ORG/infra-modules.git//{mod_name}?ref=v1.0"\n'
        f'#\n'
        f'# Tag releases: git tag v1.0 && git push origin v1.0\n'
        f'# Upgrade: bump the ?ref= value — no module code changes needed.\n'
        f'# Add new resources by editing tfvars only — no module code changes needed.\n\n'
    )

    _TAGS_VAR_FALLBACK = (
        'variable "tags" {\n'
        '  type    = map(string)\n'
        '  default = {}\n'
        '}\n'
    )
    main_hcl   = _MODULE_MAIN_HCL.get(svc, f'# TODO: add {svc} resources here\n')
    vars_hcl   = _MODULE_VARS_TF.get(svc, _TAGS_VAR_FALLBACK)
    output_hcl = _MODULE_OUTPUTS_TF.get(svc, f'# Add outputs for the {svc} module.\n')

    (mod_dir / "main.tf").write_text(header + main_hcl,  encoding="utf-8")
    (mod_dir / "variables.tf").write_text(vars_hcl,       encoding="utf-8")
    (mod_dir / "outputs.tf").write_text(output_hcl,       encoding="utf-8")

    typer.secho(f"  + modules/{mod_name}/  [main.tf, variables.tf, outputs.tf]",
                fg=typer.colors.CYAN)


def _service_module_call(svc: str) -> str:
    """
    Generate root main.tf module call block.
    Passes the whole map variable — not individual scalars.
    """
    mod_name = svc.replace("-", "_")
    _CALLS: dict[str, str] = {
        "s3": (
            f'module "s3" {{\n'
            f'  source = "./modules/s3"\n\n'
            f'  buckets     = var.s3_buckets\n'
            f'  kms_key_arn = try(module.kms.key_arn, null)\n'
            f'  tags        = local.common_tags\n'
            f'}}\n'
        ),
        "sqs": (
            f'module "sqs" {{\n'
            f'  source = "./modules/sqs"\n\n'
            f'  sqs_queues = var.sqs_queues\n'
            f'  dlq_queues = var.dlq_queues\n'
            f'  tags       = local.common_tags\n'
            f'}}\n'
        ),
        "sns": (
            f'module "sns" {{\n'
            f'  source = "./modules/sns"\n\n'
            f'  sns         = var.sns\n'
            f'  kms_key_arn = try(module.kms.key_arn, null)\n'
            f'  tags        = local.common_tags\n'
            f'}}\n'
        ),
        "kms": (
            f'module "kms" {{\n'
            f'  source = "./modules/kms"\n\n'
            f'  description             = "${{var.project_name}}-${{var.environment}} CMK"\n'
            f'  key_alias               = "${{var.project_name}}-${{var.environment}}"\n'
            f'  deletion_window_in_days = var.kms_deletion_window_days\n'
            f'  tags                    = local.common_tags\n'
            f'}}\n'
        ),
        "ecr": (
            f'module "ecr" {{\n'
            f'  source = "./modules/ecr"\n\n'
            f'  ecr_repositories = var.ecr_repositories\n'
            f'  tags             = local.common_tags\n'
            f'}}\n'
        ),
        "eventbridge": (
            f'module "eventbridge" {{\n'
            f'  source = "./modules/eventbridge"\n\n'
            f'  eventbridge_schedules = local.eventbridge_schedules\n'
            f'  lambda_arns           = local.lambda_arns\n'
            f'  lambda_function_names = local.lambda_function_names\n'
            f'  scheduler_role_arn    = try(aws_iam_role.lambda_exec.arn, null)\n'
            f'  ecr_push_rule         = var.ecr_push_rule\n'
            f'  kms_key_arn           = try(module.kms.key_arn, null)\n'
            f'  tags                  = local.common_tags\n'
            f'}}\n'
        ),
        "secrets-manager": (
            f'module "secrets_manager" {{\n'
            f'  source = "./modules/secrets_manager"\n\n'
            f'  secrets     = var.secrets\n'
            f'  kms_key_arn = try(module.kms.key_arn, null)\n'
            f'  tags        = local.common_tags\n'
            f'}}\n'
        ),
        "cloudwatch": (
            f'module "cloudwatch" {{\n'
            f'  source = "./modules/cloudwatch"\n\n'
            f'  log_retention_days = var.log_retention_days\n'
            f'  sns_topic_arn      = try(module.sns.topic_arn, null)\n'
            f'  lambda_log_groups  = local.lambda_log_groups\n'
            f'  cloudwatch_alarms  = var.cloudwatch_alarms\n'
            f'  dashboard          = var.dashboard\n'
            f'  kms_key_arn        = try(module.kms.key_arn, null)\n'
            f'  tags               = local.common_tags\n'
            f'}}\n'
        ),
    }
    return _CALLS.get(svc, (
        f'module "{mod_name}" {{\n'
        f'  source = "./modules/{mod_name}"\n\n'
        f'  tags = local.common_tags\n'
        f'}}\n'
    ))


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
        "#\n"
        "# SECURITY DEFAULTS ENFORCED\n"
        "#   - All resources tagged with Project/Owner/Environment/CostCenter\n"
        "#   - Encryption at rest enforced in each service module (KMS CMK)\n"
        "#   - S3: block_public_access enabled on every bucket\n"
        "#   - SQS/SNS: sqs_managed_sse_enabled = true; kms_master_key_id set\n"
        "#   - IAM: roles scoped to specific resource ARNs, not wildcard *\n"
        "#   - SGs: no 0.0.0.0/0 inbound except ALB 80/443\n"
        "#\n"
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
        f'      CostCenter  = var.cost_centre\n'
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
        f'    CostCenter  = var.cost_centre\n'
        f'    ManagedBy   = "devops-scaffold-tool"\n'
        f'  }}\n'
        f'}}\n'
    )
    (base / "provider.tf").write_text(content, encoding="utf-8")
    typer.secho("  + provider.tf", fg=typer.colors.GREEN)


# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
# variables.tf writer " declarations only, never hardcoded defaults
# """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

def _write_variables_tf(base: Path, service_vars: list[dict], services: list[str]) -> None:
    lines = [
        "# ------------------------------------------------------------------------------",
        "# Variable declarations -- generated by devops-scaffold-tool",
        "# Values are set per-environment in env/{env}/terraform.tfvars",
        "# map(object) vars: add resources by editing tfvars only -- no code changes.",
        "# ------------------------------------------------------------------------------",
        "",
    ]

    # Base scalar vars first
    seen: set[str] = set()
    for var in BASE_VARS:
        name = var["name"]
        seen.add(name)
        lines.append(f'variable "{name}" {{')
        lines.append(f'  description = "{var["description"]}"')
        lines.append(f'  type        = {var["type"]}')
        lines.append("}")
        lines.append("")

    # Scalar service vars (eks, lambda sizing)
    for var in service_vars:
        name = var["name"]
        if name in seen:
            continue
        seen.add(name)
        lines.append(f'variable "{name}" {{')
        lines.append(f'  description = "{var["description"]}"')
        lines.append(f'  type        = {var["type"]}')
        lines.append("}")
        lines.append("")

    # map(object) service variables — matching terraform_templates pattern
    _MAP_VARS: dict[str, str] = {
        "sqs": '''\
variable "sqs_queues" {
  description = "Map of SQS queues. Add a queue by adding one block here — no code change needed."
  type = map(object({
    name                       = string
    message_retention_seconds  = number
    max_message_size           = optional(number, 1048576)
    visibility_timeout_seconds = number
    dlq_key                    = string
  }))
  default = {}
}

variable "dlq_queues" {
  description = "Map of Dead Letter Queues paired with sqs_queues entries."
  type = map(object({
    name                      = string
    message_retention_seconds = number
  }))
  default = {}
}
''',
        "sns": '''\
variable "sns" {
  description = "SNS topic configuration with optional subscriptions."
  type = object({
    name = string
    subscriptions = optional(map(object({
      protocol = string
      endpoint = string
    })), {})
  })
  default = null
}
''',
        # NOTE: kms_deletion_window_days is declared via the scalar-var path
        # (_SCALAR_SVC_VARS["kms"]) so it carries per-env values. Do NOT declare
        # it here too — that produced a duplicate variable declaration.
        "ecr": '''\
variable "ecr_repositories" {
  description = "Map of ECR repositories. Add a repo by adding one block here."
  type = map(object({
    name                 = string
    image_tag_mutability = optional(string, "IMMUTABLE")
    scan_on_push         = optional(bool, true)
  }))
  default = {}
}
''',
        "eventbridge": '''\
variable "eventbridge_schedules" {
  description = "Map of EventBridge Scheduler schedules. lambda_key must match lambda_configs key."
  type = map(object({
    name                = string
    description         = optional(string)
    schedule_expression = string
    timezone            = string
    lambda_key          = string
    retry_attempts      = optional(number, 0)
  }))
  default = {}
}

variable "ecr_push_rule" {
  description = "If set, creates an EventBridge rule triggered on ECR image push."
  type = object({
    name        = string
    repo_prefix = string
  })
  default = null
}
''',
        "secrets-manager": '''\
variable "secrets" {
  description = "Map of Secrets Manager secrets. Add a secret by adding one block here."
  type = map(object({
    name                    = string
    description             = optional(string)
    recovery_window_in_days = optional(number, 7)
  }))
  default = {}
}
''',
        "cloudwatch": '''\
variable "cloudwatch_alarms" {
  description = "CloudWatch alarm definitions per resource type."
  type = object({
    lambdas = optional(map(object({ name = string, threshold = number, enabled = optional(bool, true) })), {})
    sqs     = optional(map(object({ name = string, threshold = number, enabled = optional(bool, true) })), {})
    dlq     = optional(map(object({ name = string, threshold = number, enabled = optional(bool, true) })), {})
  })
  default = { lambdas = {}, sqs = {}, dlq = {} }
}

variable "dashboard" {
  description = "CloudWatch dashboard config."
  type = object({
    name = string
    body = any
  })
  default = null
}
''',
    }

    for svc in services:
        # Direct match
        if svc in _MAP_VARS and svc not in seen:
            seen.add(svc)
            lines.append(_MAP_VARS[svc])

    # s3_buckets map var — emitted once when any s3-* service is present
    _has_s3 = any(
        svc == "s3" or (dg._base_svc(svc, {"services": {"s3": {}}}) == "s3")
        for svc in services
    )
    if _has_s3 and "s3_buckets" not in seen:
        seen.add("s3_buckets")
        lines.append('''\
variable "s3_buckets" {
  description = "Map of S3 buckets. Key = role (java, php, doc, ui). Add a bucket by adding one block here."
  type = map(object({
    name           = string
    versioning     = bool
    lifecycle_days = number
    force_destroy  = bool
  }))
  default = {}
}
''')

    # lambda_configs always added when lambda is present
    if "lambda" in services and "lambda_configs" not in seen:
        seen.add("lambda_configs")
        lines.append('''\
variable "lambda_configs" {
  description = "Map of Lambda functions. Add a function by adding one block here."
  type = map(object({
    function_name         = string
    handler               = string
    runtime               = optional(string, "python3.12")
    timeout               = optional(number, 30)
    memory_size           = optional(number, 512)
    s3_bucket             = optional(string)
    s3_key                = optional(string)
    environment_variables = optional(map(string), {})
    layers                = optional(list(string), [])
    sqs_trigger = optional(object({
      queue      = string
      batch_size = number
    }))
  }))
  default = {}
}
''')

    (base / "variables.tf").write_text("\n".join(lines), encoding="utf-8")
    typer.secho("  + variables.tf  [declarations only -- no defaults]", fg=typer.colors.GREEN)


def _write_locals_tf(
    base: Path,
    project_name: str,
    services: list[str],
    connections: list[str],
) -> None:
    """
    Generate locals.tf — transforms raw tfvars into resolved values for module calls.
    Mirrors the locals.tf pattern in terraform_templates: resolves lambda_key -> ARN,
    builds log group names, etc.
    """
    blocks: list[str] = [
        "# locals.tf — generated by devops-scaffold-tool",
        "# Transforms raw tfvars into resolved values passed to modules.",
        "# Cross-module ARN references are resolved here, not in module code.",
        "",
    ]

    if "lambda" in services:
        blocks.append('''\
locals {
  # Apply defaults to each lambda config entry
  lambda_configs = {
    for key, cfg in var.lambda_configs :
    key => merge({
      runtime     = "python3.12"
      timeout     = 30
      memory_size = 512
      layers      = []
      environment_variables = {}
    }, cfg)
  }

  # Convenience maps used by eventbridge and cloudwatch modules
  lambda_arns = {
    for key, fn in module.lambda : key => fn.function_arn
  }

  lambda_function_names = {
    for key, fn in module.lambda : key => fn.function_name
  }

  # CloudWatch log group per Lambda function
  lambda_log_groups = {
    for key, cfg in local.lambda_configs :
    key => {
      name              = "/aws/lambda/${cfg.function_name}"
      retention_in_days = var.log_retention_days
    }
  }
}
''')

    if "eventbridge" in services:
        blocks.append('''\
locals {
  # Resolve lambda_key -> ARN for EventBridge schedules
  eventbridge_schedules = {
    for key, sched in var.eventbridge_schedules :
    key => sched
    # lambda_arn is resolved inside the eventbridge module using lambda_arns map
  }
}
''')

    if "sqs" in services and "cloudwatch" in services:
        blocks.append('''\
locals {
  # CloudWatch alarms for SQS queues — auto-built from sqs_queues map
  sqs_alarm_targets = {
    for k, q in var.sqs_queues : k => {
      name      = q.name
      threshold = 100
      enabled   = true
    }
  }

  dlq_alarm_targets = {
    for k, q in var.dlq_queues : k => {
      name      = q.name
      threshold = 1
      enabled   = true
    }
  }
}
''')

    (base / "locals.tf").write_text("\n".join(blocks), encoding="utf-8")
    typer.secho("  + locals.tf  [cross-module ARN resolution]", fg=typer.colors.GREEN)


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
    services: list[str],
) -> None:
    env_names = list(environments.keys()) if environments else ["dev", "staging", "prod"]

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

        env_cfg = environments.get(env_name, {})

        # Resolve scalar per-env values (eks sizing, lambda sizing)
        scalar_lines: list[str] = []
        seen_names: set[str] = set()
        for var in service_vars:
            name = var["name"]
            if name in seen_names:
                continue
            seen_names.add(name)
            # s3_map vars are rendered as HCL obj_blocks, not scalar lines
            if var.get("type") == "s3_map":
                continue
            val = _env_override(name, env_cfg) or dg._env_value_for(var, env_name)
            scalar_lines.append(_format_tfvar(name, val))

        # Resolve per-env aliases (uat->staging, live->prod) for object defaults
        _env_alias = env_name
        if env_name in ("uat", "qa", "test"):
            _env_alias = "staging"
        elif env_name in ("live", "production"):
            _env_alias = "prod"
        _is_prod = _env_alias == "prod"

        # Build map(object) blocks
        obj_blocks: list[str] = []

        # ── S3 buckets map ────────────────────────────────────────────────────
        _s3_map_vars = [v for v in service_vars if v.get("type") == "s3_map" and v["name"] == "s3_buckets"]
        if _s3_map_vars:
            role_cfgs = _s3_map_vars[0].get("_env_values", {}).get(env_name, {})
            if role_cfgs:
                bucket_lines = ["s3_buckets = {"]
                for role, cfg in role_cfgs.items():
                    vers = str(cfg["versioning"]).lower()
                    ld   = cfg["lifecycle_days"]
                    fd   = str(cfg["force_destroy"]).lower()
                    bucket_lines += [
                        f'  {role} = {{',
                        f'    name           = "{cfg["name"]}"',
                        f'    versioning     = {vers}',
                        f'    lifecycle_days = {ld}',
                        f'    force_destroy  = {fd}',
                        f'  }}',
                    ]
                bucket_lines.append("}")
                obj_blocks.append("# S3 bucket configurations\n# Add a new bucket by adding a block — no module code changes needed.\n" + "\n".join(bucket_lines) + "\n")

        if "lambda" in services:
            lambda_cfg = env_cfg.get("lambda", {})
            mem   = lambda_cfg.get("memory_mb", 512 if not _is_prod else 1024)
            timo  = lambda_cfg.get("timeout_s", 30)
            obj_blocks.append(f'''\
# Lambda function configurations
# Add a new function by adding one block inside lambda_configs.
lambda_configs = {{
  deploy = {{
    function_name         = "{project_name}-{env_name}-func"
    handler               = "lambda_function.lambda_handler"
    runtime               = "python3.12"
    timeout               = {timo}
    memory_size           = {mem}
    s3_bucket             = "REPLACE_WITH_DEPLOY_BUCKET"
    s3_key                = "lambda/app.zip"
    environment_variables = {{
      ENV          = "{env_name.upper()}"
      PROJECT_NAME = "{project_name}"
    }}
  }}
}}
''')

        if "sqs" in services:
            vis = 60
            ret = 345600 if _is_prod else 86400
            obj_blocks.append(f'''\
# SQS queue configurations
# Add a new queue by adding one block inside sqs_queues (and matching DLQ in dlq_queues).
sqs_queues = {{
  main_queue = {{
    name                       = "{project_name}-{env_name}-queue"
    message_retention_seconds  = {ret}
    max_message_size           = 1048576
    visibility_timeout_seconds = {vis}
    dlq_key                    = "main_dlq"
  }}
}}

dlq_queues = {{
  main_dlq = {{
    name                      = "{project_name}-{env_name}-dlq"
    message_retention_seconds = {1209600 if _is_prod else 345600}
  }}
}}
''')

        if "ecr" in services:
            mut = "IMMUTABLE" if _is_prod else "MUTABLE"
            obj_blocks.append(f'''\
# ECR repository configurations
ecr_repositories = {{
  app = {{
    name                 = "{project_name}-{env_name}-app"
    image_tag_mutability = "{mut}"
    scan_on_push         = {str(_is_prod).lower()}
  }}
}}
''')

        if "secrets-manager" in services:
            rw = 30 if _is_prod else 0
            obj_blocks.append(f'''\
# Secrets Manager configurations
# Add a new secret by adding one block inside secrets.
secrets = {{
  app_secrets = {{
    name                    = "{project_name}/{env_name}/app"
    description             = "Application secrets for {project_name} ({env_name})"
    recovery_window_in_days = {rw}
  }}
}}
''')

        if "eventbridge" in services:
            obj_blocks.append(f'''\
# EventBridge Scheduler configurations
# lambda_key must match a key in lambda_configs above.
eventbridge_schedules = {{
  daily_trigger = {{
    name                = "{project_name}-scheduler-{env_name}"
    description         = "Daily trigger for {project_name} deploy worker"
    schedule_expression = "cron(0 0 * * ? *)"
    timezone            = "UTC"
    lambda_key          = "deploy"
    retry_attempts      = 0
  }}
}}

ecr_push_rule = {{
  name        = "{project_name}-{env_name}-ecr-push"
  repo_prefix = "{project_name}-{env_name}"
}}
''')

        if "sns" in services:
            obj_blocks.append(f'''\
# SNS topic configuration
sns = {{
  name = "{project_name}-{env_name}-notifications"
  subscriptions = {{
    # email = {{
    #   protocol = "email"
    #   endpoint = "team@example.com"
    # }}
  }}
}}
''')

        if "cloudwatch" in services:
            fn_name = f"{project_name}-{env_name}-func"
            q_name  = f"{project_name}-{env_name}-queue"
            d_name  = f"{project_name}-{env_name}-dlq"
            obj_blocks.append(f'''\
# CloudWatch alarms
cloudwatch_alarms = {{
  lambdas = {{
    deploy = {{ name = "{fn_name}", threshold = 1, enabled = true }}
  }}
  sqs = {{
    main_queue = {{ name = "{q_name}", threshold = 100, enabled = true }}
  }}
  dlq = {{
    main_dlq = {{ name = "{d_name}", threshold = 1, enabled = true }}
  }}
}}

dashboard = {{
  name = "{project_name}-{env_name}-dashboard"
  body = {{}}
}}
''')

        # Assemble tfvars
        tfvars_lines = [
            f'# {env_name} environment — generated by devops-scaffold-tool',
            f'# Do NOT commit secrets here. Use the secrets map → AWS Secrets Manager.',
            "",
            f'project_name = "{project_name}"',
            f'region       = "{region}"',
            f'environment  = "{env_name}"',
            f'owner        = "{owner}"',
            f'vpc_cidr     = "10.0.0.0/16"',
            f'cost_centre  = "REPLACE_WITH_COST_CENTRE"',
        ]

        if scalar_lines:
            tfvars_lines += ["", "# ── Scalar sizing variables ──────────────────────────────────"] + scalar_lines

        if obj_blocks:
            tfvars_lines += ["", "# ── Service configurations (map objects) ─────────────────────"]
            for blk in obj_blocks:
                tfvars_lines.append(blk)

        (env_dir / "terraform.tfvars").write_text("\n".join(tfvars_lines) + "\n", encoding="utf-8")

        # terraform.tfvars.example (commented-out copy)
        example_lines = [
            f'# {env_name}.tfvars.example — copy to terraform.tfvars and fill in real values.',
            f'# This file IS committed to source control (no secrets).',
            "",
            f'# project_name = "{project_name}"',
            f'# region       = "{region}"',
            f'# environment  = "{env_name}"',
            f'# owner        = "REPLACE_WITH_OWNER"',
            f'# vpc_cidr     = "10.0.0.0/16"',
            f'# cost_centre  = "REPLACE_WITH_COST_CENTRE"',
        ]
        for line in scalar_lines:
            example_lines.append(f'# {line}')
        for blk in obj_blocks:
            for ln in blk.splitlines():
                example_lines.append(f'# {ln}')

        (env_dir / "terraform.tfvars.example").write_text("\n".join(example_lines) + "\n", encoding="utf-8")

        typer.secho(
            f"  + env/{env_name}/  [backend.tf, terraform.tfvars, terraform.tfvars.example]",
            fg=typer.colors.GREEN,
        )


def _env_override(var_name: str, env_cfg: dict):
    """
    Extract a value from the infra.yaml environments[env] block for a given
    Terraform variable name. Returns None if no override is found.

    Mapping: infra.yaml path -> terraform var name
      eks.node_count     -> eks_node_count
      eks.instance_type  -> eks_instance_type
      lambda.memory_mb   -> lambda_memory_size
      lambda.timeout_s   -> lambda_timeout
    """
    _MAP = {
        "eks_node_count":     ("eks",    "node_count"),
        "eks_instance_type":  ("eks",    "instance_type"),
        "lambda_memory_size": ("lambda", "memory_mb"),
        "lambda_timeout":     ("lambda", "timeout_s"),
    }
    if var_name not in _MAP:
        return None
    svc, key = _MAP[var_name]
    return env_cfg.get(svc, {}).get(key)


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
        ".tf-cache/\n\n"
        "# Quality scan report (regenerated by scaffold-cli)\n"
        "checkov-report.txt\n",
        encoding="utf-8",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Cost estimate
# ─────────────────────────────────────────────────────────────────────────────

# Monthly cost estimates per service (USD, us-east-1, conservative mid-tier)
_SERVICE_COST_ESTIMATES: dict[str, dict] = {
    "lambda": {
        "dev":  {"estimate": "$1–5",   "basis": "1M requests/mo, 512 MB, 30s avg"},
        "prod": {"estimate": "$20–80",  "basis": "50M requests/mo, 1 GB, 30s avg"},
        "notes": "Cost scales with invocations × duration × memory. Free tier: 1M req + 400k GB-s/mo.",
        "optimise": ["Right-size memory with AWS Lambda Power Tuning", "Use Graviton2 runtime (arm64) for ~20% savings"],
    },
    "eks": {
        "dev":  {"estimate": "$150–200",  "basis": "1× t3.medium node + cluster ($0.10/hr)"},
        "prod": {"estimate": "$600–900",  "basis": "3× m5.xlarge + cluster + NAT + LB"},
        "notes": "EKS cluster itself is $0.10/hr ($73/mo). Main cost is EC2 nodes + NAT Gateway ($32/AZ/mo).",
        "optimise": ["Use Karpenter for bin-packing", "Spot instances for non-prod", "Reduce NAT GW with VPC endpoints"],
    },
    "ecs-fargate": {
        "dev":  {"estimate": "$30–60",   "basis": "1 task, 0.25 vCPU, 512 MB, always-on"},
        "prod": {"estimate": "$150–300", "basis": "3 tasks, 1 vCPU, 2 GB, always-on + ALB"},
        "notes": "Fargate charges per vCPU-second and GB-second. ALB adds ~$16/mo base.",
        "optimise": ["Use Fargate Spot for dev/test (up to 70% savings)", "Scale to zero overnight in dev"],
    },
    "rds": {
        "dev":  {"estimate": "$15–25",   "basis": "db.t3.micro, 20 GB gp2, single-AZ"},
        "prod": {"estimate": "$200–400", "basis": "db.m5.large, 100 GB gp3, Multi-AZ"},
        "notes": "Multi-AZ doubles instance cost. gp3 is 20% cheaper than gp2 for same IOPS.",
        "optimise": ["Migrate to gp3 storage", "Use RDS Proxy to reduce connection overhead"],
    },
    "aurora": {
        "dev":  {"estimate": "$50–80",   "basis": "db.t3.medium, serverless v2 minimum"},
        "prod": {"estimate": "$300–600", "basis": "db.r5.large, 2 instances, 3 AZs"},
        "notes": "Aurora Serverless v2 auto-scales. Billed per ACU-hour (min 0.5 ACU).",
        "optimise": ["Aurora Serverless v2 scales to zero in dev", "Read replicas cheaper than Multi-AZ for read-heavy workloads"],
    },
    "sqs": {
        "dev":  {"estimate": "<$1",    "basis": "< 1M messages/mo (free tier)"},
        "prod": {"estimate": "$5–20",  "basis": "50M–500M messages/mo standard queue"},
        "notes": "First 1M requests/mo free. $0.40 per million thereafter. FIFO 10× price.",
        "optimise": ["Batch messages to reduce API calls", "Use long polling (WaitTimeSeconds=20)"],
    },
    "sns": {
        "dev":  {"estimate": "<$1",   "basis": "< 1M publishes/mo (free tier)"},
        "prod": {"estimate": "$5–15", "basis": "10M publishes + email/HTTP deliveries"},
        "notes": "Email delivery $2/100k, HTTP $0.60/million, SMS varies by country.",
        "optimise": ["Filter subscriptions at topic level to reduce unnecessary deliveries"],
    },
    "ecr": {
        "dev":  {"estimate": "$1–5",  "basis": "10 GB storage, < 1 GB/day transfer"},
        "prod": {"estimate": "$5–20", "basis": "50 GB storage + ECR pull in same region (free)"},
        "notes": "Storage: $0.10/GB/mo. Data transfer: free within same region.",
        "optimise": ["Lifecycle policies to expire old images", "ECR pull-through cache for base images"],
    },
    "eventbridge": {
        "dev":  {"estimate": "<$1",  "basis": "< 1M events/mo (free tier)"},
        "prod": {"estimate": "$2–8", "basis": "10M–50M events/mo"},
        "notes": "First 1M events/mo free for custom bus. Scheduler: $1.00 per 1M invocations.",
        "optimise": ["Use event filtering to reduce downstream processing cost"],
    },
    "kms": {
        "dev":  {"estimate": "$1–3",  "basis": "1 CMK ($1/mo) + < 10k API calls"},
        "prod": {"estimate": "$3–10", "basis": "1–3 CMKs + high API call volume"},
        "notes": "Each CMK costs $1/mo. API calls $0.03/10k. Automatic key rotation free.",
        "optimise": ["Share one CMK across services in same account/region to minimise CMK count"],
    },
    "secrets-manager": {
        "dev":  {"estimate": "$1–3",  "basis": "2–5 secrets ($0.40/secret/mo)"},
        "prod": {"estimate": "$5–15", "basis": "10–20 secrets + 1k API calls/mo"},
        "notes": "$0.40/secret/mo. $0.05 per 10k API calls. Rotation Lambda adds compute cost.",
        "optimise": ["Store multiple related values as one JSON secret", "Use SSM Parameter Store (free tier) for non-sensitive config"],
    },
    "cloudwatch": {
        "dev":  {"estimate": "$2–8",   "basis": "5 GB logs/mo, 5 alarms, 1 dashboard"},
        "prod": {"estimate": "$20–60", "basis": "50 GB logs/mo, 20 alarms, 3 dashboards"},
        "notes": "First 5 GB logs/mo free. $0.50/GB ingest after. Dashboard $3/mo each.",
        "optimise": ["Set log retention (avoid indefinite storage)", "Use metric filters instead of custom metrics where possible"],
    },
    "api-gateway": {
        "dev":  {"estimate": "$1–5",   "basis": "< 1M API calls/mo"},
        "prod": {"estimate": "$10–50", "basis": "10M–100M API calls/mo HTTP API"},
        "notes": "HTTP API 71% cheaper than REST API. Use HTTP API unless REST features required.",
        "optimise": ["HTTP API over REST API", "Enable caching for GET-heavy APIs"],
    },
    "s3": {
        "dev":  {"estimate": "$1–3",  "basis": "10 GB storage, 10k GET, 1k PUT"},
        "prod": {"estimate": "$5–25", "basis": "100 GB + moderate GET/PUT traffic"},
        "notes": "Standard: $0.023/GB/mo. GET $0.0004/1k, PUT $0.005/1k. Intelligent-Tiering auto-saves.",
        "optimise": ["Intelligent-Tiering for objects not accessed for 30+ days", "S3 Glacier for archival"],
    },
    "dynamodb": {
        "dev":  {"estimate": "<$1",   "basis": "PAY_PER_REQUEST, < 25 GB (free tier)"},
        "prod": {"estimate": "$20–100", "basis": "PROVISIONED 10 RCU/WCU + 50 GB"},
        "notes": "On-demand mode good for unpredictable traffic. Provisioned cheaper for steady load.",
        "optimise": ["Switch to on-demand for dev, provisioned for prod", "DAX cache reduces read costs by 10×"],
    },
}

_DANGEROUS_COMBOS: list[tuple[list[str], str, str]] = [
    (["eks", "rds"],           "EKS + RDS",            "~$800+/mo in prod (node group + Multi-AZ DB). Consider Aurora Serverless for lower floor."),
    (["eks", "aurora"],        "EKS + Aurora",          "~$900+/mo in prod. Ensure RDS Proxy is used to control connection pool."),
    (["eks"],                  "EKS NAT Gateway",       "NAT Gateway costs $32/AZ/mo + data transfer. Use VPC endpoints for S3/DynamoDB to eliminate NAT traffic."),
    (["ecs-fargate", "rds"],   "ECS Fargate + RDS",     "~$400+/mo in prod. RDS Multi-AZ doubles cost — verify HA requirement."),
    (["lambda", "rds"],        "Lambda + RDS",          "Lambda cold starts + RDS connection limits. Add RDS Proxy (~$0.015/hr) to avoid connection exhaustion."),
    (["lambda", "aurora"],     "Lambda + Aurora",       "Aurora Serverless v2 scales to zero — good fit for Lambda. Ensure VPC + Proxy configured."),
]


def _write_cost_estimate(
    base: Path,
    project_name: str,
    services: list[str],
    environments: dict,
) -> None:
    env_names = list(environments.keys()) if environments else ["dev", "prod"]
    is_prod   = any(e in env_names for e in ("prod", "production", "live"))

    lines = [
        f"# Cost Estimate — {project_name}",
        "",
        "> **Auto-generated by devops-scaffold-tool.** Estimates are approximate monthly costs",
        "> in us-east-1 (2025 pricing). Actual costs depend on traffic, data volume, and usage patterns.",
        "> Always verify with the [AWS Pricing Calculator](https://calculator.aws/pricing/2/home).",
        "",
        "---",
        "",
        "## Per-Service Estimates",
        "",
        "| Service | Dev/Month | Prod/Month | Key Driver |",
        "|---------|-----------|------------|------------|",
    ]

    total_dev_low  = 0
    total_prod_low = 0

    for svc in services:
        est = _SERVICE_COST_ESTIMATES.get(svc)
        if not est:
            continue
        dev_est  = est["dev"]["estimate"]
        prod_est = est["prod"]["estimate"]
        basis    = est["dev"]["basis"]
        lines.append(f"| **{svc}** | {dev_est} | {prod_est} | {basis} |")

        try:
            total_dev_low  += int(dev_est.replace("$", "").replace("<", "").replace(">", "").split("–")[0].strip())
            total_prod_low += int(prod_est.replace("$", "").replace("<", "").replace(">", "").split("–")[0].strip())
        except (ValueError, IndexError):
            pass

    lines += [
        "",
        f"| **TOTAL (rough floor)** | **~${total_dev_low}/mo** | **~${total_prod_low}/mo** | _Conservative estimate_ |",
        "",
        "---",
        "",
        "## Expensive Combinations Detected",
        "",
    ]

    found_combo = False
    for combo_svcs, label, warning in _DANGEROUS_COMBOS:
        if all(s in services for s in combo_svcs):
            lines.append(f"⚠️  **{label}**: {warning}")
            lines.append("")
            found_combo = True

    if not found_combo:
        lines.append("No high-cost combinations detected.")
        lines.append("")

    lines += [
        "---",
        "",
        "## Optimisation Tips",
        "",
    ]

    for svc in services:
        est = _SERVICE_COST_ESTIMATES.get(svc)
        if not est or not est.get("optimise"):
            continue
        lines.append(f"### {svc}")
        for tip in est["optimise"]:
            lines.append(f"- {tip}")
        lines.append("")

    lines += [
        "---",
        "",
        "## Environment Cost Breakdown",
        "",
    ]

    for env in env_names:
        _is_p = env in ("prod", "production", "live")
        tier  = "prod" if _is_p else "dev"
        lines.append(f"### {env}")
        for svc in services:
            est = _SERVICE_COST_ESTIMATES.get(svc)
            if not est:
                continue
            e = est[tier]["estimate"]
            b = est[tier]["basis"]
            lines.append(f"- **{svc}**: {e}  _{b}_")
        lines.append("")

    lines += [
        "---",
        "",
        "## Resources",
        "",
        "- [AWS Pricing Calculator](https://calculator.aws/pricing/2/home)",
        "- [Infracost CLI](https://www.infracost.io/) — cost estimates from Terraform plan",
        "- [AWS Cost Explorer](https://aws.amazon.com/aws-cost-management/aws-cost-explorer/) — actual spend after apply",
    ]

    (base / "cost-estimate.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    typer.secho("  + cost-estimate.md  [per-service estimates + optimisation tips]", fg=typer.colors.GREEN)


