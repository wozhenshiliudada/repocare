"""Opt-in read-only delivery observation on the original monitor journal/queue."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import re
import uuid
from urllib.parse import quote

from .issue_acceptance import ReadWindow, acceptance_policy, merged_graph, positive, require, workflows
from .issue_intake import fingerprint
from .monitor_acceptance import MonitorAcceptance, association
from .monitor_history import identity
from .store import redact

KIND = 'monitor_acceptance_watch'
OBSERVATIONS = 'monitor_acceptance_observation'
MAX_HISTORY = 100
TERMINAL_STATUSES = frozenset({'scope_changed','closed_without_merge','expired','capacity_reached'})


def utc(): return datetime.now(timezone.utc)


def timestamp(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value),
            '观察到期时间须为UTC秒级时间')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def formatted(value): return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def projection(row):
    require(row['authorization_sha256'] == fingerprint(row['core']), '原观察范围变化')
    last=row.get('last_observation')
    if last:
        require(last['sha256'] == fingerprint(last['outcome'])
                and last['authorization_sha256'] == row['authorization_sha256']
                and last['watch_id'] == row['id'] and last['run_id'] == row['run_id'], '原观察回执变化')
    return {**{k:deepcopy(row.get(k)) for k in ('id','run_id','status','enabled','revision','last_checked',
        'next_check','error','notification','observations','last_observation','core')},'challenge':fingerprint(row)}


class AcceptanceWatch:
    def __init__(self, monitor, clock=utc):
        self.monitor, self.clock, self.records, self.faults = monitor, clock, {}, set()

    def _service(self, mid, eid):
        from .monitor_delivery import MonitorDelivery
        delivery = MonitorDelivery(self.monitor, mid, eid, getattr(self.monitor.hub,'monitor_delivery_remote',None))
        return MonitorAcceptance(delivery, getattr(self.monitor.hub,'monitor_acceptance_remote',None))

    def _checked(self, row):
        core = row['core']; binding = core['binding']
        identity(row['id'],32); identity(row['run_id'],32)
        require(row['run_id'] == binding['run_id'] and binding['origin_kind'] == 'monitor'
                and row['authorization_sha256'] == fingerprint(core)
                and type(row['enabled']) is bool and type(row['revision']) is int
                and type(core.get('schema_version')) is int and core['schema_version'] == 1
                and core['remote_writes'] is False and core['model_calls'] is False,
                '原观察范围或确认记录变化')
        require(fingerprint(core['policy']) == fingerprint(acceptance_policy(core['policy']['workflow_ids']))
                and type(core['interval_seconds']) is int and 60 <= core['interval_seconds'] <= 3600,
                '原观察频率或工作流规则变化')
        timestamp(core['expires_at'])
        require(isinstance(row['observations'],list) and len(row['observations']) <= MAX_HISTORY,
                '原观察历史异常，保留原日志')
        last=row.get('last_observation')
        if last:
            registry=self.monitor.registries[binding['monitor_id']]
            require(row['observations'] and row['observations'][-1] == last['id']
                    and registry.load(OBSERVATIONS).get(last['id']) == last
                    and last['watch_id'] == row['id'] and last['run_id'] == row['run_id']
                    and last['authorization_sha256'] == row['authorization_sha256']
                    and last['sha256'] == fingerprint(last['outcome']), '原观察回执或范围绑定缺失/变化')
        return row

    def restore(self, mid):
        rows = self.monitor.registries[mid].load(KIND)
        require(len(rows) <= 20, '原监控观察数超过界限')
        for wid, row in rows.items():
            self._checked(row)
            require(wid == row['id'] and row['core']['binding']['monitor_id'] == mid, '原观察日志归属不同')
            if row['enabled']:
                row.update(enabled=False,status='restart_paused',next_check=None,pending_cycle=None,
                           error='服务已重启；原历史保留，需要显式恢复只读观察',revision=row['revision']+1)
                self.monitor.registries[mid].put(KIND,wid,row)
            with self.monitor.lock:self.records[mid,wid] = row

    def view(self, mid, wid):
        identity(mid,16);identity(wid,32)
        row = self.monitor.registries[mid].load(KIND).get(wid)
        require(row, '未知只读观察')
        self._checked(row)
        require(row['core']['binding']['monitor_id'] == mid, '观察不属于此监控')
        return {**row,'challenge':fingerprint(row)}

    def _save(self, mid, row, expected=None):
        registry = self.monitor.registries[mid]
        with self.monitor.lock, registry.transaction() as db:
            old = db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(KIND,row['id'])).fetchone()
            if expected is not None:
                require(old and fingerprint(json.loads(old[0])) == expected, '观察状态已变化，请刷新')
            registry.put(KIND,row['id'],row,db)
            self.records[mid,row['id']] = deepcopy(row)

    def _inspect(self, service, run_id, reader):
        original = association(service.delivery,run_id)
        binding, receipt = original['binding'], original['receipt']
        require(receipt and positive(receipt.get('id')) and positive(receipt.get('number')),
                '先核对原PR创建回执，再启用持续观察')
        base = '/repos/'+binding['repository']
        meta = reader.api(base)
        require(meta.get('id') == binding['repository_id']
                and str(meta.get('full_name','')).casefold() == binding['repository']
                and meta.get('default_branch') == binding['base'], '观察仓库或默认分支变化')
        pr = reader.api(base+'/pulls/'+str(receipt['number']))
        require(pr.get('id') == receipt['id'] and pr.get('number') == receipt['number']
                and str(pr.get('html_url','')).casefold() == f"https://github.com/{binding['repository']}/pull/{receipt['number']}"
                and pr.get('head',{}).get('sha') == binding['commit']
                and pr.get('head',{}).get('repo',{}).get('id') == binding['repository_id']
                and pr.get('base',{}).get('ref') == binding['base']
                and pr.get('base',{}).get('repo',{}).get('id') == binding['repository_id']
                and pr.get('state') in {'open','closed'} and type(pr.get('merged')) is bool,
                '原PR身份、候选或目标范围变化')
        branch = reader.api(base+'/branches/'+quote(binding['base'],safe=''))
        head = branch.get('commit',{}).get('sha')
        require(branch.get('name') == binding['base'] and isinstance(head,str)
                and re.fullmatch('[a-f0-9]{40}',head), '默认分支准确提交不可核验')
        rows = reader.pages(base+'/actions/runs?branch='+quote(binding['base'],safe='')
            +'&head_sha='+head+'&per_page=30&page=','workflow_runs')
        require(len({r.get('id') for r in rows}) == len(rows), '工作流运行身份重复')
        for row in rows:
            require(all(positive(row.get(k)) for k in ('id','workflow_id','run_number','run_attempt'))
                    and row.get('head_sha') == head and row.get('head_branch') == binding['base']
                    and (row.get('repository') or {}).get('id') == binding['repository_id'],
                    '工作流来源或版本不同')
        return original, pr, head, rows

    def preview(self, delivery, run_id, interval_seconds, expires_at):
        self.monitor.hub.runtime.require_current()
        require(type(interval_seconds) is int and 60 <= interval_seconds <= 3600, '只读观察间隔为60至3600秒')
        expiry = timestamp(expires_at)
        require(self.clock()+timedelta(seconds=60) <= expiry <= self.clock()+timedelta(hours=24),
                '观察须在1分钟至24小时内到期')
        service = MonitorAcceptance(delivery,getattr(self.monitor.hub,'monitor_acceptance_remote',None))
        original,pr,head,rows = self._inspect(service,run_id,ReadWindow(service.remote))
        ids = sorted({r['workflow_id'] for r in rows})
        required = original['original_ci_workflow_id']
        require(required is None or required in ids, '原失败工作流未在当前默认版本出现，不能省略')
        core = {'schema_version':1,'binding':original['binding'],'association_sha256':fingerprint(original),
            'pr':{'id':pr['id'],'number':pr['number'],'url':pr['html_url'],'head_sha':original['binding']['commit']},
            'observed_default_head':head,'policy':acceptance_policy(ids),
            'interval_seconds':interval_seconds,'expires_at':expires_at,'remote_writes':False,'model_calls':False}
        return {'core':core,'revision':fingerprint({'core':core,'session':self.monitor.hub.token})}

    def configure(self, delivery, proposal, revision, confirmed):
        require(confirmed is True, '请明确确认只读观察范围与到期时间')
        core = proposal
        require(core['binding']['monitor_id'] == delivery.mid and core['binding']['event_id'] == delivery.eid,
                '观察提案不属于原事件')
        current = self.preview(delivery,core['binding']['run_id'],core['interval_seconds'],core['expires_at'])
        require(current == {'core':core,'revision':revision}, '观察提案或来源已变化，请重新预览')
        mid = delivery.mid; registry = self.monitor.registries[mid]
        require(core['binding']['monitor_id'] == mid and core['binding']['event_id'] == delivery.eid,
                '观察提案不属于原事件')
        rows = registry.load(KIND)
        require(len(rows) < 20 and not any(r['run_id'] == core['binding']['run_id'] and r['enabled'] for r in rows.values()),
                '已有相同运行的活动观察或达到容量，请先暂停并保留历史')
        row = {'id':uuid.uuid4().hex,'run_id':core['binding']['run_id'],'core':core,
            'authorization_sha256':fingerprint(core),'revision':0,'enabled':True,'status':'scheduled',
            'next_check':formatted(self.clock()),'last_checked':None,'error':None,'notification':None,
            'observations':[],'last_observation':None,'pending_cycle':None,'checks':0}
        self._save(mid,row)
        return self.view(mid,row['id'])

    def operate(self, delivery, action, payload):
        require(action in {'monitor_delivery_watch_preview','monitor_delivery_watch_configure',
                          'monitor_delivery_watch_pause','monitor_delivery_watch_resume'}, '未知只读观察操作')
        require(payload['monitor_id'] == delivery.mid and payload['event_id'] == delivery.eid,
                '观察操作不属于此原监控事件')
        run_id = payload['run_id'];identity(run_id,32)
        if action.endswith('_preview'):
            return self.preview(delivery,run_id,payload['interval_seconds'],payload['expires_at'])
        if action.endswith('_configure'):
            require(payload['preview']['binding']['run_id'] == run_id, '观察提案不属于此运行')
            return self.configure(delivery,payload['preview'],payload['revision'],payload['confirmed'])
        row = self.view(delivery.mid,payload['watch_id'])
        require(row['run_id'] == run_id and row['core']['binding']['event_id'] == delivery.eid,
                '不能控制另一事件的观察')
        return self.control('pause' if action.endswith('_pause') else 'resume',delivery.mid,row['id'],payload['revision'])

    def control(self, action, mid, wid, revision):
        row = self.view(mid,wid);row.pop('challenge')
        require(fingerprint(row) == revision, '观察状态已变化，请刷新后操作')
        observed_status = (row.get('last_observation') or {}).get('outcome',{}).get('status')
        # Older versions could overwrite a terminal row with paused. Its
        # original receipt must still require fresh confirmation after restart.
        require(row['status'] not in TERMINAL_STATUSES and observed_status not in TERMINAL_STATUSES,
                '观察已终止；请重新预览范围并明确确认')
        if action == 'pause':
            row.update(enabled=False,status='paused',next_check=None,pending_cycle=None,error=None)
        else:
            require(action == 'resume' and not row['enabled'] and row['status'] in {'paused','restart_paused','blocked'}
                    and timestamp(row['core']['expires_at']) > self.clock(),
                    '观察已启用或已到期；到期后须重新预览范围')
            require(len(row['observations']) < MAX_HISTORY, '观察历史达到容量；保留现场')
            self.monitor.hub.runtime.require_current()
            row.update(enabled=True,status='scheduled',next_check=formatted(self.clock()),error=None)
        row['revision'] += 1
        self._save(mid,row,revision)
        if action == 'resume':
            with self.monitor.lock:self.faults.discard((mid,wid))
        return self.view(mid,wid)

    def due(self):
        with self.monitor.lock:
            return [(mid,wid) for (mid,wid),r in self.records.items() if r['enabled']
                and (mid,wid) not in self.faults
                and (timestamp(r['core']['expires_at']) <= self.clock() or timestamp(r['next_check']) <= self.clock())][:4]

    def active(self):
        with self.monitor.lock:return any(r['enabled'] and key not in self.faults for key,r in self.records.items())

    def _outcome(self, service, row, reader):
        original,pr,head,runs = self._inspect(service,row['run_id'],reader)
        core = row['core']
        require(original['binding'] == core['binding'] and fingerprint(original) == core['association_sha256']
                and pr['id'] == core['pr']['id'], '原观察授权或报告已变化，需要重新确认')
        result = {'head_sha':head,'pr':core['pr'],'proof':None,'error':None,'runs':[]}
        if not pr['merged']:
            return {**result,'status':'waiting_merge' if pr['state'] == 'open' else 'closed_without_merge'}
        ids = set(core['policy']['workflow_ids']); observed = {r['workflow_id'] for r in runs}
        if observed-ids:return {**result,'status':'scope_changed','error':'出现未批准的工作流，请重新预览观察范围'}
        if ids-observed:return {**result,'status':'checks_missing','error':'准确默认版本缺少已批准工作流，不能沿用旧绿'}
        chosen = {}
        for run in runs:
            old = chosen.get(run['workflow_id'])
            key = (run['run_number'],run['run_attempt'])
            require(old is None or key != (old['run_number'],old['run_attempt']), '最新工作流运行不唯一')
            if old is None or key > (old['run_number'],old['run_attempt']):chosen[run['workflow_id']] = run
        result['runs'] = [{k:r.get(k) for k in ('id','workflow_id','run_number','run_attempt','status','conclusion','event')}
                          for _,r in sorted(chosen.items())]
        if any(r.get('event') not in core['policy']['events'] for r in chosen.values()):
            return {**result,'status':'blocked','error':'最新运行不符合已批准的事件规则'}
        if any(r.get('status') == 'completed' and r.get('conclusion') != 'success' for r in chosen.values()):
            return {**result,'status':'checks_failed','error':'最新工作流失败、取消或跳过；旧成功不适用'}
        if any(r.get('status') != 'completed' for r in chosen.values()):return {**result,'status':'waiting_ci'}
        # Only the existing complete graph/job/source verifier may produce green.
        before,verified_original = service._scope(row['run_id'],reader)
        require(verified_original == original and before['default_head_sha'] == head
                and before['acceptance_policy'] == core['policy'], '核验期间观察范围变化')
        graph = merged_graph(reader,core['binding'],before['pr'],head)
        checked = workflows(reader,core['binding'],head,core['policy'])
        after,_ = service._scope(row['run_id'],reader)
        require(after == before, '核验期间默认版本或CI attempt变化，不能签收')
        return {**result,'status':'verified','proof':{'default_head_sha':head,'pr':before['pr'],
            'inclusion':graph,'workflows':checked,'remote_writes':0,'local_tests_executed':False}}

    def poll(self, mid, wid):
        try:return self._poll(mid,wid)
        except Exception as error:
            # A damaged/unwritable journal must not cause a one-second retry loop.
            with self.monitor.lock:self.faults.add((mid,wid))
            try:
                row=self.view(mid,wid);row.pop('challenge');expected=fingerprint(row)
                row.update(enabled=False,status='blocked',next_check=None,revision=row['revision']+1,
                           error='观察日志或保存受阻；保留历史，需核对后重新启用',notification=uuid.uuid4().hex)
                self._save(mid,row,expected)
                return row
            except Exception:
                return {'status':'journal_blocked','enabled':False,'error':'原观察日志不可写或不可核验，保留现场'}

    def _poll(self, mid, wid):
        row = self.view(mid,wid);row.pop('challenge')
        if not row['enabled']:return row
        with self.monitor.registries[mid].transaction() as db:
            count=db.execute('SELECT count(*) FROM objects WHERE kind=?',(OBSERVATIONS,)).fetchone()[0]
        if timestamp(row['core']['expires_at']) <= self.clock() or count >= MAX_HISTORY:
            expected = fingerprint(row)
            row.update(enabled=False,status='expired' if timestamp(row['core']['expires_at']) <= self.clock()
                       else 'capacity_reached',next_check=None,pending_cycle=None,revision=row['revision']+1)
            self._save(mid,row,expected);return row
        if timestamp(row['next_check']) > self.clock():return row
        expected = fingerprint(row)
        row.update(status='checking',revision=row['revision']+1,pending_cycle=uuid.uuid4().hex)
        self._save(mid,row,expected);epoch = fingerprint(row)
        def allowed():
            current = self.view(mid,wid)
            require(current['challenge'] == epoch and current['enabled']
                    and timestamp(current['core']['expires_at']) > self.clock(), '观察已暂停、到期或来源控制变化')
            self.monitor.hub.runtime.require_current()
        class Window(ReadWindow):
            def api(window,path):
                allowed()
                value = super().api(path)
                allowed()
                return value
        service = self._service(mid,row['core']['binding']['event_id']);reader = Window(service.remote)
        try:
            outcome = self._outcome(service,row,reader)
            require(len(json.dumps(outcome,ensure_ascii=False).encode()) <= 256*1024,'观察回执超过有界大小')
        except Exception as error:
            outcome = {'status':'network_blocked' if isinstance(error,(OSError,TimeoutError)) else 'blocked',
                'error':redact(type(error).__name__+': '+str(error))[:500],'head_sha':None,'runs':[],'proof':None}
        current = self.view(mid,wid)
        if current['challenge'] != epoch:return current  # Pause wins over an in-flight GET.
        if timestamp(row['core']['expires_at']) <= self.clock():
            outcome = {'status':'expired','error':'观察到期；本次结果不签收','head_sha':None,'runs':[],'proof':None}
        digest = fingerprint(outcome)
        with self.monitor.lock, self.monitor.registries[mid].transaction() as db:
            old = db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(KIND,wid)).fetchone()
            require(old and fingerprint(json.loads(old[0])) == epoch, '观察控制在保存前变化')
            if not row['last_observation'] or row['last_observation']['sha256'] != digest:
                oid = uuid.uuid4().hex
                receipt = {'id':oid,'watch_id':wid,'run_id':row['run_id'],'outcome':outcome,
                    'sha256':digest,'authorization_sha256':row['authorization_sha256'],
                    'observed_at':formatted(self.clock()),'api_reads':reader.calls}
                self.monitor.registries[mid].put(OBSERVATIONS,oid,receipt,db)
                row['observations'].append(oid)
                row['last_observation'] = receipt
                row['notification'] = uuid.uuid4().hex
            terminal = outcome['status'] in {'expired','scope_changed','closed_without_merge'}
            row.update(status=outcome['status'],enabled=not terminal,error=outcome.get('error'),pending_cycle=None,
                revision=row['revision']+1,checks=row['checks']+1,last_checked=formatted(self.clock()),
                next_check=None if terminal else formatted(self.clock()+timedelta(seconds=row['core']['interval_seconds'])))
            self.monitor.registries[mid].put(KIND,wid,row,db)
            self.records[mid,wid] = deepcopy(row)
        return self.view(mid,wid)
