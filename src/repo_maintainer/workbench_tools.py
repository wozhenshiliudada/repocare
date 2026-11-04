"""Allowlisted adapters for existing local CLI libraries, never a shell gateway."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from .dashboard import unlinked, read_json
from .git import RepositoryGateway
from .models import SafetyError, digest
from .store import redact


def field(key, label, kind='text', *, options=None, default='', required=True):
    return {'key': key, 'label': label, 'kind': kind, 'options': options,
            'default': default, 'required': required}


PATH = lambda key, label, **kw: field(key, label, 'path', **kw)
REPO = PATH('repository', '目标 Git 仓库完整路径')
ROOT = PATH('source_root', '已有资料集完整路径（含 memory-catalog.json）')
DEFINITIONS = {
    'git-compare': ('固定 Git 版本比较', [REPO, field('head', '目标提交或引用', default='HEAD'),
        field('base', '基线提交或引用', required=False), field('mode', '比较方式', 'select', options=['commit','range','branch'], default='commit'),
        field('roots', '只读目录范围（每行一项）', 'lines', default='.')], '只读 Git 提交与差异，不 checkout/fetch 或修改文件。'),
    'preflight': ('原范围只测基线', [field('workspace_id', '当前已连接维护会话', 'workspace'),
        field('issue', '本次基线对应的问题')], '在原 Policy 的隔离工作区执行原检查；可能执行项目代码，不调用模型，不批准修改。'),
    'python-binding': ('可选 Python 绑定静态检查', [REPO, field('sources', '复制的 .py/.pyi 路径（每行一项）', 'lines'),
        field('checks', '要检查的文件（每行一项，须包含在上述范围）', 'lines'), field('version', 'Python 版本', default='3.11'),
        field('platform', '目标平台', 'select', options=['Windows','Linux','Darwin'], default='Windows')],
        '调用已安装的固定 Pyright，复制所选源码静态检查，不导入或执行目标项目；原工具单文件100000字节、最多50个来源。'),
    'archive-import': ('旧运行只读归档', [field('operation', '操作', 'select', options=['import','verify'], default='import'),
        PATH('run_dir', '要归档的原运行目录', required=False), PATH('archive_root', '已有归档根（验证时填写）', required=False),
        field('archive_id', '已有归档ID（验证时填写）', required=False)], '导入只复制到新的工具输出目录；验证不恢复任务、批准或额度。'),
    'backup': ('原历史备份与验证', [PATH('runs_dir', '原 runs 目录（备份须已停止该目录的服务）'),
        field('operation', '操作', 'select', options=['backup','verify'], default='backup'),
        field('backup_id', '已有备份ID（验证时填写）', required=False)],
        '原备份库在原 runs 目录内创建独立备份，保留原锁；活动服务拒绝备份，不停止或迁移它。'),
    'source-build': ('来源资料索引', [ROOT, REPO], '原 build_memory 为已有Markdown资料集生成新索引；不更改原资料或现有任务绑定。'),
    'case-export': ('运行报告导出案例', [PATH('run_dir', '原运行目录'), REPO,
        field('case_id', '案例ID（英文字母、数字、点、下划线或连字符）'), field('topic', '主题ID'),
        field('start_line', 'report.md 起始行', 'integer', default=1), field('end_line', 'report.md 结束行', 'integer', default=1)],
        '按明确行范围复制原报告并创建新案例资料集与索引，不改变原运行或审阅状态。'),
    'source-compose': ('组合固定来源', [PATH('selection', '原组合选择清单 JSON 完整路径'), REPO],
        '原 compose_memory 核对各成员来源，输出新组合资料集与索引，不更改成员。'),
    'relations': ('替代声明登记', [PATH('spec', '原替代关系说明 JSON 完整路径'), ROOT, REPO,
        PATH('index', '已有来源索引完整路径')], '原 register_relations 核验声明与来源，输出新关系清单；不作废原规则或授予执行权限。'),
    'structure-build': ('代码结构建图', [REPO, field('paths', '明确的源码路径（每行一项）', 'lines')],
        '原 CodeGraph 在选定源码副本上建图；需已有可选运行时，不下载、不自动绑定旧任务。'),
    'semantic-package': ('本地语义包登记', [PATH('manifest', '已安装固定语义包的 manifest JSON 完整路径')],
        '原 ModelPackage 核对固定模型/许可证/文件哈希；登记仅供之后新建会话选择，原会话及检索开关保持，不下载模型。'),
    'memory-migration': ('历史资料迁移', [PATH('selection', '原资料迁移选择清单 JSON 完整路径'), REPO],
        '原 migrate_memory 生成新历史资料集与目标索引，不恢复原运行权限、批准或预算。'),
    'report-export': ('运行报告下载', [PATH('run_dir', '原运行目录'), field('format', '报告格式', 'select', options=['JSON','Markdown'], default='JSON')],
        'JSON复用原白名单运行投影，Markdown读取固定 report.md 并脱敏；输出新文件，不改写原报告。'),
    'source-bind': ('新维护会话固定来源', [REPO, PATH('source_root', '资料集路径（与索引成对填写）', required=False),
        PATH('index', '已有 .memory.sqlite3 索引路径', required=False), PATH('relations', '已有替代关系清单（可选）', required=False),
        field('relations_context', '新任务是否装载替代关系只读上下文', 'select', options=['no','yes'], default='no'),
        PATH('structure_index', '已有结构索引目录（可选）', required=False), field('structure_query', '新任务结构上下文查询（可选，留空只供查询）', required=False)],
        '仅为尚无任务账的新会话固定原只读来源；填写结构查询或关系上下文会供之后新任务装载。旧任务、原审批和额度不换绑。'),
}


def parameters(capability, value):
    if capability not in DEFINITIONS or not isinstance(value, dict):
        raise SafetyError('未知本地能力或参数')
    fields = DEFINITIONS[capability][1]
    if set(value) != {f['key'] for f in fields}:
        raise SafetyError('本地能力参数字段不匹配')
    result = {}
    for f in fields:
        v = value[f['key']]
        if f['kind'] == 'integer':
            if type(v) is not int or not 1 <= v <= 1000000:
                raise SafetyError('行号应为1至1000000的整数')
        else:
            if not isinstance(v, str) or len(v) > (8000 if f['kind'] == 'lines' else 4096) or '\0' in v:
                raise SafetyError('本地能力参数无效')
            v = v.strip()
            if f['required'] and not v:
                raise SafetyError('请填写：' + f['label'])
            if f['kind'] == 'path' and v:
                if not Path(v).is_absolute() or any(ord(c) < 32 for c in v):
                    raise SafetyError('请提供不含控制字符的完整路径')
                v = str(unlinked(Path(v)))
            if f['options'] and v not in f['options']:
                raise SafetyError('请选择列出的操作')
        result[f['key']] = v
    if capability == 'archive-import':
        required = ['run_dir'] if result['operation'] == 'import' else ['archive_root','archive_id']
        if not all(result[k] for k in required):
            raise SafetyError('导入需要运行目录；验证需要归档根与ID')
    if capability == 'backup' and result['operation'] == 'verify' and not result['backup_id']:
        raise SafetyError('请填写原备份ID')
    return result


def lines(value):
    return [s.strip() for s in value.splitlines() if s.strip()]


class WorkbenchTools:
    def __init__(self, hub):
        self.hub, self.previews = hub, {}
        self.resources = unlinked(hub.root / 'tool-resources.json')
        if self.resources.exists():
            saved, _ = read_json(self.resources)
            if set(saved) != {'manifest','sha256'}:
                raise SafetyError('已有本地资源登记无效，请保留原记录')
            from .semantic_retrieval import ModelPackage
            package = ModelPackage(Path(saved['manifest']))
            if package.sha != saved['sha256']:
                raise SafetyError('已登记语义包发生变化，请保留原记录')
            if hub.semantic_model_manifest is not None and Path(hub.semantic_model_manifest) != package.path:
                raise SafetyError('显式语义包与原网页登记不一致')
            hub.semantic_model_manifest = package.path

    def definitions(self):
        return {'capabilities': [{'id': key, 'title': v[0], 'fields': v[1], 'impact': v[2],
                                  'unavailable_reason': self.prerequisite(key)}
                                 for key, v in DEFINITIONS.items()]}

    def prerequisite(self, capability):
        root = Path(__file__).resolve().parents[2]
        if capability == 'python-binding' and (not (root / 'runtime/pyright/node_modules/pyright/package.json').is_file()
                or not (root / 'scripts/python_binding_check.py').is_file()):
            return '原可选 Pyright 1.1.414 尚未安装；按项目说明安装后再使用，不自动下载。'
        if capability == 'structure-build' and not (root / 'runtime/codegraph/node_modules').is_dir():
            return '原可选 CodeGraph 运行时尚未安装；按项目说明安装后再使用。'
        return None

    def workspace(self, identity):
        workspace = self.hub.workspaces.get(identity)
        if workspace is None or workspace.session.active:
            raise SafetyError('请选择当前已连接且闲置的维护会话')
        return workspace.session

    def pins(self, capability, params):
        pins = {}
        for key, value in params.items():
            if key in {'spec','selection','manifest','index'} and value:
                from .ui_backup import read_bytes
                _, pins[key] = read_json(Path(value)) if key != 'index' else ({}, digest(read_bytes(Path(value), 32 * 1024**2)))
        if params.get('repository'):
            pins['head'] = RepositoryGateway(Path(params['repository'])).head
        if capability == 'preflight':
            from .baseline_probe import scope_for
            from .task_session import fingerprint
            session = self.workspace(params['workspace_id'])
            pins['scope'] = fingerprint(scope_for(RepositoryGateway(session.repo), params['issue'], session.policy))
            pins['session'] = session.token
        if capability == 'source-bind':
            self.check_binding(params, fresh=True)
        return pins

    def binding_file(self, repository):
        return unlinked(self.hub.root / 'source-bindings' / (digest(os.path.normcase(str(repository)).encode())[:16] + '.json'))

    def check_binding(self, p, *, fresh=False):
        repo = Path(p['repository'])
        if fresh:
            key = digest(os.path.normcase(str(repo)).encode())[:16]
            if str(repo) in self.hub.sessions or (self.hub.root / 'projects' / key / 'runs/ui-state/registry.sqlite3').exists():
                raise SafetyError('已有任务的来源保持固定；请为新会话使用独立工作台状态目录')
        if bool(p['source_root']) != bool(p['index']) or (p['relations'] and not p['source_root']):
            raise SafetyError('来源与索引须成对，替代关系须有固定来源')
        if p['relations_context'] == 'yes' and not p['relations']:
            raise SafetyError('关系模型上下文须有明确替代关系清单')
        if not p['source_root'] and not p['structure_index']:
            raise SafetyError('请选择至少一种已有只读来源')
        if p['source_root']:
            from .source_memory import search_memory
            search_memory(Path(p['source_root']), repo, Path(p['index']), 'verification', limit=1)
        if p['relations']:
            from .decision_relations import verify_relations
            verify_relations(Path(p['relations']), Path(p['source_root']), repo, Path(p['index']))
        if p['structure_index']:
            from .structure_index import verify_structure, query_structure
            verify_structure(Path(p['structure_index']), repo)
            if p['structure_query']:
                query_structure(Path(p['structure_index']), repo, p['structure_query'], 1)
        elif p['structure_query']:
            raise SafetyError('结构模型上下文须有明确结构索引')

    def binding(self, repository):
        target = self.binding_file(repository)
        if not target.exists():
            return {}
        value, _ = read_json(target)
        params = parameters('source-bind', value)
        if params['repository'] != str(repository):
            raise SafetyError('只读来源登记归属不一致')
        self.check_binding(params)
        return params

    def preview(self, capability, values):
        if len(self.previews) >= 100:
            raise SafetyError('本次服务预览已满，请保留记录后重启')
        params = parameters(capability, values)
        if self.prerequisite(capability):
            raise SafetyError(self.prerequisite(capability))
        for f in DEFINITIONS[capability][1]:
            value = params[f['key']]
            if f['kind'] == 'path' and value and Path(value).is_dir() and self.hub.root.is_relative_to(Path(value)):
                raise SafetyError('工作台输出目录必须在所选原始输入目录之外')
        if params.get('repository'):
            repo = RepositoryGateway(Path(params['repository']))
            if self.hub.root.is_relative_to(repo.common_dir):
                raise SafetyError('工具输出不能位于原仓库Git元数据内')
        if capability == 'git-compare':
            repo = RepositoryGateway(Path(params['repository']))
            for key in ('head', 'base'):
                if params[key]:
                    params[key] = repo.command('rev-parse','--verify','--end-of-options',params[key]+'^{commit}').decode().strip()
        core = {'capability': capability, 'params': params, 'pins': self.pins(capability, params)}
        identity = uuid.uuid4().hex
        revision = digest(json.dumps(core, sort_keys=True, ensure_ascii=False).encode())
        self.previews[identity] = {'core': core, 'revision': revision, 'consumed': False}
        return {'preview_id': identity, 'revision': revision, 'capability': capability,
                'params': params, 'impact': DEFINITIONS[capability][2],
                'executes_project_tests': capability == 'preflight', 'model_requests': 0}

    def execute(self, payload, job_id):
        row = self.previews.get(payload['preview_id'])
        if (not row or row['consumed'] or payload['confirmed'] is not True
                or row['revision'] != payload['revision']):
            raise SafetyError('预览已变化、已消费或尚未确认；请核对原记录')
        core = row['core']
        if self.pins(core['capability'], core['params']) != core['pins']:
            raise SafetyError('原输入或会话已变化，请重新预览')
        row['consumed'] = True  # A failure never grants replay of an old confirmation.
        directory = unlinked(self.hub.root / 'tool-outputs' / job_id)
        directory.mkdir(parents=True, exist_ok=False)
        result = self.dispatch(core['capability'], core['params'], directory, core['pins'])
        result = json.loads(redact(json.dumps(result, ensure_ascii=False, default=str)))
        markdown = result.pop('_markdown', None)
        content = redact(json.dumps(result, ensure_ascii=False, indent=2, default=str)).encode()
        if len(content) > 8 * 1024**2:
            raise SafetyError('工具结果超过8MiB，保留新输出目录')
        from .workbench_reports import ARTIFACTS
        artifacts = []
        for name, data in [('tool-result.json', content), *([('tool-report.md', markdown.encode())] if markdown is not None else [])]:
            assert name in ARTIFACTS
            target = directory / name
            with target.open('xb') as stream:
                stream.write(data)
            artifacts.append({'name': name, 'url': f'/api/exports/{job_id}/{name}',
                              'bytes': len(data), 'sha256': digest(data)})
        return {'preview_id': payload['preview_id'], 'capability': core['capability'], 'params': core['params'], 'directory': str(directory), 'result': result,
                'artifacts': artifacts, 'model_requests': 0}

    def dispatch(self, kind, p, out, pins):
        path = lambda name: Path(p[name])
        if kind == 'git-compare':
            from .git_investigation import investigate_git
            return investigate_git(RepositoryGateway(path('repository')), head_sha=p['head'],
                base_sha=p['base'] or None, mode=p['mode'], read_roots=lines(p['roots']))
        if kind == 'preflight':
            from .baseline_probe import collect_baseline
            from .task_session import fingerprint
            session = self.workspace(p['workspace_id'])
            # Serialize with original maintenance jobs on the session's original executor.
            record = session.executor.submit(lambda: collect_baseline(session.repo, p['issue'], session.policy, out / 'runs',
                approval=lambda scope: 'approve' if fingerprint(scope) == pins['scope'] else 'reject', actor='local-workbench')).result()
            return {'status': record['events'][-1]['stage'], 'run_id': record['run_id'],
                    'tests': record['baseline_tests'], 'source_unchanged': record['source_unchanged'],
                    'error': record.get('error'), 'recovery': record.get('recovery')}
        if kind == 'python-binding':
            script = Path(__file__).resolve().parents[2] / 'scripts/python_binding_check.py'
            spec = importlib.util.spec_from_file_location('repocare_trusted_binding', script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            node = shutil.which('node')
            if not node:
                raise SafetyError('需要本机Node与原固定Pyright运行时，不自动安装')
            result, code = module.evaluate(path('repository'), lines(p['sources']), lines(p['checks']),
                                           str(Path(node).resolve()), p['version'], p['platform'])
            return {'returncode': code, **result}
        if kind == 'archive-import':
            from .legacy_archive import import_legacy_run, verify_legacy_run
            return (import_legacy_run(path('run_dir'), out / 'archive') if p['operation'] == 'import'
                    else verify_legacy_run(path('archive_root'), p['archive_id']))
        if kind == 'backup':
            from .ui_backup import backup_ui_history, verify_ui_backup
            return (backup_ui_history(path('runs_dir')) if p['operation'] == 'backup'
                    else verify_ui_backup(path('runs_dir'), p['backup_id']))
        if kind == 'source-build':
            from .source_memory import build_memory
            return build_memory(path('source_root'), path('repository'), out / 'sources.memory.sqlite3')
        if kind == 'case-export':
            from .report_cases import export_report_case
            return export_report_case(path('run_dir'), out / 'corpus', path('repository'), out / 'sources.memory.sqlite3',
                                      p['case_id'], p['topic'], p['start_line'], p['end_line'])
        if kind == 'source-compose':
            from .memory_collection import compose_memory
            return compose_memory(path('selection'), out / 'corpus', path('repository'), out / 'sources.memory.sqlite3')
        if kind == 'relations':
            from .decision_relations import register_relations
            return register_relations(path('spec'), path('source_root'), path('repository'), path('index'), out / 'claims.memory-relations.json')
        if kind == 'structure-build':
            from .structure_index import build_structure
            spec = out / 'selection.json'
            spec.write_text(json.dumps({'schema_version': 1, 'paths': lines(p['paths'])}), encoding='utf-8')
            return build_structure(spec, path('repository'), out / 'structure')
        if kind == 'semantic-package':
            from .semantic_retrieval import ModelPackage
            package = ModelPackage(path('manifest'))
            temporary = self.resources.with_suffix('.json.tmp')
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump({'manifest': str(package.path), 'sha256': package.sha}, stream)
            os.replace(temporary, self.resources)
            self.hub.semantic_model_manifest = package.path
            return {'status': 'registered_for_new_sessions', 'manifest': str(package.path),
                    'sha256': package.sha, 'existing_sessions_changed': False, 'retrieval_enabled': False}
        if kind == 'memory-migration':
            from .memory_migration import migrate_memory
            return migrate_memory(path('selection'), out / 'corpus', path('repository'), out / 'sources.memory.sqlite3')
        if kind == 'report-export':
            from .dashboard import project_run
            result = project_run(path('run_dir'))
            if p['format'] == 'Markdown':
                source = unlinked(path('run_dir') / 'report.md')
                with source.open('rb') as stream:
                    raw = stream.read(8 * 1024**2 + 1)
                if len(raw) > 8 * 1024**2:
                    raise SafetyError('原Markdown报告超过8MiB')
                result['_markdown'] = redact(raw.decode('utf-8'))
            return result
        if kind == 'source-bind':
            self.check_binding(p, fresh=True)
            target = self.binding_file(p['repository'])
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('x', encoding='utf-8') as stream:
                json.dump(p, stream, ensure_ascii=False)
            return {'status': 'registered_for_new_session', 'repository': p['repository'],
                    'existing_tasks_changed': False, 'registration': str(target)}
        raise SafetyError('此能力没有已接实现')
