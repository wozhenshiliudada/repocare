"""Bounded read-only workbench views, separate from the maintenance action queue."""
from __future__ import annotations

import asyncio
import json
import re
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener

from .git import RepositoryGateway
from .maintenance_limits import LIMITS
from .github_connection import NoRedirect, github_url
from .models import SafetyError, safe_context_path


class ReadError(SafetyError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class ReadPool:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='workbench-read')
        self.slots = threading.BoundedSemaphore(2)

    async def run(self, function, *args):
        if not self.slots.acquire(blocking=False):
            raise ReadError('资料读取繁忙，请稍后明确刷新', 429)
        # Release on worker completion, including when the HTTP client disconnects.
        def work():
            try:
                return function(*args)
            finally:
                self.slots.release()
        future = asyncio.get_running_loop().run_in_executor(self.executor, work)
        return await asyncio.shield(future)

    def close(self):
        self.executor.shutdown(wait=True)


def local_view(workspace, kind, path=None, *, page=1, query='', revision=None):
    repository = RepositoryGateway(workspace.session.repo)
    if kind == 'graph':
        return local_graph(repository)
    raw = repository.command('ls-files', '-z')
    if (type(page) is not int or not 1 <= page <= LIMITS['catalog_max_files']
            or not isinstance(query, str) or len(query) > 256 or any(ord(c) < 32 for c in query)):
        raise ReadError('文件分页或查询无效', 400)
    if len(raw) > LIMITS['catalog_max_path_bytes']:
        raise ReadError('文件列表超过上限')
    names = [name for name in raw.decode('utf-8', errors='strict').split('\0') if name]
    if len(names) > LIMITS['catalog_max_files']:
        raise ReadError('文件列表超过当前配置上限')
    from .models import digest
    head = repository.head
    current_revision = digest(head.encode() + b'\0' + raw)
    if revision is not None and revision != current_revision:
        raise ReadError('仓库目录版本已变化，请从第一页重新读取', 409)
    if kind == 'file':
        if not isinstance(path, str) or path not in names:
            raise ReadError('文件不在已追踪的安全普通文件列表中', 404)
        names = [path]
    files = []
    for name in names:
        if kind == 'tree' and query.casefold() not in name.casefold():
            continue
        try:
            safe_context_path(name)
            candidate = repository.safe_file(name)
            if candidate.stat().st_size <= LIMITS['source_file_max_bytes']:
                files.append(name)
        except (SafetyError, OSError):
            continue
    if kind == 'tree':
        if repository.head != head:
            raise ReadError('仓库版本在读取期间变化，请重新读取',409)
        start, size = (page - 1) * 200, 200
        return {'files': files[start:start + size], 'head': head,
                'limit': LIMITS['source_file_max_bytes'], 'catalog_limit': LIMITS['catalog_max_files'],
                'page': page, 'page_size': size, 'has_next': len(files) > start + size,
                'total': len(files), 'query': query, 'revision': current_revision}
    if kind != 'file' or not isinstance(path, str) or path not in files:
        raise ReadError('文件不在已追踪的安全普通文件列表中', 404)
    candidate = repository.safe_file(safe_context_path(path))
    with candidate.open('rb') as stream:
        content = stream.read(LIMITS['source_file_max_bytes'] + 1)
    if len(content) > LIMITS['source_file_max_bytes'] or b'\0' in content:
        raise ReadError('文件超过当前配置容量或为二进制文件', 415)
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        raise ReadError('此文件不是 UTF-8 文本', 415) from None
    return {'path': path, 'content': text, 'bytes': len(content)}


def graph_records(raw):
    if len(raw) > 2 * 1024 * 1024:
        raise ReadError('提交历史超过响应上限')
    fields = raw.rstrip(b'\0').split(b'\0') if raw else []
    if len(fields) % 4:
        raise ReadError('提交历史格式无效')
    commits = []
    for index in range(0, len(fields), 4):
        sha, parents, timestamp, subject = fields[index:index+4]
        if not re.fullmatch(rb'[a-f0-9]{40,64}', sha):
            raise ReadError('提交标识无效')
        parent_ids = parents.decode('ascii').split()
        if any(not re.fullmatch(r'[a-f0-9]{40,64}', p) for p in parent_ids):
            raise ReadError('父提交标识无效')
        commits.append({'sha':sha.decode(), 'parents':parent_ids, 'timestamp':int(timestamp),
                        'subject':subject.decode('utf-8', errors='replace')[:1000]})
    present = {c['sha'] for c in commits}
    for c in commits:
        c['missing_parents'] = [p for p in c['parents'] if p not in present]
    return commits


def local_graph(repository):
    refs = repository.command('for-each-ref', '--count=256', '--format=%(refname:short)', 'refs/heads', 'refs/remotes')
    if len(refs) > 65536:
        raise ReadError('分支列表超过上限')
    branch = repository.command('symbolic-ref', '--short', 'HEAD', check=False).decode().strip()
    # Local refs are facts only. No fetch, ref update, test or source checkout operation.
    raw = repository.command('log', '--topo-order', '--max-count=300', '-z',
                             '--format=%H%x00%P%x00%ct%x00%s', 'HEAD', '--branches', '--remotes', '--')
    commits = graph_records(raw)
    shallow = repository.command('rev-parse', '--is-shallow-repository').strip() == b'true'
    if shallow:
        from .dashboard import unlinked
        value=repository.command('rev-parse','--git-path','shallow').decode().strip()
        path=Path(value)
        path=unlinked(path if path.is_absolute() else repository.root/path)
        with path.open('rb') as stream:
            data=stream.read(65537)
        if len(data)>65536:
            raise ReadError('浅克隆边界超过上限')
        boundaries=set(data.decode('ascii').splitlines())
        for commit in commits:
            commit['shallow']=commit['sha'] in boundaries
    branches = [b for b in refs.decode().splitlines() if b and not b.endswith('/HEAD')]
    return {'commits':commits, 'branch':branch, 'branches':branches, 'shallow':shallow,
            'cached':False, 'truncated':len(commits) == 300}


