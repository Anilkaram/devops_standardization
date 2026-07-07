# DevOps Standardization Tool

**AWS Infrastructure Scaffold Generator**

One `infra.yaml` in → a complete, security-hardened, multi-environment Terraform workspace out. Every service becomes a reusable module, every environment gets its own tfvars, and the output ships pre-validated: `terraform validate` clean and **Checkov security score 100%** out of the box.

```
infra.yaml (15–100 lines)  ──►  scaffold init  ──►  .infra/ (40+ AWS resources, plan-ready)
```

---

## Why this tool — the DevOps adoption case

| Pain today | With the scaffold tool |
|---|---|
| Every team writes Terraform differently — no naming, tagging, or layout standard | One generator = one standard. Same module layout, `<project>-<env>-<resource>` naming, and mandatory tags on every stack |
| Security hardening is an afterthought — Checkov/tfsec findings pile up before release | Templates are pre-hardened: KMS encryption, IMDSv2, private subnets, least-privilege IAM. **Checkov passes from the first generate** |
| Weeks to bootstrap a new service's infra | Minutes: write `infra.yaml`, run `scaffold init`, fill a short checklist of real-world values (cert ARN, state bucket) |
| Dev/prod drift — hand-copied `.tf` files diverge | Same modules for every env; **only `env/<env>/terraform.tfvars` differs** (sizing, multi-AZ, retention) |
| Secrets accidentally committed in tfvars | Built-in secret scan flags plaintext credentials (even inside the `secrets` map) with file + line, and fails the gate |
| "Is this stack safe to apply?" is a manual review | `scaffold validate` — one command, four gates, CI-ready exit code |
| Regenerating wipes manual customizations | Idempotent re-init: a hash manifest detects your edits and **preserves them** (override with `--force`) |
| Adding a queue/bucket/secret means touching module code | `map(object)` + `for_each` pattern: **add resources by editing tfvars only** |

---

## Quick start

```bash
# Install (editable — one time, from the workspace root)
pip install -e .

# 1. Write infra.yaml (see "Writing infra.yaml" below), then:
scaffold init            # interactive — prompts for anything missing
scaffold init --yes      # non-interactive — use infra.yaml + defaults
scaffold init --describe "Python Lambda behind API Gateway with SQS and KMS"   # AI extracts config

# 2. Health-check and fill in real-world values
scaffold doctor          # lists every unfilled placeholder with plain-English hints

# 3. Create the remote-state backend (once per project)
scaffold init-backend    # S3 state bucket + DynamoDB lock table

# 4. Quality gate — run any time, wire into CI
scaffold validate        # terraform validate + Checkov + secret scan + placeholder check
scaffold validate --plan # additionally runs terraform plan (needs AWS credentials)

# 5. Deploy
cd .infra
terraform init
terraform plan  -var-file=env/dev/terraform.tfvars
terraform apply -var-file=env/dev/terraform.tfvars
```

## Command reference

| Command | What it does |
|---|---|
| `scaffold init` | Generate the full Terraform workspace from `infra.yaml`. Ends with a **NEXT STEPS checklist** of values you must fill. Re-running preserves files you edited (`--force` to overwrite; `--dry-run` to preview) |
| `scaffold doctor` | Diagnose the scaffold: required tools present, unfilled `REPLACE_WITH_*` placeholders (with file:line + what each value means), plaintext-secret scan, list of user-modified files. Exit 1 if anything needs fixing |
| `scaffold validate` | The quality gate. Five checks, one scorecard: **terraform validate**, **Checkov security scan** (with % score), **tfsec security scan** (second engine, fails on critical/high), **tfvars secret scan**, **placeholder check**. `--plan` adds a real `terraform plan`. Non-zero exit on any failure → drop it straight into CI |
| `scaffold init-backend` | Bootstrap the S3 state bucket + DynamoDB lock table (run once), then patch `backend.tf` |
| `scaffold update <svc...>` | **AI-assisted template refresh**: reviews the Jinja templates for deprecated arguments, new provider requirements, and missing best practices. The AI only proposes — every proposal is verified by regenerating a stack and running `terraform validate` before it's applied; originals are backed up to `templates/.backups/`. `--all` for every template, `--dry-run` to preview the diff, `-y` to skip confirmation. Needs an AI provider key (same as `--describe`) |
| `scaffold services` | List the service catalog by category, showing which are static-template vs AI-generated |
| `scaffold providers` | Show the configured AI provider/model (AI is only used for `--describe` and non-catalog services) |

