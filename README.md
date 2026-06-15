# DevOps Standardization Tool
**Greenfield Infrastructure Scaffold Generator — AWS v1**

Every greenfield project currently starts from tribal knowledge. This tool eliminates that. The DevOps Standardization Tool is a conversational CLI that accepts a minimal project descriptor (`infra.yaml`) and generates a complete, opinionated AWS infrastructure scaffold using Terraform and GitHub Actions.

## 🚀 Features
* **Zero Tribal Knowledge:** Enforces AWS best practices (Least Privilege IAM, strictly defined VPCs, mandatory tagging) automatically.
* **Conversational CLI:** Prompts interactively for missing architectural decisions and explains trade-offs.
* **Decision Auditing:** Every choice is logged to `decisions.md` with a timestamp and rationale. No more guessing *why* an architecture was chosen.
* **Rules-Driven Engine:** Infrastructure choices are decoupled from the tool code. Rules live in a versioned parent repo.
* **Idempotent:** Safe to re-run. Prior decisions are saved and respected.

## 📦 Output Structure
Running the tool generates a predictable, fixed `.infra/` directory inside your project:

```text
.infra/
├── iac/                    # Terraform files (compute, data, networking, iam)
├── cicd/                   # GitHub Actions workflows
├── environments/           # tfvars for dev, staging, and prod
├── secrets/                # Secrets Manager structure policies
└── decisions.md            # Architecture Decision Record (ADR)