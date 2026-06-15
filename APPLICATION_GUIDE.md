# DevOps Greenfield Scaffold Generator — Application Guide

## Overview

The **DevOps Greenfield Scaffold Generator** is a command-line tool that automatically generates Terraform Infrastructure-as-Code (IaC) scaffolds from a declarative YAML configuration file (`infra.yaml`). It standardizes AWS infrastructure setup by mapping high-level service declarations to production-ready Terraform templates.

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Input Structure (infra.yaml)](#input-structure)
3. [Processing Flow](#processing-flow)
4. [Output Files](#output-files)
5. [Service Mappings](#service-mappings)
6. [Connection System](#connection-system)
7. [Examples](#examples)
8. [Validation Rules](#validation-rules)

---

## How It Works

```
infra.yaml (Input)
      ↓
  [Validation]
      ↓
  [Parse Configuration]
      ↓
  [Infer Connections]
      ↓
  [Merge Templates]
      ↓
  .infra/ Directory (Output)
```

1. **Load Configuration** — Read `infra.yaml` from current directory
2. **Validate** — Ensure required fields and valid service names
3. **Process Services** — Categorize into compute, data, ingress, auth
4. **Infer Connections** — Build service wiring (explicit or implicit)
5. **Render Templates** — Use Jinja2 to generate Terraform files
6. **Write Output** — Create `.infra/` directory structure with all files

---

## Input Structure

### Required: `infra.yaml`

Located in the working directory where you run the CLI.

#### **Project Section** (Required)

```yaml
project:
  name: my-app              # Lowercase, hyphens only, max 20 chars
  region: us-east-1         # AWS region
  owner: platform-team      # Team/owner name
```

**Validation Rules:**
- `name`: Matches regex `^[a-z0-9][a-z0-9-]*[a-z0-9]$`
- `region`: Must be valid AWS region
- `owner`: Any string

#### **Services Section** (Required)

Flat list of all AWS services your infrastructure needs:

```yaml
services:
  # Compute (at least one required)
  - lambda              # Serverless functions
  - ecs-fargate        # Container orchestration
  - eks                # Kubernetes

  # Frontend / CDN
  - static-site        # CloudFront + S3

  # Ingress
  - alb                # Application Load Balancer
  - api-gateway        # REST API Gateway

  # Data Stores
  - postgres           # RDS PostgreSQL
  - mysql              # RDS MySQL
  - redis              # ElastiCache
  - dynamodb           # NoSQL
  - s3                 # Object storage

  # Messaging / Events
  - sqs                # Simple Queue Service
  - eventbridge        # Event bus

  # Security / Auth
  - cognito            # User authentication
  - kms                # Key encryption

  # AI / ML (IAM permissions only)
  - bedrock            # Foundation models
  - polly              # Text-to-speech
```

**Valid Services:**
- **Compute:** `lambda`, `ecs-fargate`, `eks` (at least one required)
- **Frontend:** `static-site`
- **Ingress:** `alb`, `api-gateway`
- **Databases:** `postgres`, `mysql`, `redis`, `dynamodb`, `s3`
- **Messaging:** `sqs`, `eventbridge`
- **Auth:** `cognito`, `kms`
- **AI/ML:** `bedrock`, `polly`

#### **Connections Section** (Optional)

Explicit service-to-service wiring:

```yaml
connections:
  - from: static-site
    to: api-gateway
  
  - from: api-gateway
    to: lambda
  
  - from: lambda
    to: dynamodb
  
  - from: lambda
    to: s3
  
  - from: sqs
    to: lambda
```

**Format:** Each connection has:
- `from` (service name)
- `to` (service name)

**If omitted:** The tool auto-infers connections based on:
- Service co-presence
- Compute target type (Lambda vs ECS vs EKS)
- Standard AWS patterns

#### **Environments Section** (Optional)

Define deployment environments with environment-specific overrides:

```yaml
environments:
  dev:
    eks:
      node_count: 2
      instance_type: t3.medium
    dynamodb:
      billing_mode: PAY_PER_REQUEST
    multi_az: false

  staging:
    eks:
      node_count: 4
      instance_type: t3.large
    multi_az: true

  prod:
    eks:
      node_count: 8
      instance_type: m5.xlarge
    multi_az: true
```

#### **CI/CD Section** (Optional)

Configure auto-deployment behavior:

```yaml
cicd:
  auto_deploy:
    - dev
    - staging
    # Exclude prod for manual approval
```

#### **Flows Section** (Optional)

Document service workflows/flows:

```yaml
flows:
  user_chat_flow:
    description: "User query → chatbot → response"
    services:
      - api-gateway
      - lambda
      - bedrock
      - dynamodb
  
  admin_flow:
    description: "Admin portal → backend → database"
    services:
      - static-site
      - api-gateway
      - lambda
      - dynamodb
```

---

## Processing Flow

### 1. Configuration Loading

```python
config = _load_yaml("infra.yaml")
```

- Reads YAML file
- Parses into Python dictionary
- Returns to main handler

### 2. Validation

```
✓ project.name (lowercase, hyphens only, ≤20 chars)
✓ project.region (required)
✓ project.owner (required)
✓ services list (not empty)
✓ services are valid (all in VALID_SERVICES set)
✓ at least one compute service (lambda | ecs-fargate | eks)
✓ max two compute targets (only lambda+eks combination allowed)
```

### 3. Service Categorization

```python
compute_list = [s for s in services if s in COMPUTE_SERVICES]
compute_target = compute_list[0]  # primary compute
other_services = [s for s in services if s not in COMPUTE_SERVICES]
data_stores = [s for s in other_services if s in DATA_TEMPLATE]
auth_required = "cognito" in services
```

### 4. Connection Inference

**If explicit connections provided:**
```python
connections = {
    f"{c['from']}->{c['to']}"
    for c in config.get("connections", [])
}
```

**If no connections, auto-infer:**
- Messaging chains: `eventbridge→sqs`, `sqs→lambda`, etc.
- Ingress routes: `alb→ecs-fargate`, `api-gateway→lambda`, etc.
- Compute→Data: `lambda→dynamodb`, `lambda→s3`, etc.

### 5. Template Rendering

For each template file:
1. Load Jinja2 template from `parent-repo/templates/`
2. Pass context variables (project name, region, services, connections, etc.)
3. Render to Terraform code
4. Write to output file in `.infra/` directory

### 6. Output Generation

```
.infra/
├── iac/
│   ├── providers.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── networking.tf
│   ├── compute.tf
│   ├── iam.tf
│   ├── data.tf
│   └── observability.tf
├── cicd/
│   └── pipeline.yml
├── environments/
│   ├── dev.tfvars.example
│   ├── staging.tfvars.example
│   └── prod.tfvars.example
├── secrets/
│   └── secrets-policy.yml
└── .gitignore
```

---

## Output Files

### Infrastructure (`.infra/iac/`)

| File | Purpose | Generated From |
|------|---------|-----------------|
| **providers.tf** | Terraform version, AWS provider config | `providers.tf.j2` |
| **variables.tf** | Input variables (region, environment, etc.) | `variables.tf.j2` |
| **outputs.tf** | Export values (API Gateway URL, ELB DNS, etc.) | `outputs.tf.j2` |
| **networking.tf** | VPC, subnets, route tables, security groups | `networking.tf.j2` |
| **compute.tf** | Lambda, ECS, EKS, ALB, API Gateway | Compute + Ingress templates |
| **iam.tf** | IAM roles, policies for all compute targets | IAM service templates |
| **data.tf** | RDS, DynamoDB, S3, Redis, SQS, EventBridge | Data service templates |
| **observability.tf** | CloudWatch logs, alarms | `observability.tf.j2` |

### CI/CD (`.infra/cicd/`)

| File | Purpose |
|------|---------|
| **pipeline.yml** | GitHub Actions workflow (or CI/CD platform definition) |

### Configuration (`.infra/environments/`)

| File | Purpose |
|------|---------|
| **dev.tfvars.example** | Example variables for dev environment |
| **staging.tfvars.example** | Example variables for staging environment |
| **prod.tfvars.example** | Example variables for production environment |

Content template:
```hcl
environment = "dev"
region      = "REPLACE_WITH_REGION"
cost_centre = "REPLACE_WITH_COST_CENTRE"

# Environment-specific settings
eks_node_count    = 2
eks_instance_type = "t3.medium"
```

### Secrets (`.infra/secrets/`)

| File | Purpose |
|------|---------|
| **secrets-policy.yml** | Secrets structure and paths (AWS Secrets Manager / SSM Parameter Store) |

Example:
```yaml
secrets:
  - path: "/my-app/{environment}/db/password"
    service: "AWS Secrets Manager"
    description: "RDS master password — auto-rotated"
  
  - path: "/my-app/{environment}/redis/auth-token"
    service: "AWS Secrets Manager"
    description: "ElastiCache auth token"
```

### Root (.infra/)

| File | Purpose |
|------|---------|
| **.gitignore** | Ignores Terraform state files, lock files, tfvars |

---

## Service Mappings

### Compute Services

| Service | Description | Terraform Template |
|---------|-------------|-------------------|
| `lambda` | Serverless functions | `iac/compute/lambda.tf.j2` |
| `ecs-fargate` | Container on Fargate | `iac/compute/ecs-fargate.tf.j2` |
| `eks` | Kubernetes cluster | `iac/compute/eks.tf.j2` |

### Data Services

| Service | Description | Terraform Template |
|---------|-------------|-------------------|
| `postgres` | RDS PostgreSQL | `iac/data/rds.tf.j2` (with db_engine=postgres) |
| `mysql` | RDS MySQL | `iac/data/rds.tf.j2` (with db_engine=mysql) |
| `redis` | ElastiCache | `iac/data/redis.tf.j2` |
| `dynamodb` | NoSQL | `iac/data/dynamodb.tf.j2` |
| `s3` | Object storage | `iac/data/s3.tf.j2` |
| `sqs` | Message queue | `iac/data/sqs.tf.j2` |
| `eventbridge` | Event bus | `iac/data/eventbridge.tf.j2` |
| `cognito` | User authentication | `iac/data/cognito.tf.j2` |
| `kms` | Key encryption | `iac/data/kms.tf.j2` (auto-added if data services present) |

### Ingress Services

| Service | Valid Compute Targets | Terraform Template |
|---------|----------------------|-------------------|
| `alb` | ecs-fargate, eks | `iac/compute/alb.tf.j2` |
| `api-gateway` | lambda, ecs-fargate, eks | `iac/compute/api-gateway.tf.j2` |

### AI/ML Services

| Service | Description | Output |
|---------|-------------|--------|
| `bedrock` | Foundation models (RAG) | IAM permissions only, no Terraform resource |
| `polly` | Text-to-speech | IAM permissions only, no Terraform resource |

### Frontend Services

| Service | Description | Terraform Template |
|---------|-------------|-------------------|
| `static-site` | CloudFront + S3 | `iac/compute/static-site.tf.j2` |

---

## Connection System

### Explicit Connections

Define exactly how services communicate:

```yaml
connections:
  - from: api-gateway
    to: lambda
  
  - from: lambda
    to: dynamodb
  
  - from: sqs
    to: lambda
```

### Auto-Inferred Connections

If `connections:` section omitted, the tool infers standard patterns:

**Messaging Chains:**
- If `eventbridge` + `sqs` present → `eventbridge→sqs`
- If `sqs` + Lambda compute → `sqs→lambda`
- If `sqs` + ECS compute → `sqs→ecs-fargate`

**Ingress Routes:**
- If `alb` + ECS/EKS compute → `alb→ecs-fargate` or `alb→eks`
- If `api-gateway` + Lambda compute → `api-gateway→lambda`

**Compute→Data:**
- If compute + `postgres`/`mysql`/`redis`/`dynamodb`/`s3` → `lambda→postgres`, etc.

### Template Conditionals

Templates use connections to conditionally render resources:

```jinja2
{% if 'api-gateway->lambda' in connections %}
  # Render API Gateway → Lambda integration
{% endif %}

{% if 'sqs->lambda' in connections %}
  # Render SQS → Lambda trigger
{% endif %}
```

---

## Examples

### Example 1: Simple Lambda API

**Input: infra.yaml**
```yaml
project:
  name: api-service
  region: us-east-1
  owner: backend-team

services:
  - lambda
  - api-gateway
  - dynamodb
```

**Output Behavior:**
- ✓ Generates Lambda function
- ✓ Creates API Gateway REST endpoint
- ✓ Sets up DynamoDB table
- ✓ Auto-infers: `api-gateway→lambda`, `lambda→dynamodb`
- ✓ Generates IAM: Lambda execution role + DynamoDB permissions

**Generated Files:**
```
.infra/iac/
├── providers.tf         (Terraform version, AWS provider)
├── networking.tf        (VPC, subnets)
├── compute.tf           (Lambda + API Gateway)
├── iam.tf               (Lambda IAM role)
├── data.tf              (DynamoDB table)
├── variables.tf         (Input variables)
├── outputs.tf           (API Gateway URL)
└── observability.tf     (CloudWatch logs)
```

### Example 2: ECS with RDS & Cache

**Input: infra.yaml**
```yaml
project:
  name: web-app
  region: us-west-2
  owner: platform-team

services:
  - ecs-fargate
  - alb
  - postgres
  - redis
  - s3

environments:
  dev:
    multi_az: false
  prod:
    multi_az: true
```

**Output Behavior:**
- ✓ ECS Fargate cluster with ALB
- ✓ RDS PostgreSQL database
- ✓ ElastiCache Redis cluster
- ✓ S3 bucket
- ✓ Auto-infers: `alb→ecs-fargate`, `ecs-fargate→postgres`, `ecs-fargate→redis`, `ecs-fargate→s3`
- ✓ Generates environment-specific tfvars (dev, prod)

### Example 3: Kubernetes Microservices

**Input: infra.yaml**
```yaml
project:
  name: microservices
  region: eu-west-1
  owner: platform-team

services:
  - eks
  - api-gateway
  - s3
  - dynamodb
  - sqs
  - cognito

connections:
  - from: api-gateway
    to: eks
  
  - from: eks
    to: dynamodb
  
  - from: eks
    to: s3
  
  - from: sqs
    to: eks

flows:
  request_flow:
    description: "API → EKS pods → DynamoDB"
  async_flow:
    description: "SQS → EKS workers → S3"
```

**Output Behavior:**
- ✓ EKS cluster with node groups
- ✓ API Gateway REST endpoint
- ✓ Explicit connections honored
- ✓ IAM: EKS pod role + node role, DynamoDB/S3 permissions
- ✓ Cognito user pool
- ✓ Flows documented in context

---

## Validation Rules

### Project Metadata

| Field | Rule | Error |
|-------|------|-------|
| `name` | Lowercase, hyphens only, 2-20 chars | `project.name invalid: must match [a-z0-9][a-z0-9-]*[a-z0-9]` |
| `region` | Required, non-empty | `project.region is required` |
| `owner` | Required, non-empty | `project.owner is required` |

### Services

| Rule | Error |
|------|-------|
| At least one service | `ERROR: services list is empty` |
| All services valid | `ERROR: unknown services: [service]` |
| At least one compute service | `ERROR: no compute target found` |
| Max two compute services | `ERROR: too many compute targets` |
| Compute combo: only `lambda + eks` | `ERROR: invalid compute combination` |

### Connections

| Rule | Error |
|------|-------|
| Both `from` and `to` present | Silently skipped if missing |
| Service names valid | No explicit check, but used in templates |

### Existing Scaffold

| Scenario | Behavior |
|----------|----------|
| `.infra/` exists | Show warning, ask for confirmation |
| Overwrite confirmed | Replace all `.infra/iac/*.tf` files |
| Overwrite declined | Exit without changes |

---

## Command Reference

### Basic Usage

```bash
# Dry-run (see what would be generated)
python scaffold-cli/main.py --dry-run

# Full generation (creates .infra/)
python scaffold-cli/main.py

# With confirmation prompt
python scaffold-cli/main.py --confirm
```

### Flags

| Flag | Description |
|------|-------------|
| `--dry-run` | Show files that would be generated without writing |
| `--help` | Show help message |

---

## Error Handling

### Common Errors

**Error:** `ERROR: infra.yaml not found`
- **Cause:** Running from wrong directory
- **Fix:** Run from directory containing `infra.yaml`

**Error:** `ERROR: unknown services: ['dynamodb']`
- **Cause:** Service name misspelled
- **Fix:** Check spelling against valid services list

**Error:** `ERROR: no compute target found`
- **Cause:** No lambda/ecs-fargate/eks in services
- **Fix:** Add at least one compute service

**Error:** `ERROR: invalid compute combination`
- **Cause:** Unsupported compute pair (e.g., lambda+ecs-fargate)
- **Fix:** Use only `lambda+eks` for multi-compute

---

## Summary

| Aspect | Details |
|--------|---------|
| **Input** | `infra.yaml` (project, services, connections, environments) |
| **Validation** | Project metadata, service names, compute requirements, connections |
| **Processing** | Load → Validate → Categorize → Infer → Render → Write |
| **Output** | `.infra/` directory with Terraform, CI/CD, config, secrets files |
| **Connections** | Explicit (declared) or implicit (auto-inferred from service co-presence) |
| **Extensibility** | Jinja2 templates in `parent-repo/templates/` can be customized |

