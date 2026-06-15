import os
from typing import Any

import typer

SYSTEM_PROMPT = """You are an infrastructure configuration extractor for cloud-native applications.
Extract structured configuration fields from plain-language project descriptions.
Only populate fields that are clearly stated or strongly implied by the description.
Be conservative — only include fields you are confident about.
Do not guess or infer fields that are not mentioned."""


def extract_config_from_description(description: str) -> dict[str, Any]:
    """Call Claude to extract structured infra config from a free-text description.

    Returns a flat dict with dotted keys: {"project.type": "web-api", ...}
    Returns {} if ANTHROPIC_API_KEY is missing, anthropic is not installed, or the API call fails.
    """
    try:
        import anthropic
    except ImportError:
        typer.secho(
            "  anthropic package not installed. Run: pip install anthropic",
            fg=typer.colors.YELLOW,
        )
        return {}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        typer.secho(
            "  ANTHROPIC_API_KEY not set — skipping LLM extraction.",
            fg=typer.colors.YELLOW,
        )
        return {}

    client = anthropic.Anthropic(api_key=api_key)

    tools = [
        {
            "name": "extract_infra_config",
            "description": (
                "Extract structured infrastructure configuration from a plain-language "
                "project description. Only include fields that are clearly stated or "
                "strongly implied. Omit fields you cannot determine."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_name": {
                        "type": "string",
                        "description": (
                            "Project name (lowercase, hyphens only, max 20 chars). "
                            "Only include if explicitly named."
                        ),
                    },
                    "project_type": {
                        "type": "string",
                        "enum": ["web-api", "static-site", "data-pipeline", "worker"],
                        "description": (
                            "Type of application: web-api (REST/GraphQL/backend service), "
                            "static-site (frontend, docs, marketing), "
                            "data-pipeline (ETL, batch jobs, data processing), "
                            "worker (background, event-driven, queue consumer)."
                        ),
                    },
                    "runtime_language": {
                        "type": "string",
                        "description": "Primary programming language (e.g. python, node, go, java, ruby).",
                    },
                    "runtime_containerised": {
                        "type": "boolean",
                        "description": (
                            "True if containers, Docker, ECS, Fargate, or Kubernetes/EKS are mentioned. "
                            "False if Lambda, serverless, or functions are mentioned. "
                            "If both Lambda AND EKS/containers are mentioned together, set to True "
                            "(the containerised path takes precedence for infrastructure purposes)."
                        ),
                    },
                    "cloud_region": {
                        "type": "string",
                        "description": "AWS region (e.g. us-east-1, eu-west-1). Only include if explicitly mentioned.",
                    },
                    "team_size": {
                        "type": "string",
                        "enum": ["solo", "small", "medium", "large"],
                        "description": "Team size: solo=1 person, small=2-5, medium=6-15, large=15+.",
                    },
                    "team_ops_maturity": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": (
                            "DevOps / ops maturity level. "
                            "Set to 'high' if EKS, Kubernetes, KMS, or advanced AWS services are mentioned. "
                            "Set to 'low' if fully managed/serverless-only services are mentioned. "
                            "Otherwise only include if clearly implied."
                        ),
                    },
                    "stage": {
                        "type": "string",
                        "enum": ["prototype", "early", "growth", "scale"],
                        "description": (
                            "Project stage: prototype (no real users yet), "
                            "early (first real users), "
                            "growth (scaling up), "
                            "scale (high traffic, strict reliability)."
                        ),
                    },
                    "data_stores": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["postgres", "mysql", "redis", "s3", "dynamodb", "sqs", "eventbridge"],
                        },
                        "description": (
                            "Data storage and messaging services used. "
                            "Map: PostgreSQL/Postgres/RDS/Amazon RDS/relational database → 'postgres', "
                            "MySQL → 'mysql', "
                            "Redis/cache → 'redis', "
                            "S3/object storage/files/archive → 's3', "
                            "DynamoDB/NoSQL → 'dynamodb', "
                            "SQS/queue/message queue/buffering → 'sqs', "
                            "EventBridge/event bus/event routing → 'eventbridge'. "
                            "When 'RDS' or 'relational database' is mentioned without a specific engine, default to 'postgres'. "
                            "Redshift is output, not a store — do not include it."
                        ),
                    },
                    "auth_required": {
                        "type": "boolean",
                        "description": "Whether user authentication is needed. Only include if mentioned.",
                    },
                },
                "required": [],
            },
        }
    ]

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": description}],
            tools=tools,
            tool_choice={"type": "any"},
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_infra_config":
                raw = block.input
                field_map = {
                    "project_name": "project.name",
                    "project_type": "project.type",
                    "runtime_language": "runtime.language",
                    "runtime_containerised": "runtime.containerised",
                    "cloud_region": "cloud.region",
                    "team_size": "team.size",
                    "team_ops_maturity": "team.ops_maturity",
                    "stage": "stage",
                    "data_stores": "data.stores",
                    "auth_required": "auth.required",
                }
                return {config_key: raw[tool_key] for tool_key, config_key in field_map.items() if tool_key in raw}

    except Exception as e:
        typer.secho(f"  LLM extraction failed: {e}", fg=typer.colors.YELLOW)

    return {}