---

## How the Terraform is generated

The generator walks three layers for every service in `infra.yaml`:

```
infra.yaml
   │
   ▼
┌───────────────────────────────────────────────────────────────┐
│ 1. Service catalog lookup (services_catalog.yaml)             │
│    static Jinja2 template?  → render it (hardened HCL)        │
│    no template?             → AI generates the module         │
├───────────────────────────────────────────────────────────────┤
│ 2. Module assembly   modules/<svc>/                           │
│    main.tf       the resources (reads var.* only)             │
│    variables.tf  declarations — auto-completed: every var.*   │
│                  the module uses is guaranteed declared       │
│    outputs.tf    ARNs/names re-exported for cross-wiring      │
├───────────────────────────────────────────────────────────────┤
│ 3. Root wiring       .infra/main.tf                           │
│    module "rds" { … } blocks connect each module to           │
│    VPC subnets, the shared security group, KMS, and root vars │
└───────────────────────────────────────────────────────────────┘
   │
   ▼
env/<env>/terraform.tfvars   ← the ONLY per-environment file
```

Key design rules:

- **Modules never hardcode environment values.** They read variables; the root `main.tf` wires those variables to other modules' outputs (`module.vpc.private_subnets`, `module.kms.key_arn`) and to root variables.
- **Values live in tfvars.** `environments:` in infra.yaml becomes `env/dev|uat|prod/terraform.tfvars`. Dev and prod deploy **identical code** — only the tfvars differ (`multi_az`, node counts, retention...).
- **tfvars only contains real knobs.** Services that size themselves from the environment name (RDS instance class, backup retention) get no tfvars entry — that's intentional, not missing.
- **`map(object)` + `for_each` everywhere it fits.** Adding a second queue or secret = adding a map entry in tfvars. No module code changes, ever.
- **Cross-cutting infra is always generated**: VPC + subnets (`networking.tf`), IAM roles (`iam.tf`), shared security group (`security.tf`), tagging (`provider.tf` → `local.common_tags`), plus a GitHub Actions pipeline (`cicd/pipeline.yml`) and a cost estimate (`cost-estimate.md`).

### Generated layout

```
.infra/
├── provider.tf          # AWS provider, versions, common_tags
├── main.tf              # root module calls — one block per service
├── networking.tf        # VPC, public/private subnets, NAT
├── security.tf          # shared app security group
├── iam.tf               # roles + least-privilege policies
├── locals.tf            # cross-module ARN resolution
├── variables.tf         # root declarations (no values)
├── modules/<svc>/       # main.tf / variables.tf / outputs.tf per service
├── env/<env>/           # backend.tf + terraform.tfvars per environment
├── cicd/pipeline.yml    # GitHub Actions: validate → plan → apply per env
├── cost-estimate.md     # monthly cost projection
├── checkov-report.txt   # security scan report
└── decisions.md         # audit log of every generation decision
```

---

## Writing infra.yaml — from an architecture diagram

When you have an architecture image (draw.io, Lucidchart, a whiteboard photo) plus requirement notes, translate it section by section. **Rule of thumb: every box in the diagram is a `services:` entry; every arrow is a `connections:` entry.**

### 1. `project` — the title block of your diagram
```yaml
project:
  name: newton-platform        # lowercase + hyphens, max 20 chars — prefixes every resource
  region: us-east-1
  owner: platform-team         # becomes the Owner tag on all resources
```

### 2. `services` — one entry per box in the image
Walk the diagram edge-to-core and list what you see (`scaffold services` shows all valid names):

