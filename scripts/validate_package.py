from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_STAGES = {
    "data-ingestion",
    "data-quality",
    "feature-engineering",
    "factor-screening",
    "portfolio-construction",
    "backtesting",
    "evaluation",
    "risk",
    "reporting",
    "orchestration",
}
PLATFORMS = {"cursor", "claude-code", "codex", "hermes", "openclaw"}
REQUIRED_FILES = [
    ROOT / "README.md",
    ROOT / "README.en.md",
    ROOT / "CLAUDE.md",
    ROOT / "LICENSE",
    ROOT / "agents" / "openai.yaml",
    ROOT / "agents" / "portable-loader.md",
    ROOT / ".cursor" / "rules" / "quantskills-agent.mdc",
    ROOT / ".github" / "workflows" / "validate.yml",
    ROOT / "config" / "default.yaml",
    ROOT / "references" / "data-contract.md",
    ROOT / "references" / "research-boundary.md",
]
TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".toml", ".py", ".txt", ".cfg", ".ini"}
SECRET_PATTERNS = (
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"(?i)\b(?:sk|ghp|github_pat)_[A-Za-z0-9_\-]{20,}\b"),
)


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError(f"missing YAML frontmatter: {path}")
    value = yaml.safe_load(parts[1])
    if not isinstance(value, dict):
        raise ValueError(f"invalid YAML frontmatter: {path}")
    return value


def check_required_files(errors: list[str]) -> None:
    for path in REQUIRED_FILES:
        if not path.is_file():
            errors.append(f"missing required file: {path.relative_to(ROOT)}")


def check_path_references(errors: list[str]) -> None:
    """Check local Markdown links without attempting to validate URLs."""
    link_pattern = re.compile(r"\[[^]]+\]\(([^)]+)\)")
    for path in (ROOT / "README.md", ROOT / "README.en.md", ROOT / "AGENTS.md"):
        text = path.read_text(encoding="utf-8")
        for target in link_pattern.findall(text):
            target = target.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            candidate = (path.parent / target).resolve()
            try:
                candidate.relative_to(ROOT.resolve())
            except ValueError:
                errors.append(f"{path.relative_to(ROOT)}: link escapes repository: {target}")
                continue
            if not candidate.exists():
                errors.append(f"{path.relative_to(ROOT)}: missing linked path: {target}")


def check_secrets(errors: list[str]) -> None:
    excluded_parts = {"data", "outputs", ".git", ".pytest_cache", "__pycache__", "agent-template-main"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if excluded_parts.intersection(path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        # Runtime credential acquisition is intentionally allowed: values are
        # supplied interactively or through the environment and are never
        # persisted. Only concrete literals should fail this repository scan.
        scan_text = re.sub(
            r"(?ms)(?:getpass\.getpass\([^\n]*\)|os\.environ\.get\([^\n]*\)|pdx\.init_token\([^\n]*\))",
            "",
            text,
        )
        for pattern in SECRET_PATTERNS:
            if pattern.search(scan_text):
                errors.append(f"possible credential in {path.relative_to(ROOT)}")
                break


def check_python_syntax(errors: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "src", "skills"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        errors.append(f"python syntax check failed: {result.stderr.strip() or result.stdout.strip()}")


def main() -> int:
    errors = []
    agent = frontmatter(ROOT / "AGENTS.md")
    qs = agent.get("quantSkills", {})
    expected = {
        "schema_version": "2.0.0",
        "organization": "quantskills",
        "repository": "agent-market-style-adaptive",
        "project_type": "agent",
        "license": "GPL-3.0-only",
        "status": "active",
        "validation_level": "runnable",
    }
    for key, value in expected.items():
        if qs.get(key) != value:
            errors.append(f"AGENTS.md quantSkills.{key} must equal {value!r}")
    if qs.get("organization_url") != "https://github.com/quantskills":
        errors.append("AGENTS.md quantSkills.organization_url must point to quantskills")
    if qs.get("repository_url") != "https://github.com/quantskills/agent-market-style-adaptive":
        errors.append("AGENTS.md quantSkills.repository_url is invalid")
    catalog = qs.get("catalog") or {}
    if catalog.get("category") != "10" or catalog.get("subcategory") != "10.market-style-adaptive":
        errors.append("AGENTS.md quantSkills.catalog must be 10/10.market-style-adaptive")
    workflow = qs.get("workflow") or {}
    stages = workflow.get("workflow_stages") or []
    if workflow.get("primary_stage") != "orchestration":
        errors.append("AGENTS.md quantSkills.workflow.primary_stage must be orchestration")
    unknown_stages = sorted(set(stages) - WORKFLOW_STAGES)
    if unknown_stages:
        errors.append(f"AGENTS.md quantSkills.workflow has unknown stages: {unknown_stages}")
    requires = qs.get("requires") or []
    if any(not isinstance(item, str) or not item.startswith(("skill-", "agent-")) for item in requires):
        errors.append("AGENTS.md quantSkills.requires entries must start with skill- or agent-")
    if set(qs.get("platforms") or []) != PLATFORMS:
        errors.append("AGENTS.md quantSkills.platforms must list cursor, claude-code, codex, hermes, openclaw")
    for field, low, high in (("summary_zh", 8, 120), ("summary_en", 8, 200)):
        value = qs.get(field, "")
        if not isinstance(value, str) or not (low <= len(value) <= high):
            errors.append(f"AGENTS.md quantSkills.{field} must be {low}-{high} characters")
    interface = qs.get("interface") or {}
    if interface.get("mode") != "cli" or interface.get("command") != "market-style-agent":
        errors.append("AGENTS.md quantSkills.interface must declare market-style-agent CLI")
    for path in sorted((ROOT / "skills").glob("*/SKILL.md")):
        data = frontmatter(path)
        if not data.get("name") or not data.get("description"):
            errors.append(f"{path}: name and description are required")
        if path.parent.name != f"skill-{data.get('name')}":
            errors.append(f"{path}: folder must be skill-{data.get('name')}")
        if data.get("license") != "GPL-3.0-only":
            errors.append(f"{path}: license must be GPL-3.0-only")
    check_required_files(errors)
    check_path_references(errors)
    check_secrets(errors)
    check_python_syntax(errors)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("package validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
