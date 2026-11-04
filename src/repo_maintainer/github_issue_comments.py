"""Fixed Issue-comments endpoints; no update/delete/labels/close or POST retries."""
from .github_connection import GitHubConnection, github_url
from .issue_progress import CommentPermissionDenied, _positive, require


class GitHubIssueComments(GitHubConnection):
    def comment_actor(self):
        actor = self._request('/user')
        require(isinstance(actor, dict), 'GitHub comment account unavailable')
        _positive(actor.get('id'))
        require(isinstance(actor.get('login'), str) and 0 < len(actor['login']) <= 100,
                'GitHub comment account unavailable')
        return {'id': actor['id'], 'login': actor['login']}

    @staticmethod
    def _scope(repository, number):
        slug, _ = github_url('https://github.com/'+repository)
        _positive(number)
        return slug, f'/repos/{slug}/issues/{number}'

    def _preflight(self, repository, number, actor_id, token, expected_issue_state):
        slug, path = self._scope(repository, number)
        _positive(actor_id)
        require(expected_issue_state in {'open', 'closed'}, 'Exact approved Issue state required')
        actor = self._request('/user', _token=token)
        require(isinstance(actor, dict) and actor.get('id') == actor_id, 'Approved GitHub account changed')
        repo = self._request('/repos/'+slug, _token=token)
        require(isinstance(repo, dict) and str(repo.get('full_name', '')).casefold() == slug.casefold(),
                'Exact repository permission response required')
        if repo.get('permissions', {}).get('push') is not True:
            raise CommentPermissionDenied('First version requires repository write access for progress comments')
        issue = self._request(path, _token=token)
        require(isinstance(issue, dict) and issue.get('number') == number and 'pull_request' not in issue
                and issue.get('state') == expected_issue_state and issue.get('locked') is False
                and str(issue.get('html_url', '')).casefold() == 'https://github.com/'+slug.casefold()+'/issues/'+str(number),
                'Exact approved unlocked Issue state required; no state changes or PR comments')

    def preflight(self, repository, number, actor_id, *, expected_issue_state='open'):
        token = self.credential()
        try:
            self._preflight(repository, number, actor_id, token, expected_issue_state)
        finally:
            token = None

    def create_comment(self, repository, number, body, actor_id, *, expected_issue_state='open'):
        _, path = self._scope(repository, number)
        require(isinstance(body, str) and 0 < len(body.encode()) <= 24000, 'Bounded approved comment required')
        token = self.credential()
        try:
            # Bind final account check and POST to the same credential.
            self._preflight(repository, number, actor_id, token, expected_issue_state)
            return self._request(path+'/comments', payload={'body': body}, _token=token)
        finally:
            token = None

    def comments(self, repository, number):
        _, path = self._scope(repository, number)
        rows = []
        for page in range(1, 4):
            result = self._request(path+f'/comments?per_page=100&page={page}')
            require(isinstance(result, list) and len(result) <= 100
                    and all(isinstance(r, dict) for r in result), 'Malformed comment page')
            rows.extend(result)
            if len(result) < 100:
                return rows
        require(False, 'Comment pagination incomplete; preserve unknown, never repeat POST')