```yaml
services:
  - cloudfront          # the CDN box at the edge
  - waf                 # the shield icon in front of everything
  - s3                  # the static-assets bucket
  - api-gateway         # the API entry point
  - lambda              # serverless compute box
  - eks                 # the Kubernetes cluster box
  - ecr                 # container registry
  - kms                 # the key icon — encryption at rest
  - secrets-manager     # credentials store
  - cloudwatch          # the monitoring box
```
Don't list VPC, subnets, IAM, or security groups — the networking/IAM layer is **always generated automatically**.

### 3. `connections` — one entry per arrow
These document the data flow and drive IAM/wiring decisions:
```yaml
connections:
  - { from: cloudfront,  to: s3     }   # CDN serves static files from bucket
  - { from: api-gateway, to: lambda }   # API calls invoke Lambda
  - { from: ecr,         to: eks    }   # cluster pulls images
  - { from: kms,         to: s3     }   # encryption edges from the key icon
```

### 4. `environments` — the sizing table from your requirements
Any per-env numbers in the requirement doc (instance sizes, node counts, HA) go here — they become each env's `terraform.tfvars`:
```yaml
environments:
  uat:
    multi_az: true
    eks:    { node_count: 2, instance_type: t3.xlarge }
    lambda: { memory_mb: 512, timeout_s: 30 }
  prod:
    multi_az: true
    eks:    { node_count: 3, instance_type: t3.2xlarge }
    lambda: { memory_mb: 1024, timeout_s: 30 }
```

### 5. `auth`, `flows`, `cicd` — the annotations
```yaml
auth:
  required: true
  method: iam            # or cognito for user-facing JWT auth

flows:                   # optional but valuable: narrate the numbered arrows
  user_request_flow:
    description: "Client → WAF → CloudFront/S3 for static, API Gateway → Lambda → EKS for dynamic."
    services: [cloudfront, s3, api-gateway, lambda, eks]

cicd:
  auto_deploy:   [dev]
  manual_deploy: [uat, prod]   # approval gate before promotion
```

**Shortcut:** paste the diagram description into `scaffold init --describe "..."` and the AI drafts the infra.yaml for you; review it, then re-run `init --yes`.

After generation, `infra.yaml.example` is written next to your file with the complete field reference.

---

## Built-in guardrails

- **Security-hardened templates** — encryption at rest (KMS/SSE), IMDSv2, private subnets for data services, account-scoped IAM ARNs, log retention, WAF managed rules. Verified by **two independent scanners**: Checkov on every `init`, plus tfsec in `scaffold validate` (skipped with a hint if not installed).
- **Secret scan** — plaintext credentials in any tfvars (including `value = "..."` inside the `secrets` map) are reported with file + line and fail `doctor`/`validate`. Remediation guidance included (`aws secretsmanager put-secret-value` post-apply).
- **Placeholder discipline** — values only you can know (ACM cert ARN, state bucket, AMI ID) are generated as `REPLACE_WITH_*` and tracked until you fill them.
- **Idempotent regeneration** — `.infra/.scaffold-manifest.json` fingerprints every generated file; re-running `init` after you customized a module keeps your changes.
- **Decision audit** — every choice (from prompt, yaml, or AI) is logged to `.infra/decisions.md` for review.
- **Coverage-tested generator** — the test suite generates every catalog service and asserts each module contains real resources and is wired from root (`scaffold-cli/tests/`).

## Requirements

- Python ≥ 3.10, Terraform ≥ 1.5, Checkov (`pip install checkov`) for the security gate
- Optional: tfsec (or Trivy) for the second security gate — [releases](https://github.com/aquasecurity/tfsec/releases), or set `TFSEC_PATH` to the binary
- An AI provider key only if you use `--describe` or non-catalog services (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY`)

## Repository layout

```
devops-scaffold-workspace/
├── scaffold-cli/        # the generator CLI (main.py, generator.py, catalog, tests)
├── parent-repo/         # Jinja2 templates (iac/, cicd/) — the standardization source of truth
├── testing-ground/      # sandbox: sample infra.yaml + generated .infra/
├── pyproject.toml       # pip install -e . → `scaffold` command
└── APPLICATION_GUIDE.md # deep-dive walkthrough
```
