"""Thin, authenticated workbench adapters for existing project and CLI evidence."""
import json
import os
import re
from pathlib import Path

from .dashboard import export_dashboard, unlinked
from .models import SafetyError, digest

ARTIFACTS = {'dashboard.html': 'text/html', 'comparison.json': 'application/json',
             'comparison.md': 'text/markdown', 'tool-result.json': 'application/json', 'tool-report.md': 'text/markdown'}
MAX_DOWNLOAD = 32 * 1024**2


def saved_projects(root, page):
    directory = unlinked(root / 'projects')
    if not directory.exists():
        return {'projects': [], 'page': page, 'has_next': False}
    entries = []
    for child in directory.iterdir():
        if len(entries) >= 1000:
            raise SafetyError('已保存项目超过本入口容量；请使用原路径检查入口')
        entries.append(child)
    rows = []
    for child in sorted(entries, key=lambda p: p.name):
        if not re.fullmatch(r'[a-f0-9]{16}', child.name):
            continue
        target = unlinked(child / 'project.json')
        if not target.is_file() or target.stat().st_size > 16384:
            raise SafetyError('已有项目配置不可读；保留原记录，请使用原路径检查入口')
        saved = json.loads(target.read_text(encoding='utf-8'))
        path = saved.get('repository') if isinstance(saved, dict) else None
        if (not isinstance(path, str) or not Path(path).is_absolute()
                or len(path) > 4096 or any(ord(c) < 32 for c in path)
                or digest(os.path.normcase(path).encode())[:16] != child.name):
            raise SafetyError('已有项目归属不一致；保留原记录')
        rows.append({'path': path, 'name': Path(path).name})
    start = (page - 1) * 30
    return {'projects': rows[start:start + 30], 'page': page, 'has_next': len(rows) > start + 30}


def export_report(root, identity, kind, inputs):
    directory = unlinked(root / 'exports' / identity)
    if directory.exists():
        raise SafetyError('此导出目录已存在；请保留原记录并核对结果')
    if kind == 'dashboard':
        result = export_dashboard([Path(path) for path in inputs], directory / 'dashboard.html')
        names = ['dashboard.html']
        details = {'runs': result['runs'], 'read_only': True}
    else:
        from .comparison_summary import load_comparison_summary, format_comparison_summary
        summary = load_comparison_summary(Path(inputs[0]))
        # Only the original allowlisted summary is exported, never arbitrary input fields.
        directory.mkdir(parents=True, exist_ok=False)
        (directory / 'comparison.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (directory / 'comparison.md').write_text(format_comparison_summary(summary), encoding='utf-8')
        names = ['comparison.json', 'comparison.md']
        details = {'status': summary['status'], 'read_only': True,
                   'model_accuracy_claim': False, 'real_model_requests': 0}
    artifacts = []
    for name in names:
        data = unlinked(directory / name).read_bytes()
        artifacts.append({'name': name, 'url': f'/api/exports/{identity}/{name}',
                          'sha256': digest(data), 'bytes': len(data)})
    return {**details, 'directory': str(directory), 'artifacts': artifacts}


def read_artifact(workbench, identity, name):
    if not re.fullmatch(r'[a-f0-9]{32}', identity) or name not in ARTIFACTS:
        raise SafetyError('导出文件引用无效')
    with workbench.lock:
        job = workbench.jobs.get(identity)
        if not job or job['action'] not in {'export_dashboard', 'export_comparison', 'capability_execute'} or job['status'] != 'finished':
            raise SafetyError('没有本次会话已完成的导出记录，请核对原文件')
        record = next((row for row in job['result']['artifacts'] if row['name'] == name), None)
    if not record:
        raise SafetyError('此导出记录不包含所选文件')
    target = unlinked(workbench.root / ('tool-outputs' if job['action'] == 'capability_execute' else 'exports') / identity / name)
    if not target.is_file() or target.stat().st_size > MAX_DOWNLOAD:
        raise SafetyError('导出文件缺失或超出容量')
    data = target.read_bytes()
    if len(data) != record['bytes'] or digest(data) != record['sha256']:
        raise SafetyError('导出文件已变化，请保留现场')
    return data, ARTIFACTS[name]
