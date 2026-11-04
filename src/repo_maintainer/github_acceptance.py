"""Narrow delivery-evidence GETs and one fixed completed-Issue PATCH.

No merge, workflow dispatch/rerun, reopen, comment or label operations. Original
GitHubConnection.api remains its existing read-only whitelist.
"""
from __future__ import annotations

import re
from .github_pr import GitHubPullRequests
from .github_connection import github_url
from .models import SafetyError
from .issue_intake import project_issue


class GitHubAcceptance(GitHubPullRequests):
    def read(self, path):
        prefix = r'/repos/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)'
        extra = re.fullmatch(prefix + r'(?:/pulls/[1-9][0-9]{0,9}'
            r'|/pulls\?state=all&head=[A-Za-z0-9-]+%3Aintegration%2F[a-f0-9]{32}&per_page=30&page=[1-3]'
            r'|/compare/[a-f0-9]{40}\.\.\.[a-f0-9]{40}\?per_page=1'
            r'|/actions/runs\?branch=[A-Za-z0-9_.%/-]+&head_sha=[a-f0-9]{40}&per_page=30&page=[1-3]'
            r'|/actions/runs/[1-9][0-9]{0,19}/attempts/[1-9][0-9]{0,9}/jobs\?per_page=30&page=[1-3])', path)
        if extra:
            github_url('https://github.com/'+extra[1]+'/'+extra[2])
            return self._request(path)
        return self.api(path)

    def close_completed(self, repository, number, expected_actor, expected_issue):
        slug, _ = github_url('https://github.com/'+repository)
        if type(number) is not int or not 1 <= number <= 9999999999:
            raise SafetyError('Exact Issue number required')
        if (not isinstance(expected_actor, dict) or type(expected_actor.get('id')) is not int
                or not isinstance(expected_actor.get('login'), str)):
            raise SafetyError('Exact approved GitHub actor required')
        token = self.credential()
        try:
            actor = self._request('/user', _token=token)
            if (actor.get('id') != expected_actor['id']
                    or str(actor.get('login', '')).casefold() != expected_actor['login']):
                raise SafetyError('GitHub account changed; no Issue close sent')
            current = self._request(f'/repos/{slug}/issues/{number}', _token=token)
            if project_issue(current, slug.casefold(), number) != expected_issue or expected_issue.get('state') != 'open':
                raise SafetyError('Issue changed at the last bound-credential read; no close sent')
            return self._request(f'/repos/{slug}/issues/{number}', method='PATCH', _token=token,
                                 payload={'state': 'closed', 'state_reason': 'completed'})
        finally:
            token = None
