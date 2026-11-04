"""Narrow GCM-authenticated draft-PR transport. No push, merge or Issue writes.

Callers own durable intent and approval. Transport never retries a POST, including
an HTTP/JSON failure after GitHub may already have performed the write.
"""
from __future__ import annotations

from urllib.parse import quote

from .github_connection import GitHubConnection, github_url, branch_name
from .models import SafetyError


class GitHubPullRequests(GitHubConnection):
    def pulls_for_head(self, repository, head):
        slug, _ = github_url('https://github.com/'+repository)
        branch_name(head)
        prefix = f'/repos/{slug}/pulls?state=all&head='+quote(slug.split('/')[0]+':'+head, safe='')
        rows = []
        for page in range(1, 4):
            result = self._request(prefix+f'&per_page=30&page={page}')
            if (not isinstance(result, list) or len(result) > 30
                    or any(not isinstance(row, dict) for row in result)):
                raise SafetyError('Malformed pull-request page')
            rows.extend(result)
            if len(result) < 30:
                return rows
        raise SafetyError('Pull-request pagination incomplete; no write/replay')

    def create_draft(self, repository, payload):
        slug, _ = github_url('https://github.com/'+repository)
        if (not isinstance(payload, dict) or set(payload) != {'title', 'body', 'head', 'base', 'draft'}
                or payload['draft'] is not True
                or any(not isinstance(payload[key], str) for key in ('title', 'body', 'head', 'base'))
                or not 1 <= len(payload['title'].encode()) <= 256
                or not 1 <= len(payload['body'].encode()) <= 32768):
            raise SafetyError('Only a bounded exact draft-PR payload is supported')
        branch_name(payload['head'])
        branch_name(payload['base'])
        if payload['head'] == payload['base']:
            raise SafetyError('PR head must differ from base')
        return self._request(f'/repos/{slug}/pulls', payload=payload)