class GitHubBrowser:
    """Fixed endpoint read transport. Raw credentials/headers/errors never reach callers."""
    def __init__(self, connection):
        self.connection = connection

    def get(self, path):
        token = self.connection.credential()
        request = Request('https://api.github.com'+path, headers={
            'Authorization':'Bearer '+token, 'Accept':'application/vnd.github+json',
            'User-Agent':'RepoCare-workbench', 'X-GitHub-Api-Version':'2022-11-28'})
        try:
            with build_opener(NoRedirect()).open(request, timeout=20) as response:
                raw = response.read(512*1024+1)
                link = response.headers.get('Link', '')
            if len(raw) > 512*1024:
                raise ReadError('GitHub 响应超过上限', 413)
            return json.loads(raw), bool(re.search(r'<https://api\.github\.com/[^<>\s]+>;\s*rel="next"', link))
        except HTTPError as error:
            if error.code == 401:
                raise ReadError('GitHub 登录失效，请在连接设置中重新登录', 401) from None
            if error.code == 403:
                rate = error.headers.get('X-RateLimit-Remaining') == '0' or bool(error.headers.get('Retry-After'))
                raise ReadError('GitHub 请求频率受限，请稍后手动刷新' if rate else 'GitHub 权限不足，请检查组织授权', 429 if rate else 403) from None
            if error.code == 404:
                raise ReadError('仓库或条目不存在，或当前账号无权读取', 404) from None
            raise ReadError('GitHub 服务读取失败，请稍后手动刷新', 502) from None
        except (URLError, TimeoutError, ValueError):
            raise ReadError('GitHub 网络读取失败，请稍后手动刷新', 502) from None
        finally:
            token = None

    def view(self, kind, url=None, page=1):
        if type(page) is not int or not 1 <= page <= 99:
            raise ReadError('页码应在 1–99 之间', 400)
        if kind == 'account':
            data, _ = self.get('/user')
            if not isinstance(data, dict) or not isinstance(data.get('login'), str):
                raise ReadError('GitHub 账号响应无效', 502)
            return {'login':data['login'][:100]}
        if kind == 'repositories':
            data, has_next = self.get(f'/user/repos?per_page=30&page={page}&sort=updated')
            if not isinstance(data, list):
                raise ReadError('GitHub 仓库响应无效', 502)
            repos = []
            for item in data[:30]:
                try:
                    slug, canonical = github_url(item.get('html_url'))
                    repos.append({'name':slug, 'url':canonical, 'private':bool(item.get('private')),
                                  'archived':bool(item.get('archived'))})
                except (SafetyError, AttributeError):
                    continue
            return {'repositories':repos, 'page':page, 'has_next':has_next and page < 99}
        slug, _ = github_url(url)
        if kind == 'branches':
            data, has_next = self.get(f'/repos/{slug}/branches?per_page=100&page={page}')
            if not isinstance(data,list):
                raise ReadError('GitHub 分支响应无效',502)
            from .github_connection import branch_name
            branches=[]
            for item in data[:100]:
                try:
                    branches.append(branch_name(item.get('name')))
                except (SafetyError,AttributeError):
                    continue
            return {'branches':branches,'has_next':has_next and page<99,'page':page}
        suffix = {'issues':f'issues?state=all&per_page=30&page={page}',
                  'pulls':f'pulls?state=all&per_page=30&page={page}',
                  'actions':f'actions/runs?per_page=30&page={page}'}
        if kind not in suffix:
            raise ReadError('不支持的 GitHub 只读视图', 404)
        data, has_next = self.get('/repos/'+slug+'/'+suffix[kind])
        entries = data.get('workflow_runs') if kind == 'actions' and isinstance(data, dict) else data
        if not isinstance(entries, list):
            raise ReadError('GitHub 条目响应无效', 502)
        items = []
        for item in entries[:30]:
            if not isinstance(item, dict) or kind == 'issues' and 'pull_request' in item:
                continue
            link = item.get('html_url', '')
            parsed = urlsplit(link) if isinstance(link, str) else None
            if not parsed or parsed.scheme != 'https' or parsed.netloc != 'github.com' or not parsed.path.startswith('/'+slug+'/'):
                continue
            if type(item.get('id')) is not int:
                continue
            user = item.get('user') or item.get('actor') or {}
            projected = {'id':item['id'], 'number':item.get('number'), 'url':link,
                          'title':str(item.get('title') or item.get('display_title') or item.get('name') or '')[:1000],
                          'state':str(item.get('conclusion') or item.get('state') or item.get('status') or '')[:100],
                          'author':str(user.get('login', ''))[:100],
                          'updated_at':str(item.get('updated_at') or '')[:100]}
            if kind == 'actions':
                for field, maximum in [('run_attempt', 10000), ('workflow_id', 2**53-1)]:
                    value = item.get(field)
                    if type(value) is int and 1 <= value <= maximum:
                        projected[field] = value
                head = item.get('head_sha')
                if isinstance(head, str) and re.fullmatch(r'[a-f0-9]{40}', head):
                    projected['head_sha'] = head
            items.append(projected)
        return {'items':items, 'page':page, 'has_next':has_next and page < 99}
