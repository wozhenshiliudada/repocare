from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .demo import run_demo
from .models import Policy, SafetyError
from .provider import MockProvider
from .pi_provider import PiProvider, SavedPlanProvider
from .workflow import cleanup_run, plan_repair, repair
from .runtime_limits import MAX_REQUESTS
from .maintenance_limits import LIMITS


def load_policy(path: Path) -> Policy:
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data.get("allowed_paths"), list)
            or not all(isinstance(p, str) for p in data["allowed_paths"])
            or not isinstance(data.get("allowed_tests"), list)
            or not isinstance(data.get("readonly_paths", []), list)
            or not all(isinstance(p, str) for p in data.get("readonly_paths", []))
            or not all(isinstance(cmd, list) and all(isinstance(arg, str) for arg in cmd)
                       for cmd in data["allowed_tests"])):
        raise SafetyError("Policy requires arrays of exact paths and test argv arrays")
    return Policy(tuple(data["allowed_paths"]), tuple(tuple(cmd) for cmd in data["allowed_tests"]),
                  float(data.get("timeout_seconds", 30)), tuple(data.get("readonly_paths", [])))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pi-based isolated repository maintenance")
    sub = parser.add_subparsers(dest="command", required=True)
    from .gateway import configure_parser as configure_gateway_parser
    configure_gateway_parser(sub)
    mcp = sub.add_parser('mcp',help='Expose scoped RepoCare tools over local stdio MCP')
    mcp.add_argument('--home',type=Path)
    mcp.add_argument('--allow-plan',action='store_true',help='Allow new proposals through an already authorized workspace; no approval or publishing tools')
    setup = sub.add_parser('setup-check', help='本地安装与明确模型配置自检；不联网、不调用模型')
    setup.add_argument('--provider-config', type=Path,
                       help='只检查明确指定的配置文件；不查找其他项目或环境密钥')
    setup.add_argument('--json', action='store_true', help='输出脱敏的结构化本地检查结果')
    runtime = sub.add_parser('runtime', help='Pi 运行时准备；默认只诊断，不自动安装')
    runtime_actions = runtime.add_subparsers(dest='runtime_action', required=True)
    runtime_install = runtime_actions.add_parser(
        'install', help='查看/执行锁定 Pi 依赖安装；执行需 --approve 确认码')
    runtime_install.add_argument('--approve', metavar='SHA256',
                                 help='仅执行与准备输出完全一致的确认码')
    runtime_install.add_argument('--json', action='store_true')
    for name in ("structure-build", "structure-verify", "structure-query"):
        structure = sub.add_parser(name, help="Explicit read-only CodeGraph structure prototype; no model")
        structure.add_argument("--repo", type=Path, required=True)
        if name == "structure-build":
            structure.add_argument("--spec", type=Path, required=True)
            structure.add_argument("--output", type=Path, required=True, help="New directory outside Git/runtime archives")
        else:
            structure.add_argument("--index-dir", type=Path, required=True)
        if name == "structure-query":
            structure.add_argument("--query", required=True)
            structure.add_argument("--limit", type=int, choices=range(1, 6), default=5)
    for name in ("plan", "repair"):
        command = sub.add_parser(name)
        command.add_argument("--repo", type=Path, required=True)
        command.add_argument("--issue", required=True)
        command.add_argument("--policy", type=Path, required=True)
        command.add_argument("--provider", choices=("pi", "mock"), default="pi")
        command.add_argument("--pi-mock-url", help="Local Pi replay endpoint (HTTP 127.0.0.1 only)")
        command.add_argument("--credential-source", choices=("environment", "openclaw-readonly"), default="environment",
                             help="Explicit opt-in to existing local DeepSeek credentials")
        command.add_argument("--provider-config", type=Path, help="Explicit local key/endpoint/model file; never auto-discovered")
        command.add_argument("--request-budget", type=int, choices=range(1, MAX_REQUESTS + 1), default=MAX_REQUESTS)
        command.add_argument("--memory-root", type=Path, help="Explicit corpus with memory-catalog.json")
        command.add_argument("--memory-index", type=Path, help="Explicit verified .memory.sqlite3 index")
        command.add_argument('--preflight-runs-dir', type=Path,
                             help='Opt-in separate test-only approval before fresh CLI Pi planning; new external run directory')
        command.add_argument('--approve-baseline', help='Exact SHA256 of the reviewed test-only scope; does not approve edits')
        command.add_argument("--scenario", choices=("fix", "broken", "regression"), default="fix")
        if name == "repair":
            command.add_argument("--mode", choices=("approval", "auto"), default="approval")
            command.add_argument("--runs-dir", type=Path, required=True)
            decisions = command.add_mutually_exclusive_group()
            decisions.add_argument("--approve-plan", help="Full SHA-256 from the reviewed plan")
            decisions.add_argument("--reject", action="store_true")
            command.add_argument("--actor", default="local-cli-operator")
            command.add_argument("--plan-file", type=Path, help="Resume an exact reviewed JSON plan")
            command.add_argument("--max-attempts", type=int, choices=range(1,LIMITS['candidate_max_rounds']+1), default=3)
    demo = sub.add_parser("demo")
    demo.add_argument("--directory", type=Path, required=True)
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--run-dir", type=Path, required=True)
    for name in ("memory-build", "memory-search"):
        command = sub.add_parser(name, help="Source-pinned local memory; no runtime authority")
        command.add_argument("--source-root", type=Path, required=True)
        command.add_argument("--repo", type=Path, required=True)
        command.add_argument("--index", type=Path, required=True)
        if name == "memory-search":
            command.add_argument("--query", required=True)
    case = sub.add_parser("memory-export-report", help="Export one explicit historical report range to NEW corpus and index")
    case.add_argument("--run-dir", type=Path, required=True)
    case.add_argument("--source-root", type=Path, required=True)
    case.add_argument("--repo", type=Path, required=True)
    case.add_argument("--index", type=Path, required=True)
    case.add_argument("--case-id", required=True)
    case.add_argument("--topic", required=True)
    case.add_argument("--start-line", type=int, required=True)
    case.add_argument("--end-line", type=int, required=True)
    collection = sub.add_parser("memory-compose", help="Compose explicit document/report/migrated corpora into NEW pinned collection")
    collection.add_argument("--selection", type=Path, required=True)
    collection.add_argument("--source-root", type=Path, required=True)
    collection.add_argument("--repo", type=Path, required=True)
    collection.add_argument("--index", type=Path, required=True)
    migration_preview=sub.add_parser('memory-migration-preview',help='Preview selected archive materials without restoring runtime rights')
    migration_preview.add_argument('--source-kind',choices=('ui_backup','legacy_archive'),required=True)
    migration_preview.add_argument('--archive-root',type=Path,required=True)
    migration_preview.add_argument('--archive-id',required=True)
    migration=sub.add_parser('memory-migrate',help='Export explicit archive selections into historical corpus and target-bound index')
    migration.add_argument('--selection',type=Path,required=True)
    migration.add_argument('--source-root',type=Path,required=True)
    migration.add_argument('--repo',type=Path,required=True)
    migration.add_argument('--index',type=Path,required=True)
    retrieval_eval=sub.add_parser('memory-evaluate-retrieval',help='Offline fixed-fixture ranking evaluation; no model or live journal access')
    retrieval_eval.add_argument('--suite',type=Path,required=True)
    retrieval_eval.add_argument('--output',type=Path,required=True)
    ablation=sub.add_parser('memory-evaluate-ablation',help='Four memory configurations on NEW authored fixtures; synthetic loopback Pi only')
    ablation.add_argument('--output',type=Path,required=True)
    ablation.add_argument('--aggregate-request-ceiling',type=int,choices=range(1,101),default=100)
    long_ablation=sub.add_parser('memory-evaluate-long-context',help='Legacy summary versus native compaction/maintenance on NEW synthetic fixtures only')
    long_ablation.add_argument('--output',type=Path,required=True)
    long_ablation.add_argument('--aggregate-request-ceiling',type=int,choices=range(1,101),default=100)
    for name in ("memory-relations-register", "memory-relations-verify", "memory-relations-preview"):
        command = sub.add_parser(name, help="Explicit replacement evidence; never change active policy or approvals")
        command.add_argument("--source-root", type=Path, required=True)
        command.add_argument("--repo", type=Path, required=True)
        command.add_argument("--index", type=Path, required=True)
        if name == "memory-relations-register":
            command.add_argument("--spec", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
        else:
            command.add_argument("--relations", type=Path, required=True)
        if name == "memory-relations-preview":
            command.add_argument("--query", required=True)
            command.add_argument("--limit", type=int, choices=range(1, 6), default=5)
    legacy = sub.add_parser("import-legacy-run", help="Import one legacy evidence archive without runtime rights")
    legacy.add_argument("--run-dir", type=Path, required=True)
    legacy.add_argument("--archive-root", type=Path, required=True)
    legacy_verify = sub.add_parser("verify-legacy-run", help="Read-only archive verification and optional explicit source comparison")
    legacy_verify.add_argument("--archive-root", type=Path, required=True)
    legacy_verify.add_argument("--archive-id", required=True)
    legacy_verify.add_argument("--source-run-dir", type=Path)
    for name in ("backup-ui-history", "verify-ui-backup"):
        command = sub.add_parser(name, help="Archive/verify offline UI history; never restore or reset budgets")
        command.add_argument("--runs-dir", type=Path, required=True)
        if name == "verify-ui-backup":
            command.add_argument("--backup-id", required=True)
    dashboard = sub.add_parser("dashboard", help="Export an offline read-only run evidence dashboard")
    dashboard.add_argument("--input", type=Path, action="append", required=True,
                           help="Run directory or direct runs collection; repeat for multiple inputs")
    dashboard.add_argument("--output", type=Path, required=True, help="New HTML file outside inputs and source repos")
    prepare = sub.add_parser("prepare-integration", help="Seal and test an immutable candidate; does not publish a branch")
    prepare.add_argument("--run-dir", type=Path, required=True)
    prepare.add_argument("--retry-aborted", action="store_true", help="Archive an explicitly recovered attempt and rerun all tests")
    recover = sub.add_parser("recover-integration", help="Abort an interrupted preparation after ownership/process checks")
    recover.add_argument("--run-dir", type=Path, required=True)
    inspect_repair = sub.add_parser("inspect-repair", help="Inspect unfinished repair ownership/processes without replay")
    inspect_repair.add_argument("--run-dir", type=Path, required=True)
    abort_repair = sub.add_parser("recover-repair", help="Explicitly abort a checked unfinished repair")
    abort_repair.add_argument("--run-dir", type=Path, required=True)
    abort_repair.add_argument("--approve-abort", required=True, help="Full SHA-256 from a fresh inspect-repair preview")
    abort_repair.add_argument("--actor", default="local-operator")
    integration = sub.add_parser("integrate", help="Explicitly approve publication to a new run-owned integration branch")
    integration.add_argument("--run-dir", type=Path, required=True)
    integration.add_argument("--actor", default="local-integration-operator")
    integration_decisions = integration.add_mutually_exclusive_group()
    integration_decisions.add_argument("--approve-integration", help="Full reviewed SHA-256; edit approval is not enough")
    integration_decisions.add_argument("--reject", action="store_true")
    evaluation = sub.add_parser("evaluate", help="Run five fixture-oracle workflow cases")
    evaluation.add_argument("--directory", type=Path, required=True)
    for name in ("compare", "showcase"):
        command = sub.add_parser(name, help="Controlled comparison on new self-authored fixtures; no live model")
        command.add_argument("--directory", type=Path, required=True)
        command.add_argument("--pi-mock-url", help="Explicit synthetic HTTP 127.0.0.1:PORT/v1 only")
    live = sub.add_parser("validate-deepseek", help="At most six live model requests, self-authored fixtures only")
    live.add_argument("--directory", type=Path, required=True)
    live.add_argument("--credential-source", choices=("environment", "openclaw-readonly"), default="environment")
    mimo = sub.add_parser("validate-mimo", help="One self-authored fixture, at most three live model requests")
    mimo.add_argument("--directory", type=Path, required=True)
    mimo.add_argument("--provider-config", type=Path, required=True)
    comp_summary = sub.add_parser("comparison-summary", help="Read-only view of controlled workflow comparison summary")
    comp_summary.add_argument("--input", type=Path, required=True, help="Path to comparison summary.json")
    comp_summary.add_argument("--json", action="store_true", help="Output allowlisted JSON only")
    web_ui = sub.add_parser("serve", help="Loopback browser UI; no merge/push endpoints")
    web_ui.add_argument("--demo", type=Path, help="Create a NEW self-authored demo directory; no model requests")
    web_ui.add_argument("--resume-demo", type=Path, help="Reopen an existing journaled demo; historical actions require explicit claim")
    web_ui.add_argument("--repo", type=Path)
    web_ui.add_argument("--policy", type=Path)
    web_ui.add_argument("--runs-dir", type=Path)
    web_ui.add_argument("--pi-mock-url", help="Explicit loopback Pi replay endpoint")
    web_ui.add_argument("--provider-config", type=Path, help="Explicit local provider file; never auto-discovered")
    web_ui.add_argument('--semantic-model-manifest',type=Path,help='Register pinned local encoder data; remains off until explicitly enabled in UI')
    web_ui.add_argument("--memory-root", type=Path, help="Explicit memory source root, fixed for this UI journal")
    web_ui.add_argument("--memory-index", type=Path, help="Explicit source-pinned memory index")
    web_ui.add_argument("--memory-relations", type=Path, help="Explicit read-only decision relations, pinned to this journal")
    web_ui.add_argument("--memory-relations-context", action="store_true", help="Explicitly include verified claims in Pi context; fixed to this journal")
    web_ui.add_argument('--structure-index', type=Path, help='Explicit verified structure directory; model projection requires --structure-context-query')
    web_ui.add_argument('--structure-context-query', help='Explicitly give Pi bounded structure metadata for this fixed literal query; requires structure-index')
    web_ui.add_argument("--port", type=int, default=0)
    web_ui.add_argument("--allow-auto", action="store_true", help="Enable scoped autonomous repair, not publication")
    web_ui.add_argument("--max-attempts", type=int, choices=range(1,LIMITS['candidate_max_rounds']+1), default=1)
    budget_options = web_ui.add_mutually_exclusive_group()
    budget_options.add_argument("--request-budget", type=int, choices=range(1, MAX_REQUESTS + 1), default=MAX_REQUESTS)
    budget_options.add_argument('--resume-budget', action='store_true', help='Use the existing persisted ceiling, never reset or expand it')
    workbench = sub.add_parser("workbench", aliases=["web"], help="Open the web workbench; configure API credentials in Settings")
    workbench.add_argument("--workspace-root", type=Path, default=Path.home() / '.repocare' / 'workbench', help="Private state outside target repositories (default: ~/.repocare/workbench)")
    workbench.add_argument("--provider-config", type=Path, help="Optional explicit provider file; omit to configure in the browser")
    workbench.add_argument('--semantic-model-manifest',type=Path,help='Register pinned local encoder data; each repository defaults off')
    workbench.add_argument("--request-budget", type=int, choices=range(1, MAX_REQUESTS + 1), default=MAX_REQUESTS)
    workbench.add_argument("--allow-auto", action="store_true")
    workbench.add_argument("--port", type=int, default=0)
    workbench.add_argument('--ui',choices=['legacy','react'],default='react',help='Workbench shell; build frontend first, or select legacy')
    workbench.add_argument('--github-push-config', type=Path,
                           help='Explicit local receiver config for one existing GitHub monitor; no public deployment')
    workbench.add_argument('--ci-log-host', action='append', default=[],
                           help='Trusted exact CI log download host; repeat for each observed host (default: disabled)')
    args = parser.parse_args(argv)
    try:
        if hasattr(args, 'gateway_handler'):
            return args.gateway_handler(args)
        if args.command == 'mcp':
            from .mcp_server import main as mcp_main
            options=(['--home',str(args.home)] if args.home else [])+(['--allow-plan'] if args.allow_plan else [])
            return mcp_main(options)
        if args.command == 'setup-check':
            from .service_readiness import ServiceReadiness
            report = ServiceReadiness(args.provider_config.absolute() if args.provider_config else None).inspect()
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                for row in report['checks']:
                    print(f"[{row['status']}] {row['label']}：{row['detail']}")
                    if row['next_action']:
                        print('  下一步：' + row['next_action'])
                print('模型请求尚未验证；此命令不会联网或产生模型调用。')
            return 0 if report['status'] == 'ready' else 1
        if args.command == 'runtime':
            from .pi_distribution import runtime_command
            return runtime_command(args)
        if args.command in {"workbench", "web"}:
            from .repository_workbench import RepositoryWorkbench, serve_workbench
            application = RepositoryWorkbench(args.workspace_root, args.provider_config,
                            request_budget=args.request_budget, allow_auto=args.allow_auto,
                            semantic_model_manifest=args.semantic_model_manifest,ui=args.ui,
                            ci_download_hosts=tuple(args.ci_log_host))
            try:
                if args.github_push_config is not None:
                    push = application.configure_github_push(args.github_push_config)
                    print('GitHub push receiver: ' + push['local_url'], flush=True)
                serve_workbench(application, args.port)
            finally:
                application.close()
            return 0
        if args.command.startswith("structure-"):
            from .structure_index import build_structure, verify_structure, query_structure
            if args.command == "structure-build":
                result = build_structure(args.spec,args.repo,args.output)
            elif args.command == "structure-verify":
                result = verify_structure(args.index_dir,args.repo)
            else:
                result = query_structure(args.index_dir,args.repo,args.query,args.limit)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command.startswith("memory-relations-"):
            from .decision_relations import register_relations, verify_relations, preview_relations
            params = (args.source_root, args.repo, args.index)
            if args.command == "memory-relations-register":
                result = register_relations(args.spec, *params, args.output)
            elif args.command == "memory-relations-verify":
                result = verify_relations(args.relations, *params)
            else:
                result = preview_relations(args.relations, *params, args.query, args.limit)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command=='memory-evaluate-retrieval':
            from .retrieval_evaluation import evaluate_file
            print(json.dumps(evaluate_file(args.suite,args.output),indent=2,ensure_ascii=False))
            return 0
        if args.command=='memory-evaluate-long-context':
            from .memory_long_ablation import run_long_ablation
            result=run_long_ablation(args.output,args.aggregate_request_ceiling,
                                    progress=lambda row:print(json.dumps(row),flush=True))
            print(json.dumps({'passed':result['passed'],'cells':len(result['cells']),
                              'aggregate_requests':result['aggregate_received'],'paid_requests':0,
                              'model_benefit_claim':False,'summary':str(args.output/'summary.json')},indent=2))
            return 0 if result['passed'] else 1
        if args.command=='memory-evaluate-ablation':
            from .memory_ablation import run_ablation
            result=run_ablation(args.output,args.aggregate_request_ceiling,
                                progress=lambda row:print(json.dumps(row),flush=True))
            print(json.dumps({'passed':result['passed'],'cells':len(result['cells']),
                              'aggregate_requests':result['aggregate_received'],'paid_requests':0,
                              'model_benefit_claim':False,'summary':str(args.output/'summary.json')},indent=2))
            return 0 if result['passed'] else 1
        if args.command in {'memory-migration-preview','memory-migrate'}:
            from .memory_migration import preview_migration,migrate_memory
            result=(preview_migration(args.source_kind,args.archive_root,args.archive_id) if args.command=='memory-migration-preview'
                    else migrate_memory(args.selection,args.source_root,args.repo,args.index))
            print(json.dumps(result,indent=2,ensure_ascii=False))
            return 0
        if args.command == "memory-compose":
            from .memory_collection import compose_memory
            result = compose_memory(args.selection, args.source_root, args.repo, args.index)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command == "memory-export-report":
            from .report_cases import export_report_case
            result = export_report_case(args.run_dir, args.source_root, args.repo, args.index,
                                        args.case_id, args.topic, args.start_line, args.end_line)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command in {"memory-build", "memory-search"}:
            from .source_memory import build_memory, search_memory
            result = (build_memory(args.source_root,args.repo,args.index) if args.command == "memory-build"
                      else search_memory(args.source_root,args.repo,args.index,args.query))
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command in {"import-legacy-run", "verify-legacy-run"}:
            from .legacy_archive import import_legacy_run, verify_legacy_run
            result = (import_legacy_run(args.run_dir, args.archive_root) if args.command == "import-legacy-run"
                      else verify_legacy_run(args.archive_root, args.archive_id, args.source_run_dir))
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command in {"backup-ui-history", "verify-ui-backup"}:
            from .ui_backup import backup_ui_history, verify_ui_backup
            result = (backup_ui_history(args.runs_dir) if args.command == "backup-ui-history"
                      else verify_ui_backup(args.runs_dir, args.backup_id))
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command == "serve":
            from .local_ui import UISession, serve
            if args.structure_context_query is not None:
                from .structure_index import text
                if args.structure_index is None or not text(args.structure_context_query, 128):
                    raise SafetyError('Structure model context requires an explicit index and bounded query')
            if bool(args.memory_root) != bool(args.memory_index):
                raise SafetyError("Explicit UI memory root and index must be supplied together")
            if args.memory_relations and not args.memory_root:
                raise SafetyError("UI decision relations require explicit memory root and index")
            if args.memory_relations_context and not args.memory_relations:
                raise SafetyError("Pi relation context requires explicit decision relations")
            from .demo import DEMO_TEST, create_fixture
            if args.demo or args.resume_demo:
                if args.demo and args.resume_demo:
                    raise SafetyError("Choose a new demo or an existing demo, not both")
                if any((args.repo, args.policy, args.runs_dir, args.pi_mock_url, args.provider_config,
                        args.memory_root, args.memory_index, args.memory_relations, args.memory_relations_context,
                        args.structure_index, args.structure_context_query, args.resume_budget)):
                    raise SafetyError("Demo cannot be combined with source/provider configuration")
                directory = (args.demo or args.resume_demo).resolve()
                if args.demo:
                    if directory.exists():
                        raise SafetyError("Demo destination exists; choose a new directory")
                    repo = create_fixture(directory / "repository")
                else:
                    if not (directory / "runs" / "ui-state" / "registry.sqlite3").is_file():
                        raise SafetyError("Existing demo journal is required; preserve old unjournaled evidence")
                    repo = directory / "repository"
                policy = Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",))
                provider, runs_dir, label = MockProvider(), directory / "runs", "固定样例（不调用模型）"
            else:
                if (not all((args.repo, args.policy, args.runs_dir))
                        or bool(args.pi_mock_url) == bool(args.provider_config)):
                    raise SafetyError("Explicit repo/policy/runs-dir and exactly one mock URL or provider file required")
                repo, policy, runs_dir = args.repo, load_policy(args.policy), args.runs_dir
                if args.resume_budget:
                    from .service_state import saved_budget
                    args.request_budget = saved_budget(runs_dir)
                provider = PiProvider(policy.allowed_paths, args.pi_mock_url, policy.readonly_paths,
                                      args.request_budget)
                provider.config_file = args.provider_config
                provider.memory_root, provider.memory_index = args.memory_root, args.memory_index
                provider.memory_relations = args.memory_relations
                provider.memory_relations_context = args.memory_relations_context
                label = "Pi 本机合成回放" if args.pi_mock_url else "Pi 显式模型配置（可能产生费用）"
            serve(UISession(repo, policy, provider, runs_dir, allow_auto=args.allow_auto,
                            max_attempts=args.max_attempts, label=label, structure_index=args.structure_index,
                            structure_context_query=args.structure_context_query,
                            semantic_model_manifest=args.semantic_model_manifest), args.port)
            return 0
        if args.command == "comparison-summary":
            from .comparison_summary import load_comparison_summary, format_comparison_summary
            summary = load_comparison_summary(args.input)
            if args.json:
                print(json.dumps(summary, indent=2, ensure_ascii=False))
            else:
                print(format_comparison_summary(summary))
            return 0
        if args.command in {"compare", "showcase"}:
            from .comparison import run_comparison
            result = run_comparison(args.directory, args.pi_mock_url, args.command == "showcase")
            print(json.dumps({k: v for k, v in result.items() if k != "cells"}, indent=2, ensure_ascii=False))
            return 0 if result["passed"] else 1
        if args.command == "dashboard":
            from .dashboard import export_dashboard
            print(json.dumps(export_dashboard(args.input, args.output), indent=2, ensure_ascii=False))
            return 0
        if args.command in {"inspect-repair", "recover-repair"}:
            from .repair_recovery import inspect_repair, recover_repair
            if args.command == "inspect-repair":
                preview = inspect_repair(args.run_dir)
                print(json.dumps(preview, indent=2, ensure_ascii=False))
                return 0 if preview["can_abort"] or preview["status"] == "aborted" else 2
            record = recover_repair(args.run_dir, args.approve_abort, args.actor)
            print(json.dumps({"run_id":record["run_id"], "status":record["status"],
                              "source_unchanged":record["source_unchanged"], "report":str(args.run_dir / "report.md")}, indent=2))
            return 0
        if args.command in {"prepare-integration", "recover-integration", "integrate"}:
            from .integration import prepare_integration, recover_integration, integrate
            if args.command == "prepare-integration":
                review = prepare_integration(args.run_dir, args.retry_aborted)
            elif args.command == "recover-integration":
                review = recover_integration(args.run_dir)
            else:
                review = integrate(args.run_dir, args.approve_integration, args.reject, args.actor)
            print(json.dumps({"status": review["status"], "review_sha256": review.get("review_sha256"),
                              "target_branch": review.get("review", {}).get("target_branch"),
                              "commit": review.get("review", {}).get("commit"),
                              "review": str(args.run_dir / "integration-review.md")}, indent=2))
            return 2 if review["status"] == "awaiting_approval" else (0 if review["status"] in
                        {"prepared", "published", "rejected", "aborted"} else 1)
        if args.command == "validate-mimo":
            from .live_validation import run_mimo_validation
            summary = run_mimo_validation(args.directory, args.provider_config)
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            return 0 if summary["passed"] else 1
        if args.command == "demo":
            summary = run_demo(args.directory)
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            return 0 if summary["passed"] else 1
        if args.command == "evaluate":
            from .evaluation import run_evaluation
            summary = run_evaluation(args.directory)
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            return 0 if summary["passed"] == summary["total"] else 1
        if args.command == "validate-deepseek":
            from .live_validation import run_deepseek_validation
            summary = run_deepseek_validation(args.directory, args.credential_source)
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            return 0 if summary["total_cases"] and summary["passed_cases"] == summary["total_cases"] else 1
        if args.command == "cleanup":
            record = cleanup_run(args.run_dir)
        else:
            policy = load_policy(args.policy)
            if (bool(args.memory_root) != bool(args.memory_index) or ((args.memory_root or args.memory_index)
                    and (args.provider != "pi" or (args.command == "repair" and args.plan_file)))):
                raise SafetyError("Memory requires explicit root/index and fresh Pi planning")
            if args.pi_mock_url and args.provider != "pi":
                raise SafetyError("--pi-mock-url requires the Pi provider")
            if args.provider_config and (args.provider != "pi" or args.pi_mock_url
                                         or args.credential_source != "environment" or
                                         (args.command == "repair" and args.plan_file)):
                raise SafetyError("--provider-config requires live Pi without other credential sources or saved plans")
            provider = (PiProvider(policy.allowed_paths, args.pi_mock_url, policy.readonly_paths, args.request_budget) if args.provider == "pi"
                        else MockProvider(args.scenario))
            if args.provider == "pi":
                provider.credential_source = args.credential_source
                provider.config_file = args.provider_config
                provider.memory_root, provider.memory_index = args.memory_root, args.memory_index
            if args.command == "repair" and args.plan_file:
                provider = SavedPlanProvider(args.plan_file)
            if args.command == 'repair' and args.mode == 'auto' and (args.approve_plan or args.reject):
                raise SafetyError('Approval options require approval mode')
            if args.approve_baseline and not args.preflight_runs_dir:
                raise SafetyError('--approve-baseline requires --preflight-runs-dir')
            if args.preflight_runs_dir:
                if args.provider != 'pi' or (args.command == 'repair' and (args.plan_file or args.reject)):
                    raise SafetyError('Preflight requires fresh CLI Pi planning, not saved/rejected plans')
                from .baseline_probe import collect_baseline, BaselineEvidence
                from .git import RepositoryGateway
                from .task_session import fingerprint
                def approve_baseline(scope):
                    scope_sha = fingerprint(scope)
                    print(json.dumps({'test_only_scope': scope, 'baseline_scope_sha256': scope_sha,
                                      'edit_approval': False}, ensure_ascii=False, indent=2))
                    if args.approve_baseline:
                        if args.approve_baseline != scope_sha:
                            raise SafetyError('Baseline approval hash is stale or does not match scope')
                        return 'approve'
                    if not sys.stdin.isatty():
                        return 'pending'
                    answer = input(f"Type 'test {scope_sha}' to run only these baseline commands; "
                                   "type 'reject' to refuse: ").strip()
                    return 'approve' if answer == 'test ' + scope_sha else 'reject' if answer == 'reject' else 'pending'
                probe = collect_baseline(args.repo, args.issue, policy, args.preflight_runs_dir,
                                         approve_baseline, getattr(args, 'actor', 'local-cli-operator'))
                directory = Path(probe['workspace']).parent
                print(json.dumps({'baseline_probe': probe['run_id'], 'status': probe['status'],
                                  'source_unchanged': probe['source_unchanged'], 'report': str(directory / 'report.md'),
                                  'planning_started': False}, ensure_ascii=False, indent=2))
                if probe['status'] != 'baseline_ready':
                    return 2 if probe['status'] == 'awaiting_baseline_approval' else 0 if probe['status'] in {
                        'baseline_passes', 'baseline_rejected'} else 1
                provider.baseline_context = BaselineEvidence(directory, RepositoryGateway(args.repo), args.issue, policy)
            if args.command == "plan":
                plan = plan_repair(args.repo, args.issue, provider, policy)
                print(json.dumps({"plan": asdict(plan), "plan_sha256": plan.fingerprint,
                                  "provider_evidence": getattr(provider, "evidence", {})},
                                 indent=2, ensure_ascii=False))
                return 0
            def approval(plan):
                print(json.dumps({"plan": asdict(plan), "plan_sha256": plan.fingerprint},
                                 indent=2, ensure_ascii=False))
                if args.reject:
                    return "reject"
                if args.approve_plan:
                    if args.approve_plan != plan.fingerprint:
                        raise SafetyError("Approval hash is stale or does not match this plan")
                    return "approve"
                if not sys.stdin.isatty():
                    return "pending"
                answer = input(f"Type 'approve {plan.fingerprint}' to allow candidate edits; "
                               "type 'reject' to refuse: ").strip()
                if answer == "approve " + plan.fingerprint:
                    return "approve"
                return "reject" if answer == "reject" else "pending"

            record = repair(args.repo, args.issue, provider, policy, args.mode, args.runs_dir,
                            approval=approval, actor=args.actor, max_attempts=args.max_attempts)
        print(json.dumps({"run_id": record["run_id"], "status": record["status"],
                          "source_unchanged": record["source_unchanged"],
                          "report": str(Path(record["workspace"]).parent / "report.md")},
                         indent=2, ensure_ascii=False))
        if record["status"] in {"candidate_ready", "rejected", "discarded"}:
            return 0
        return 2 if record["status"] == "awaiting_approval" else 1
    except (SafetyError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
