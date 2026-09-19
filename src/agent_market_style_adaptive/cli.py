from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

import yaml

from .backtest import Backtester
from .config import config_hash, load_config
from .data import DataContractError, materialize_raw_artifacts, require_fresh_sync, run_mcp_command, validate_cache, validate_manifest, write_request_plan
from .reporting import build_report, compare_runs


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="market-style-agent")
    root.add_argument("--config", default="config/default.yaml")
    commands = root.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("data-sync", help="plan MCP requests or validate materialized Pandadata artifacts")
    sync.add_argument("--validate-only", action="store_true")
    sync.add_argument("--execute", action="store_true", help="invoke the configured MCP host bridge")
    sync.add_argument("--raw-manifest", help="materialize locally downloaded MCP artifacts from this manifest")
    backtest = commands.add_parser("backtest")
    backtest.add_argument("--mode", choices=["adaptive", "independent"], required=True)
    backtest.add_argument("--skip-sensitivity", action="store_true")
    commands.add_parser("compare")
    commands.add_parser("report")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = load_config(args.config)
    output = Path(config["data"]["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    if args.command == "data-sync":
        plan = write_request_plan(config, output / "data_sync" / "mcp_requests.json")
        raw_manifest = None
        if args.execute:
            try:
                raw_manifest = run_mcp_command(config, plan)
            except DataContractError as exc:
                raise SystemExit(str(exc)) from exc
        elif args.raw_manifest:
            raw_manifest = Path(args.raw_manifest)
        if raw_manifest:
            try:
                manifest = materialize_raw_artifacts(config, plan, raw_manifest)
                print(manifest)
            except DataContractError as exc:
                raise SystemExit(str(exc)) from exc
        findings = validate_cache(config["data"]["cache_dir"], config["data"]["required_tables"])
        if args.validate_only:
            try:
                validate_manifest(Path(config["data"]["cache_dir"]) / "manifest.json", config["data"]["cache_dir"])
                findings["manifest_ok"] = True
            except (DataContractError, json.JSONDecodeError) as exc:
                findings["manifest_ok"] = False
                findings["manifest_error"] = str(exc)
                findings["ok"] = False
        findings_path = output / "data_sync" / "data_dictionary.json"
        findings_path.write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
        print(findings_path)
        return 0 if findings["ok"] or not args.validate_only else 2
    if args.command == "backtest":
        try:
            require_fresh_sync(config)
        except DataContractError as exc:
            raise SystemExit(str(exc)) from exc
        findings = validate_cache(config["data"]["cache_dir"], config["data"]["required_tables"])
        if not findings["ok"]:
            raise SystemExit("data cache does not satisfy the contract; run data-sync --validate-only")
        engine = Backtester.from_cache(config)
        if args.mode == "independent":
            for sleeve in ("small_cap", "etf", "blue_chip"):
                sleeve_config = deepcopy(config)
                sleeve_config["run"]["active_sleeves"] = [sleeve]
                sleeve_config["allocation"] = {
                    regime: {
                        "small_cap": 0.0,
                        "etf": 0.0,
                        "blue_chip": 0.0,
                        "cash": 0.0,
                        sleeve: 1.0,
                    }
                    for regime in sleeve_config["allocation"]
                }
                sleeve_config["risk"]["warning_drawdown"] = 2.0
                sleeve_config["risk"]["crisis_drawdown"] = 3.0
                sleeve_config["sleeve_risk_budget"] = {"method": "absolute_target_vol"}
                engine.config = sleeve_config
                result = engine.run("adaptive")
                result.mode = f"independent/{sleeve}"
                destination = result.write(output)
                (destination / "config.snapshot.yaml").write_text(
                    yaml.safe_dump(sleeve_config, allow_unicode=True, sort_keys=False), encoding="utf-8"
                )
                print(f"{destination} config_hash={config_hash(sleeve_config)}")
            return 0
        result = engine.run(args.mode)
        destination = result.write(output)
        (destination / "config.snapshot.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        if not args.skip_sensitivity:
            for multiplier in (0.5, 2.0):
                sensitivity = engine.run(args.mode, cost_multiplier=multiplier)
                sensitivity.mode = f"sensitivity/{args.mode}_cost_{multiplier:g}x"
                sensitivity.write(output)
        print(f"{destination} config_hash={config_hash(config)}")
        return 0
    if args.command == "compare":
        try:
            require_fresh_sync(config)
        except DataContractError as exc:
            raise SystemExit(str(exc)) from exc
        comparison = compare_runs(output, int(config["run"]["annualization_days"]))
        print(comparison.to_string(index=False))
        return 0
    if args.command == "report":
        try:
            require_fresh_sync(config)
        except DataContractError as exc:
            raise SystemExit(str(exc)) from exc
        print(build_report(output, int(config["run"]["annualization_days"])))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


