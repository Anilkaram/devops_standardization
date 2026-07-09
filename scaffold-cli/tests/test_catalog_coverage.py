"""Catalog coverage: every catalog service must generate a non-empty module.

Guards against the class of bug where a service silently produces an empty
"# TODO" module (as rds/autoscaling once did) — the stack still validates
because an empty module has nothing to check.
"""
import sys
from pathlib import Path

import pytest

SCAFFOLD_CLI = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCAFFOLD_CLI))

import importlib.util

_spec = importlib.util.spec_from_file_location("dynamic_generator", SCAFFOLD_CLI / "dynamic_generator.py")
dg = importlib.util.module_from_spec(_spec)
sys.modules["dynamic_generator"] = dg
_spec.loader.exec_module(dg)

import generator  # noqa: E402


def _template_services(catalog: dict) -> list[str]:
    """Catalog services with a static template (AI-generated ones need a live provider)."""
    infra_layers = set(dg.get_infra_layer_names(catalog))
    return [
        name for name, entry in catalog.get("services", {}).items()
        if entry.get("template") is not None and name not in infra_layers
    ]


def test_every_catalog_service_generates_a_real_module(tmp_path, monkeypatch):
    catalog  = dg.load_catalog()
    services = _template_services(catalog)
    assert services, "catalog has no template-backed services — catalog load broken?"

    config = {
        "project": {"name": "coverage-test", "region": "us-east-1", "owner": "ci"},
        "services": services,
        "environments": {"dev": {}, "prod": {}},
    }

    monkeypatch.chdir(tmp_path)
    generator.generate_scaffold(config, catalog, output_dir=str(tmp_path / ".infra"))

    modules_dir = tmp_path / ".infra" / "modules"
    assert modules_dir.exists(), "no modules directory generated"

    empty = []
    for mod in sorted(modules_dir.iterdir()):
        main_tf = mod / "main.tf"
        if not main_tf.exists():
            empty.append(f"{mod.name}: missing main.tf")
            continue
        content = main_tf.read_text(encoding="utf-8")
        if "resource " not in content and "module " not in content and "data " not in content:
            empty.append(f"{mod.name}: main.tf has no resource/module/data block")

    assert not empty, "Empty or placeholder modules generated:\n  " + "\n  ".join(empty)


def test_serverless_stack_skips_vpc(tmp_path, monkeypatch):
    """Pure serverless/managed stacks must not generate a VPC (no idle NAT cost)."""
    catalog = dg.load_catalog()
    config = {
        "project": {"name": "no-vpc-test", "region": "us-east-1", "owner": "ci"},
        "services": ["lambda", "api-gateway", "dynamodb", "s3", "cloudwatch"],
        "environments": {"dev": {}},
    }
    monkeypatch.chdir(tmp_path)
    generator.generate_scaffold(config, catalog, output_dir=str(tmp_path / ".infra"))
    assert not (tmp_path / ".infra" / "networking.tf").exists(), \
        "serverless stack generated a VPC it does not need"
    # and no dangling module.vpc references anywhere at root
    for tf in (tmp_path / ".infra").glob("*.tf"):
        assert "module.vpc" not in tf.read_text(encoding="utf-8"), f"dangling vpc ref in {tf.name}"


def test_network_stack_keeps_vpc(tmp_path, monkeypatch):
    """Network-attached services (ec2/rds/alb) must still generate the VPC."""
    catalog = dg.load_catalog()
    config = {
        "project": {"name": "vpc-test", "region": "us-east-1", "owner": "ci"},
        "services": ["ec2", "rds", "kms"],
        "environments": {"dev": {}},
    }
    monkeypatch.chdir(tmp_path)
    generator.generate_scaffold(config, catalog, output_dir=str(tmp_path / ".infra"))
    assert (tmp_path / ".infra" / "networking.tf").exists()


def test_root_wires_every_module(tmp_path, monkeypatch):
    """Every generated module must be referenced from the root main.tf."""
    catalog  = dg.load_catalog()
    services = _template_services(catalog)
    config = {
        "project": {"name": "coverage-test", "region": "us-east-1", "owner": "ci"},
        "services": services,
        "environments": {"dev": {}},
    }
    monkeypatch.chdir(tmp_path)
    generator.generate_scaffold(config, catalog, output_dir=str(tmp_path / ".infra"))

    root_main = (tmp_path / ".infra" / "main.tf").read_text(encoding="utf-8")
    unwired = [
        mod.name for mod in sorted((tmp_path / ".infra" / "modules").iterdir())
        if f"./modules/{mod.name}" not in root_main
    ]
    assert not unwired, f"Modules generated but never called from root main.tf: {unwired}"
