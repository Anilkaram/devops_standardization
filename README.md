# DevOps Standardization Tool
**AWS Infrastructure Scaffold Generator — v1**

A conversational CLI that generates production-ready Terraform IaC and GitHub Actions pipelines from a minimal project descriptor. No tribal knowledge required.

---

## Quick Start

### 1. Install dependencies

```bash
cd devops-scaffold-workspace/scaffold-cli
pip install -r requirements.txt
```

### 2. Set your AI provider key

```bash
# Claude (default)
set ANTHROPIC_API_KEY=sk-ant-...

# Or OpenAI
set AI_PROVIDER=openai
set OPENAI_API_KEY=sk-...

# Or Gemini
set AI_PROVIDER=gemini
set GOOGLE_API_KEY=AIza...
```

### 3. Run the tool

```bash
# Option A: Interactive — answer prompts to build your config
python scaffold-cli/main.py init

# Option B: Describe your architecture in plain English
python scaffold-cli/main.py init --describe "Python FastAPI backend on ECS Fargate with Postgres and Redis, deployed to dev/staging/prod"

# Option C: Use infra.yaml and fill in what's missing interactively
#   (place infra.yaml in the directory you run from)
python scaffold-cli/main.py init

# Option D: Dry run — preview files without writing
python scaffold-cli/main.py init --dry-run
```

### 4. Review output

```
.infra/
├── provider.tf            # AWS provider + Terraform version + locals
├── main.tf                # Compute resources (ECS, Lambda, EKS, EC2)
├── networking.tf          # VPC, subnets, security groups
├── iam.tf                 # IAM roles and policies
├── data.tf                # Databases, queues, storage
├── observability.tf       # CloudWatch logs and alarms
├── output.tf              # Terraform outputs (URLs, ARNs)
├── variables.tf           # Variable declarations (no hardcoded defaults)
├── env/
│   ├── dev/
│   │   ├── backend.tf             # S3 remote state for dev
│   │   ├── terraform.tfvars       # Actual values for dev
│   │   └── terraform.tfvars.example
│   ├── staging/  (same structure)
│   └── prod/     (same structure)
├── cicd/
│   ├── pipeline.yml       # Full GitHub Actions pipeline
│   └── README.md          # Pipeline setup guide
├── secrets/
│   └── secrets-policy.yml
└── decisions.md           # Architecture Decision Record
```

---

## How It Works

```
infra.yaml (optional)
        |
        v
  --describe flag --> AI extracts config from free-text description
        |
        v
  Interactive prompts -- only asks for fields that are still missing
        |
        v
  Anti-pattern checks (EKS + solo team? Missing auth on public backend?)
        |
        v
  Static templates -- well-known services (ECS, Lambda, RDS, Redis...)
  AI-generated files -- unknown or complex services via Claude/OpenAI/Gemini
        |
        v
  .infra/ scaffold + cicd/pipeline.yml + decisions.md
```

### Three paths for any project size

| Project size | Recommended approach |
|---|---|
| Small / prototype | Interactive prompts -- answer 8-10 questions |
| Medium | `infra.yaml` with main services + interactive fills gaps |
| Large / complex | `infra.yaml` + `--describe` for AI extraction of complex wiring |

---

## Switching AI Models

See [APPLICATION_GUIDE.md](APPLICATION_GUIDE.md) for the full guide.

**Quick reference:**

```bash
# Use Claude (default)
set ANTHROPIC_API_KEY=sk-ant-...
python scaffold-cli/main.py init

# Use OpenAI
set AI_PROVIDER=openai
set OPENAI_API_KEY=sk-...
python scaffold-cli/main.py init

# Use Gemini
set AI_PROVIDER=gemini
set GOOGLE_API_KEY=AIza...
python scaffold-cli/main.py init

# Override the model version
set AI_MODEL=gpt-4o
python scaffold-cli/main.py init

# Or via CLI flags (no env var needed)
python scaffold-cli/main.py init --ai-provider openai --ai-model gpt-4o-mini

# See all provider status
python scaffold-cli/main.py providers
```

---

## Commands

| Command | Description |
|---|---|
| `python scaffold-cli/main.py init` | Generate scaffold (interactive) |
| `python scaffold-cli/main.py init --dry-run` | Preview files without writing |
| `python scaffold-cli/main.py init --describe "..."` | AI extracts config from text |
| `python scaffold-cli/main.py init --yes` | Skip interactive prompts |
| `python scaffold-cli/main.py services` | List all supported services |
| `python scaffold-cli/main.py providers` | Show AI provider status |

---

## infra.yaml Quick Reference

Minimal:

```yaml
project:
  name: my-api
  region: us-east-1
  owner: platform-team

services:
  - lambda
  - api-gateway
  - postgres

environments:
  dev: {}
  staging: {}
  prod: {}
```

Full schema with all options: see [APPLICATION_GUIDE.md](APPLICATION_GUIDE.md).

---

## Applying the Terraform

```bash
cd .infra

# Initialize for the dev environment
terraform init -backend-config=env/dev/backend.tf

# Plan
terraform plan -var-file=env/dev/terraform.tfvars

# Apply
terraform apply -var-file=env/dev/terraform.tfvars
```

---

## Repository Layout

```
devops-scaffold-workspace/
├── scaffold-cli/              # The tool -- run main.py from here
│   ├── main.py                # CLI entry point
│   ├── generator.py           # Static Terraform file writer
│   ├── dynamic_generator.py   # AI-powered generator for unknown services
│   ├── pipeline_generator.py  # GitHub Actions pipeline builder
│   ├── interactive_prompts.py # Conversational CLI prompts
│   ├── config_extractor.py    # --describe AI extraction
│   ├── ai_client.py           # Claude / OpenAI / Gemini abstraction
│   ├── decisions.py           # decisions.md writer
│   ├── services_catalog.yaml  # Source of truth for 50+ AWS services
│   └── requirements.txt
├── parent-repo/
│   └── templates/             # Jinja2 Terraform templates (static services)
└── testing-ground/            # Sample project with infra.yaml for testing
```
