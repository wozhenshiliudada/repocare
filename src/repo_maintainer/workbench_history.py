"""Explicit bounded history fetches into new owned bare repositories only."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .dashboard import unlinked
from .github_connection import branch_name, github_url
from .models import SafetyError, digest
from .runner import stop_tree
from .workbench_read import ReadError, graph_records


class HistoryCache:
    def __init__(self, workbench):
        self.workbench = workbench
        self.root = unlinked(workbench.root/'history')
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='workbench-history')
        self.jobs = {}
        self.active = None
        self.closed = False

    def submit(self, value):
        if not isinstance(value, dict) or set(value) != {'request_id','workspace_id','branches','confirmed'}:
            raise ReadError('历史补取字段无效', 400)
        identity, workspace_id, branches = value['request_id'], value['workspace_id'], value['branches']
        if (not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity)
                or not isinstance(workspace_id, str) or not re.fullmatch(r'[a-f0-9]{32}', workspace_id)
                or value['confirmed'] is not True or not isinstance(branches, list)
                or not 1 <= len(branches) <= 8 or len(set(branches)) != len(branches)):
            raise ReadError('请明确确认 1–8 个分支', 400)
        branches = [branch_name(b) for b in branches]
        with self.workbench.lock:
            workspace = self.workbench.workspaces.get(workspace_id)
        if workspace is None:
            raise ReadError('请重新连接本地仓库', 404)
        slug, url = github_url(workspace.github_url)
        specification = {'repository_id':workspace.repository_id, 'url':url, 'branches':branches, 'depth':300}
        signature = digest(json.dumps(specification, sort_keys=True).encode())
        with self.lock:
            if identity in self.jobs:
                if self.jobs[identity]['signature'] != signature:
                    raise ReadError('同一历史请求不能更换内容')
                return dict(self.jobs[identity])
            directory = unlinked(self.root/identity)
            if directory.exists():
                # Restart does not re-launch or adopt any old process/cache.
                raise ReadError('该历史请求已存在，不能重放；请查看原记录并发起新的明确操作')
            if self.closed or self.active or len(self.jobs) >= 20:
                raise ReadError('历史补取正在进行，或本次启动已达 20 项上限', 429)
            directory.mkdir(parents=True)
            with (directory/'intent.json').open('x', encoding='utf-8') as stream:
                json.dump({'id':identity, 'signature':signature, **specification}, stream, ensure_ascii=False)
            job = {'id':identity, 'signature':signature, 'status':'running', 'phase':'preflight', 'result':None}
            self.jobs[identity] = job
            self.active = identity
            self.save(job)
            self.executor.submit(self.execute, job, directory, url, branches)
            return dict(job)

    def save(self, job):
        directory = unlinked(self.root/job['id'])
        temporary = unlinked(directory/'state.tmp')
        temporary.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
        temporary.replace(unlinked(directory/'state.json'))

    def view(self, identity):
        if not re.fullmatch(r'[a-f0-9]{32}', identity):
            raise ReadError('历史请求编号无效', 400)
        with self.lock:
            if identity in self.jobs:
                return dict(self.jobs[identity])
        path = unlinked(self.root/identity/'state.json')
        if not path.is_file() or path.stat().st_size > 4*1024*1024:
            raise ReadError('历史补取记录不存在', 404)
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('id') != identity:
            raise ReadError('历史补取归属无效')
        if value['status'] == 'running':
            return {**value, 'status':'interrupted', 'error':'上次服务未记录终态；不重放补取，请保留缓存并明确发起新的请求'}
        return value

    def command(self, job, directory, *args, timeout=30):
        # No shell; no inherited git/askpass configuration; no source checkout involved.
        config = ['git','-c','core.hooksPath='+os.devnull,'-c','core.fsmonitor=false',
                  '-c','http.followRedirects=false','-c','protocol.file.allow=never',
                  '-c','protocol.ext.allow=never','-c','credential.helper=manager',
                  '-c','credential.interactive=never']
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(config+list(args), stdout=output, stderr=errors,
                stdin=subprocess.DEVNULL, env=self.workbench.github.environment(),
                start_new_session=os.name!='nt',
                creationflags=(getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)))
            with self.lock:
                job['process']={'pid':process.pid,'argv':list(args)}
                self.save(job)
            try:
                process.wait(timeout=timeout)
            except BaseException:
                stop_tree(process);process.wait(timeout=10)
                raise
            output.seek(0)
            raw = output.read(2*1024*1024+1)
            if process.returncode or len(raw)>2*1024*1024:
                raise ReadError('历史补取命令未完成；源仓库未用于 fetch，独立缓存与意图已保留')
            with self.lock:
                job.pop('process', None)
                self.save(job)
            return raw

    def execute(self, job, directory, url, branches):
        try:
            heads = {b:self.workbench.github.branch_head(url,b) for b in branches}
            cache = unlinked(directory/'objects.git')
            self.command(job,directory,'init','--bare',str(cache))
            for index, branch in enumerate(branches):
                with self.lock:
                    job.update(phase='fetching',branch=branch,completed_branches=index)
                    self.save(job)
                target = f'refs/heads/selected-{index}'
                self.command(job,directory,'-C',str(cache),'fetch','--depth=300','--no-tags',
                             '--no-recurse-submodules','--no-write-fetch-head','--',url,
                             f'refs/heads/{branch}:{target}',timeout=120)
                actual = self.command(job,directory,'-C',str(cache),'rev-parse',target).decode().strip()
                if actual != heads[branch]:
                    raise ReadError('远端分支在补取期间变化；保留本次缓存，不把它当作固定快照')
            raw = self.command(job,directory,'-C',str(cache),'log','--topo-order','--max-count=300',
                               '-z','--format=%H%x00%P%x00%ct%x00%s','--branches','--')
            commits = graph_records(raw)
            shallow = unlinked(cache/'shallow')
            boundaries = set(shallow.read_text().splitlines()) if shallow.is_file() else set()
            for c in commits:
                c['shallow'] = c['sha'] in boundaries
            result = {'cached':True,'branch':branches[0],'branches':branches,'heads':heads,
                      'commits':commits,'depth_per_branch':300,'truncated':len(commits)==300}
            with self.lock:
                job.update(status='finished',phase='finished',result=result,completed_branches=len(branches))
        except Exception as error:
            with self.lock:
                job.update(status='failed',phase='failed',error=str(error) if isinstance(error,SafetyError)
                           else '历史补取中断或超时；独立现场已保留，请明确核验后再发起新请求')
        finally:
            with self.lock:
                self.active=None
                self.save(job)

    def close(self):
        with self.lock:
            self.closed=True
        self.executor.shutdown(wait=True)
