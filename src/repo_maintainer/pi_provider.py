from __future__ import annotations
from .maintenance_limits import LIMITS

import json
import os
import shutil
import subprocess
from pathlib import Path

from .models import Edit, Plan, SafetyError, digest
from .runner import stop_tree
from . import pi_evidence
from .runtime_limits import MAX_REQUESTS
from .pi_distribution import runtime_script
from .pi_revision import RevisionLedger, encoded, verified_reads




def decode_plan(data: dict) -> Plan:
    plan = data["plan"]
    return Plan(plan["issue"], plan["base_sha"], plan["diagnosis"], plan["risk"],
                tuple(Edit(**edit) for edit in plan["edits"]),
                tuple(tuple(command) for command in plan["tests"]), plan["provider"])


class PiProvider:
    """Embed Pi AgentSession through a versioned JSON boundary, without filesystem tools."""

    def __init__(self, allowed_paths: tuple[str, ...], mock_url: str | None = None,
                 readonly_paths: tuple[str, ...] = (), max_requests: int = MAX_REQUESTS):
        self.allowed_paths = allowed_paths
        self.readonly_paths = readonly_paths
        self.mock_url = mock_url
        self.max_requests = max_requests
        self.requests_used = 0
        self.evidence = {}
        self.baseline_context = None  # Explicit CLI preflight callback; default off.
        self.credential_source = "environment"
        self.config_file: Path | None = None
        self.memory_root: Path | None = None
        self.memory_index: Path | None = None
        self.memory_relations: Path | None = None  # UI evidence; model projection requires its own opt-in.
        self.memory_relations_context = False  # Separate explicit, journal-bound opt-in.
        self._memory_relations_guard = None
        self._structure_guard = None  # Fixed original-source gate; projection has its own opt-in.
        self._structure_context = None  # Separate explicit, journal-bound model projection callback.
        self._memory_scope = self._memory_common = None
        self._memory_index_sha = None  # Optional immutable UI-journal pin; CLI remains unchanged.
        self._revision = RevisionLedger()
        self._progress_callback = None
        self._cancel_event = None
        self._task_session_request = None
        self._investigation = None  # Only a controller turn binds this read-only scope.
        self._task_checkpoint = None
        self._continuation_feedback = None
        self._review_context = None
        self._experience_context = None
        self._maintenance_context = None
        self._memory_catalog = None
        self._load_attempt_id = None
        self._knowledge_context = None
        self._context_policy = None
        self._behavior_contract = None  # Only an original repair controller binds this.
        self._behavior_source = None  # Trusted original checkout; revisions use its common Git dir.
        self._validation_source = None  # Original readonly stage, never repair authority.

    def task_identity(self):
        from .provider_config import load_provider_config, resolve_model_reasoning
        from urllib.parse import urlsplit
        config = load_provider_config(self.config_file) if self.config_file and not self.mock_url else os.environ
        if self.mock_url:
            provider, model, endpoint = 'pi-loopback', 'mock-maintenance', self.mock_url
        elif self.credential_source == 'openclaw-readonly':
            provider, model, endpoint = 'pi-configured', 'deepseek-flash', 'https://api.deepseek.com'
        else:
            provider, model, endpoint = 'pi-configured', config.get('PI_MAINTENANCE_MODEL'), config.get('PI_MAINTENANCE_BASE_URL')
        if not isinstance(model, str) or not isinstance(endpoint, str):
            raise SafetyError('Pi task requires explicit provider/model identity')
        url = urlsplit(endpoint)
        if url.username or url.password or url.query or url.fragment:
            raise SafetyError('Pi task endpoint cannot contain credentials or query parameters')
        context = {'provider':provider,'model':model,'paths':list((*self.allowed_paths,*self.readonly_paths))}
        # Mock subprocesses strip provider settings. The omitted legacy context means off.
        level = resolve_model_reasoning({} if self.mock_url else config)['reasoning_effort']
        if level not in (None, 'off'):
            context['thinking_level'] = level
        return context, {'provider':provider,'model':model,'endpoint':[url.scheme,url.hostname,url.port,url.path],
                         'credential_source':self.credential_source}

    def effective_model_limits(self, **options):
        """Effective selected profile capacities; leaves historical identity intact."""
        from .provider_config import load_provider_config, resolve_model_limits
        config = load_provider_config(self.config_file) if self.config_file and not self.mock_url else os.environ
        if self.mock_url:
            config = {}
        if self.credential_source == 'openclaw-readonly' and not self.mock_url:
            config = {**config, 'PI_MAINTENANCE_MODEL': 'deepseek-flash',
                      'PI_MAINTENANCE_BASE_URL': 'https://api.deepseek.com'}
        return resolve_model_limits(config, mock_url=self.mock_url, **options)

    def plan(self, repository, issue: str, tests: tuple[tuple[str, ...], ...]) -> Plan:
        self._revision = RevisionLedger()  # No history reuse across tasks, even in the same repository.
        return self._generate(repository, issue, tests, None)

    def revise(self, repository, issue: str, tests: tuple[tuple[str, ...], ...], feedback: dict) -> Plan:
        return self._generate(repository, issue, tests, feedback)

    def reflect(self, source, guard):
        from .memory_reflection import reflect
        return reflect(self, source, guard)

    def propose_validation(self, repository, issue, tests=()):
        from .validation_provider import generate
        return generate(self, repository, issue, tests)

    def compact(self, source, guard):
        from .task_compaction import native_compact
        return native_compact(self,source,guard)

    def _generate(self, repository, issue: str, tests: tuple[tuple[str, ...], ...], feedback: dict | None) -> Plan:
        from .models import safe_context_path
        from .job_control import OperationStopped
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise OperationStopped('Planning stopped before credentials or process launch')
        self.evidence = {}
        from .issue_review import verified_review_context
        review = None
        if self._review_context is not None:
            review=verified_review_context(self._review_context,issue,repository.head,
                self._task_session_request['binding']['task_id'] if self._task_session_request else None)
        if self._structure_context and not self._structure_guard:
            raise SafetyError('Pi structure context requires a fixed UI structure binding')
        if self._structure_guard:
            self._structure_guard()  # Before every plan/revision/config/credential read.
        structure = self._structure_context(issue, repository.head, repository) if self._structure_context else None
        if self.memory_relations_context and not self._memory_relations_guard:
            raise SafetyError("Pi relation context requires a fixed UI memory binding")
        if self._memory_relations_guard:
            self._memory_relations_guard()  # Also recheck before a revision/config/credential read.
        memory = None
        if bool(self.memory_root) != bool(self.memory_index):
            raise SafetyError("Explicit memory root and index must be supplied together")
        if self.memory_index:
            if type(self.max_requests) is not int or not 1 <= self.max_requests <= MAX_REQUESTS or self.requests_used >= self.max_requests:
                raise SafetyError("Pi total model request budget exhausted")
            from .source_memory import planning_context
            if self._memory_scope is None:
                self._memory_scope, self._memory_common = repository.root, repository.common_dir
            if repository.common_dir != self._memory_common:
                raise SafetyError("Memory provider cannot move to another Git repository")
            memory = planning_context(self.memory_root,self._memory_scope,self.memory_index,issue)
            if self._memory_index_sha and memory["index_sha256"] != self._memory_index_sha:
                raise SafetyError("UI memory index changed before model invocation")
            if self.memory_relations_context:
                from .pi_memory_relations import with_relations
                memory = with_relations(memory, self._memory_relations_guard())
        if self.config_file and (self.mock_url or self.credential_source != "environment"):
            raise SafetyError("Local provider file cannot be combined with mock or existing-account credentials")
        if type(self.max_requests) is not int or not 1 <= self.max_requests <= MAX_REQUESTS or self.requests_used >= self.max_requests:
            raise SafetyError("Pi total model request budget exhausted")
        if not self.allowed_paths or len(self.allowed_paths) + len(self.readonly_paths) > LIMITS["source_context_max_files"]:
            raise SafetyError("Pi planning exceeds configured allowed-file count")
        files = []
        for path in (*self.allowed_paths, *self.readonly_paths):
            safe_context_path(path)
            content = repository.read(path)
            if len(content) > LIMITS["source_file_max_bytes"]:
                raise SafetyError("Source file exceeds Pi planning size limit")
            files.append({"path": path, "content": content.decode("utf-8"),
                          "before_sha256": digest(content), "editable": path in self.allowed_paths})
        revision = self._revision.prepare(repository, issue, tests, files, feedback) if feedback is not None else None
        from .pi_behavior import planning_context
        behavior = planning_context(self._behavior_contract, repository, issue, tests,
                                    {row['path'] for row in files}, source=self._behavior_source)
        baseline = None
        if self.baseline_context is not None and feedback is None:
            if self._task_session_request is not None or self._continuation_feedback is not None:
                raise SafetyError('Preflight context is not supported with durable/continuation sessions')
            baseline = self.baseline_context(repository, issue, tests, files)
        experience = self._experience_context(issue, repository) if self._experience_context else None
        knowledge = self._knowledge_context(issue, repository) if self._knowledge_context else None
        maintenance = self._maintenance_context(issue, repository) if self._maintenance_context else None
        continuation = None
        if self._continuation_feedback is not None:
            from .task_session import fingerprint, require
            controlled = self._continuation_feedback
            require(self._task_session_request and controlled['task_id']==self._task_session_request['binding']['task_id'] and
                    fingerprint(controlled['payload'])==controlled['payload_sha256'], 'Invalid controlled failure feedback')
            continuation = controlled['payload']
        load=None
        if self._memory_catalog:
            task_id=self._task_session_request['binding']['task_id'] if self._task_session_request else None
            load=self._memory_catalog.compile(self._load_attempt_id,issue,repository,files,
                 {'source':memory,'structure':structure,'experience':experience,'knowledge':knowledge,'maintenance':maintenance},
                 self.max_requests-self.requests_used,task_id)
        config = {}
        if self.config_file:
            from .provider_config import load_provider_config
            config = load_provider_config(self.config_file)
        entry = runtime_script("plan.mjs")
        node = shutil.which("node")
        if not node:
            raise SafetyError("Pi requires Node.js >=22.19 and npm ci in runtime/pi")
        command = [node, str(entry)]
        if self.mock_url:
            command += ["--mock-url", self.mock_url]
        elif self.credential_source == "openclaw-readonly":
            command += ["--credential-source", "openclaw-readonly"]
        self.evidence = {"pi_version": "1.0.4", "command": command, "status": "started",
                         "transport": "loopback-mock" if self.mock_url else "live"}
        from .provider_config import PROVIDER_FIELDS
        env = {k: v for k, v in os.environ.items()
               if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME",
                                "USERPROFILE", "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT"}
               or (not self.config_file and k in PROVIDER_FIELDS)}
        env.update(config)
        if self.mock_url:
            env = {k: v for k, v in env.items() if not k.startswith("PI_MAINTENANCE_")}
        from .provider_config import resolve_model_limits
        model_limits = resolve_model_limits(env, mock_url=self.mock_url, context_policy=self._context_policy)
        investigation_request = None
        if self._investigation is not None:
            from .pi_investigation import INVESTIGATION_PURPOSE
            if (self._task_session_request is None or
                    self._task_session_request['context'].get('purpose') != INVESTIGATION_PURPOSE):
                raise SafetyError('Investigation tools require the explicit versioned Pi task scope')
            binding = self._investigation
            investigation_request = {
                'schema_version': 1, 'scope_sha256': binding['scope_sha256'],
                'base_sha': binding['base_sha'], 'read_roots': list(binding['read_roots']),
                'max_calls': binding['max_calls'], 'max_output_bytes': binding['max_output_bytes'],
                'bridge': {'url': binding['bridge'].url, 'token': binding['bridge'].token}}
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env,
                                   start_new_session=os.name != "nt",
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        request = {"issue": issue, "base_sha": repository.head, "files": files, "tests": tests,
                   "feedback": feedback, "request_budget": self.max_requests - self.requests_used,
                   "revision_protocol": 1}
        if self._task_session_request is not None:
            request['task_session'] = self._task_session_request
        if investigation_request is not None:
            request['investigation'] = investigation_request
        if self._context_policy is not None:
            request['context_policy'] = self._context_policy
        if continuation is not None:
            request['continuation_feedback'] = continuation
        if review is not None:
            request['review_context'] = review
        if revision is not None:
            request["revision_context"] = revision
        if baseline is not None:
            request['baseline_context'] = baseline
        if behavior is not None:
            request['behavior_context'] = behavior
        if memory is not None:
            request["source_memory"] = memory
        if experience is not None:
            request['repository_experience'] = experience
        if knowledge is not None:
            request['repository_knowledge'] = knowledge
        if maintenance is not None:
            request['repository_maintenance'] = maintenance
        if structure is not None:
            request['structure_context'] = structure
        if load is not None:
            request['memory_load']=load
            request['controller_binding']=self._memory_catalog.frame(self._load_attempt_id)
        def failed(category, error=b""):
            progress = pi_evidence.progress_evidence(error, request["request_budget"])
            if progress and not pi_evidence.matches_model_limits(progress, model_limits):
                progress = None
            if progress and any(event.get('path') not in (*self.allowed_paths,*self.readonly_paths)
                    for event in progress['events'] if 'path' in event):
                progress = None
            self.evidence.update(progress or {})
            self.evidence.update(schema_version=1, status="failed", failure_category=category,
                                 consumption="unknown", requests=None, events_complete=False)
        try:
            if self._progress_callback is not None and self._cancel_event is not None:
                from .pi_live import communicate_live
                output, error = communicate_live(process, json.dumps(request).encode(),
                    request['request_budget'], self._progress_callback, self._cancel_event,
                    pi_evidence.PROCESS_TIMEOUT_SECONDS)
            else:
                output, error = process.communicate(json.dumps(request).encode(), timeout=pi_evidence.PROCESS_TIMEOUT_SECONDS)
        except BaseException as problem:
            stop_tree(process)
            partial = getattr(problem, 'partial', b'')
            if self._progress_callback is None:
                try:
                    _, partial = process.communicate(timeout=10)
                except Exception:
                    partial = b""
            failed("parent_timeout" if isinstance(problem, subprocess.TimeoutExpired) else "parent_interrupted", partial)
            self.evidence["returncode"] = process.returncode
            if isinstance(problem, subprocess.TimeoutExpired):
                raise SafetyError("Pi planning timed out; request consumption remains unknown") from None
            raise
        finally:
            # communicate_live drains its reader threads but Popen still owns
            # the pipe objects. Close these handles after success or stop.
            for stream in (process.stdin,process.stdout,process.stderr):
                if stream is not None:stream.close()
        # A provider response must not echo its credential into plans, logs or reports.
        secret = env.get("PI_MAINTENANCE_API_KEY", "")
        if secret:
            output = output.replace(secret.encode(), b"[REDACTED]")
        try:
            if len(output) > LIMITS["planning_packet_max_bytes"]:
                raise SafetyError("Pi result exceeds its size limit")
            data = json.loads(output)
            if not isinstance(data, dict) or type(data.get("protocol_version")) is not int or data["protocol_version"] != 1:
                raise SafetyError("Unsupported Pi result protocol")
            evidence = pi_evidence.validate_evidence(data["evidence"], request["request_budget"])
            if not pi_evidence.matches_model_limits(evidence, model_limits):
                raise SafetyError('Pi output limit receipt differs from the selected model configuration')
            if evidence.get('behavior_contract_sha256') != (behavior['contract_sha256'] if behavior else None):
                raise SafetyError('Pi behavior expectations receipt differs from the original contract')
            if evidence.get('memory_load_sha256')!=(load['context_sha256'] if load else None):
                raise SafetyError('Pi unified memory load receipt differs')
            if evidence.get('memory_task_type')!=(load['task_type'] if load else None):
                raise SafetyError('Pi controlled task route differs')
            if evidence.get('maintenance_context_sha256')!=(maintenance['context_sha256'] if maintenance else None):
                raise SafetyError('Pi maintenance receipt differs from original selection')
            if evidence.get('maintenance_selection_sha256')!=(maintenance['selection_sha256'] if maintenance else None):
                raise SafetyError('Pi maintenance selection receipt differs')
            from .task_session import fingerprint
            if evidence.get('context_policy_sha256') != (fingerprint(self._context_policy) if self._context_policy is not None else None):
                raise SafetyError('Pi context policy receipt differs')
            if investigation_request is not None:
                if (evidence.get('purpose') != 'repair-investigation-v1'
                        or evidence.get('investigation_scope_sha256') != investigation_request['scope_sha256']):
                    raise SafetyError('Pi investigation receipt differs from the original task scope')
            elif 'purpose' in evidence or 'investigation_scope_sha256' in evidence:
                raise SafetyError('Reflection evidence cannot authorize a repair plan')
            if any(event.get('path') not in (*self.allowed_paths,*self.readonly_paths)
                    for event in evidence['events'] if 'path' in event):
                raise SafetyError('Pi event path is outside the authorized snapshot')
            if evidence.get("memory_context_sha256") != (memory["context_sha256"] if memory else None):
                raise SafetyError("Pi memory receipt does not match verified context")
            if evidence.get('structure_context_sha256') != (structure['context_sha256'] if structure else None):
                raise SafetyError('Pi structure receipt does not match verified context')
            if evidence.get('experience_context_sha256') != (experience['context_sha256'] if experience else None):
                raise SafetyError('Pi experience receipt does not match verified context')
            if evidence.get('knowledge_context_sha256') != (knowledge['context_sha256'] if knowledge else None):
                raise SafetyError('Pi knowledge receipt does not match verified context')
            if evidence.get('revision_context_sha256') != (digest(encoded(revision)) if revision else None):
                raise SafetyError('Pi revision receipt does not match verified task context')
            if evidence.get('continuation_feedback_sha256') != (digest(encoded(continuation)) if continuation else None):
                raise SafetyError('Pi continuation failure receipt does not match original controller')
            if evidence.get('review_context_sha256') != (review['context_sha256'] if review else None):
                raise SafetyError('Pi review receipt does not match the current controlled review')
            if evidence.get('baseline_context_sha256') != (digest(encoded(baseline)) if baseline else None):
                raise SafetyError('Pi baseline receipt does not match approved preflight')
            if data.get("status") != evidence["status"] or data["status"] not in {"completed", "failed"}:
                raise SafetyError("Pi result status mismatch")
            if process.returncode or data["status"] == "failed":
                if data["status"] != "failed":
                    raise SafetyError("Pi exit contradicts completion")
                if 'terminal_count' in evidence:
                    snapshot_sha = digest(json.dumps(files, ensure_ascii=False, separators=(',', ':')).encode())
                    if (process.returncode != 1 or set(data) != {'protocol_version','status','evidence'}
                            or evidence.get('source_snapshot_sha256') != snapshot_sha
                            or evidence.get('transport') != ('loopback-mock' if self.mock_url else 'live')):
                        raise SafetyError('Terminal failure receipt differs from the current process or snapshot')
                    self.evidence = {**evidence, 'command':command, 'returncode':process.returncode}
                    self.requests_used += evidence['requests']
                    self.evidence.update(total_requests_used=self.requests_used, total_request_budget=self.max_requests)
                    raise pi_evidence.CountedPlanningFailure(evidence, request['request_budget'])
                self.evidence = {**evidence, "command":command, "returncode":process.returncode}
                raise SafetyError("Pi planning failed; inspect safe runtime evidence")
            from .pi_edits import verify_materialized_plan
            verify_materialized_plan(data, files)
            verified_reads(data.get('planning_summary'), files)
            if self._task_session_request is not None:
                checkpoint = data.get('task_checkpoint')
                snapshot_sha = digest(json.dumps(files,ensure_ascii=False,separators=(',', ':')).encode())
                if (not isinstance(checkpoint, dict) or checkpoint.get('snapshot_sha256') != snapshot_sha
                        or evidence.get('source_snapshot_sha256') != snapshot_sha):
                    raise SafetyError('Pi task checkpoint does not match the current source snapshot')
            plan = decode_plan(data)
        except pi_evidence.CountedPlanningFailure:
            raise
        except (ValueError, KeyError, TypeError, SafetyError):
            if self.evidence.get("status") != "failed":
                failed("protocol_error", error)
                self.evidence["returncode"] = process.returncode
            raise SafetyError("Pi planning failed; inspect safe runtime evidence") from None
        self.evidence = {**evidence, "command": command, "returncode": process.returncode}
        if load is not None:self.evidence['memory_load']=load
        if continuation is not None:
            from .task_feedback import TaskFailureFeedback
            self.evidence['continuation_feedback_ref'] = TaskFailureFeedback.reference(self._continuation_feedback)
            self.evidence['repeated_failed_patch'] = ({e.path:digest(e.content.encode()) for e in plan.edits} ==
                                                     continuation['proposed_content_hashes'])
        self.requests_used += evidence["requests"]
        self.evidence["total_requests_used"] = self.requests_used
        self.evidence["total_request_budget"] = self.max_requests
        if memory is not None:
            self.evidence["memory_citations"] = [{k:v for k,v in row.items() if k != "excerpt"} for row in memory["hits"]]
        if self.config_file:
            self.evidence["credential_source"] = "explicit-local-file"
        if self.memory_relations_context:
            self._memory_relations_guard()  # A changed source during inference cannot return a usable plan.
        if self._structure_guard:
            self._structure_guard()
        if experience is not None:
            current = self._experience_context(issue, repository)
            if current['context_sha256'] != experience['context_sha256']:
                raise SafetyError('Experience changed during inference')
            self.evidence['experience_citations'] = [{k: hit[k] for k in ('id','payload_sha256','review_version')}
                                                   for hit in experience['hits']]
        if knowledge is not None:
            current = self._knowledge_context(issue, repository)
            if current['context_sha256'] != knowledge['context_sha256']:
                raise SafetyError('Knowledge changed during inference')
            self.evidence['knowledge_citations'] = [{k:hit[k] for k in ('id','payload_sha256','review_version')}
                                                   for hit in knowledge['hits']]
        if structure is not None:
            current = self._structure_context(issue, plan.base_sha, repository)
            if current['context_sha256'] != structure['context_sha256']:
                raise SafetyError('Pi structure context changed during inference')
            self.evidence['structure_provenance'] = {k: structure[k] for k in
                ('manifest_sha256', 'graph_sha256', 'query_sha256', 'planning_base_sha', 'planning_files_sha256', 'hits', 'edges', 'related_nodes',
                 'structural_claims_only', 'runtime_authorized')}
            self.evidence['structure_provenance']['source_head'] = structure['source']['head']
        self._revision.remember(repository, issue, tests, files, plan, data.get('planning_summary'))
        self.evidence['planning_summary'] = data.get('planning_summary', {'reads': []})
        self._task_checkpoint = data.get('task_checkpoint') if self._task_session_request is not None else None
        return plan


class SavedPlanProvider:
    """Resume the exact reviewed plan instead of asking a stochastic model to regenerate it."""

    def __init__(self, path: Path):
        self.data = json.loads(path.read_text(encoding="utf-8"))
        self.planning_origin = {"plan_sha256": self.data.get("plan_sha256"),
                                "provider_evidence": self.data.get("provider_evidence", {})}
        self.evidence = {"requests": 0, "transport": "saved-plan", "new_model_requests": False}

    def plan(self, repository, issue: str, tests: tuple[tuple[str, ...], ...]) -> Plan:
        plan = decode_plan(self.data)
        if self.data.get("plan_sha256") != plan.fingerprint:
            raise SafetyError("Saved plan fingerprint does not match its contents")
        if plan.issue != issue or plan.base_sha != repository.head or plan.tests != tests:
            raise SafetyError("Saved plan no longer matches issue, repository baseline or tests")
        return plan
