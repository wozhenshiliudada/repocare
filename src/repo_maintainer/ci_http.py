"""CI-only HTTP adapter; trusted exact log hosts must be supplied server-side.

The deadline is cooperative: DNS and other blocking OS calls may overrun it.
There are no retries,
implicit proxy settings, cookies, automatic redirects or on-disk caches.
"""
from __future__ import annotations

import http.client
import re
from dataclasses import dataclass, replace
from urllib.parse import parse_qs, urlsplit

from .ci_transport import (CiJsonPage, CiReadError, capture_prefix, decode_json)
from .github_connection import github_url


_PREFIX = r'/repos/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)'
_PATH = re.compile(_PREFIX + r'(?:/actions/runs/([1-9][0-9]{0,15})/attempts/([1-9][0-9]{0,4})'
                   r'(/jobs\?per_page=([1-9][0-9]{0,2})&page=([1-9][0-9]?))?'
                   r'|/actions/jobs/([1-9][0-9]{0,15})(/logs)?)?')


@dataclass(frozen=True)
class CiDownloadPolicy:
    """Trusted server-side exact hosts; never populated from a request or log."""
    hosts: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.hosts, tuple) or len(self.hosts) > 16:
            raise ValueError('Invalid internal CI download policy')
        for host in self.hosts:
            if (not isinstance(host, str) or len(host) > 253 or host != host.lower()
                    or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host)
                    or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                           for label in host.split('.'))
                    or not (host == 'results-receiver.actions.githubusercontent.com'
                            or host.endswith('.blob.core.windows.net'))):
                raise ValueError('Invalid internal CI download host')

    def destination(self, url):
        try:
            if (not isinstance(url, str) or not url.isascii() or len(url) > 8192
                    or any(ord(c) <= 32 or ord(c) == 127 for c in url)):
                raise ValueError()
            parsed = urlsplit(url)
            if (parsed.scheme != 'https' or parsed.hostname not in self.hosts
                    or parsed.username is not None or parsed.password is not None
                    or parsed.port not in (None, 443) or parsed.fragment
                    or not parsed.query or not parsed.path.startswith('/')
                    or parsed.netloc not in (parsed.hostname, parsed.hostname + ':443')):
                raise ValueError()
            return parsed.hostname, parsed.path + '?' + parsed.query
        except (ValueError, TypeError):
            raise CiReadError('redirect_rejected') from None


class _DeadlineStream:
    def __init__(self, connection, response, budget):
        self.connection, self.response, self.budget = connection, response, budget

    def read(self, n):
        remaining = self.budget.timeout()
        sock = self.connection.sock
        if sock is None:
            sock = getattr(getattr(getattr(self.response, 'fp', None), 'raw', None), '_sock', None)
        if sock is not None:
            sock.settimeout(remaining)
        # read1 does one underlying buffered read, avoiding a slow stream filling
        # a large read(n) forever while staying below the per-socket timeout.
        chunk = getattr(self.response, 'read1', self.response.read)(n)
        self.budget.remaining()
        return chunk


class CiHttpTransport:
    def __init__(self, connection, *, policy=None, https_factory=None):
        self.connection = connection
        self.policy = policy or CiDownloadPolicy()
        self.https_factory = https_factory or (lambda host, timeout:
            http.client.HTTPSConnection(host, timeout=timeout))

    @staticmethod
    def _path(path, *, logs=False):
        match = _PATH.fullmatch(path) if isinstance(path, str) else None
        if not match or bool(match[9]) != logs:
            raise CiReadError('invalid_response')
        github_url(f'https://github.com/{match[1]}/{match[2]}')
        for group in (3, 8):
            if match[group] and int(match[group]) > 2**53-1:
                raise CiReadError('invalid_response')
        if ((match[4] and int(match[4]) > 10000)
                or (match[6] and int(match[6]) > 100)
                or (match[7] and int(match[7]) > 5)):
            raise CiReadError('invalid_response')

    def _token(self, budget):
        remaining = budget.remaining()
        try:
            token = self.connection.credential(timeout=min(20, remaining))
            budget.remaining()
            if not isinstance(token, str) or not token or len(token) > 65536 or any(c.isspace() for c in token):
                raise ValueError()
            return token
        except (TimeoutError, CiReadError):
            raise CiReadError('timeout') from None
        except Exception:
            budget.remaining()
            raise CiReadError('authentication_required') from None

    @staticmethod
    def _headers(token):
        return {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                'User-Agent': 'RepoCare-CI-evidence', 'X-GitHub-Api-Version': '2022-11-28',
                'Accept-Encoding': 'identity'}

    def _exchange(self, host, path, headers, budget, *, redirect=False, logs=False):
        connection = None
        try:
            connection = self.https_factory(host, budget.timeout())
            budget.remaining()
            connection.request('GET', path, headers=headers)
            budget.remaining()
            if connection.sock is not None:
                connection.sock.settimeout(budget.timeout())
            response = connection.getresponse()
            budget.remaining()
            fields = {}
            for key, value in response.getheaders():
                key = key.lower()
                if key in fields and key in {'location', 'link', 'content-length', 'content-encoding'}:
                    raise CiReadError('invalid_response')
                fields[key] = value
            status = response.status
            if redirect and status == 302:
                return fields.get('location')
            if 300 <= status < 400:
                raise CiReadError('redirect_rejected')
            if status != 200:
                if status == 401:
                    code = 'authentication_required'
                elif status == 429 or status == 403 and (
                        fields.get('x-ratelimit-remaining') == '0' or fields.get('retry-after')):
                    code = 'rate_limited'
                elif status == 403:
                    code = 'permission_denied'
                elif status in (404, 410):
                    code = 'logs_unavailable' if logs or redirect else 'not_found'
                else:
                    code = 'network_error'
                raise CiReadError(code)
            if redirect:
                raise CiReadError('invalid_response')
            if fields.get('content-encoding', 'identity').lower() != 'identity':
                raise CiReadError('invalid_response')
            length = fields.get('content-length')
            if length is not None and (not length.isascii() or not length.isdecimal() or len(length) > 20):
                raise CiReadError('invalid_response')
            length = int(length) if length is not None else None
            stream = _DeadlineStream(connection, response, budget)
            if logs:
                captured = capture_prefix(stream, budget.limits.log_bytes, budget)
                if length is not None and (length < len(captured.raw)
                        or not captured.truncated and length != len(captured.raw)):
                    raise CiReadError('invalid_response')
                return captured
            raw = bytearray()
            limit = budget.limits.json_response_bytes
            while len(raw) <= limit:
                chunk = stream.read(min(65536, limit+1-len(raw)))
                if not isinstance(chunk, bytes) or len(chunk) > min(65536, limit+1-len(raw)):
                    raise CiReadError('invalid_response')
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) > limit:
                raise CiReadError('size_limit')
            if length is not None and length != len(raw):
                raise CiReadError('invalid_response')
            return decode_json(bytes(raw), budget.limits), fields.get('link', '')
        except CiReadError:
            raise
        except TimeoutError:
            raise CiReadError('timeout') from None
        except (OSError, http.client.HTTPException, ValueError, UnicodeError):
            raise CiReadError('network_error') from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass

    @staticmethod
    def _next(link, path):
        if len(link) > 8192:
            raise CiReadError('invalid_response')
        found = False
        for item in link.split(','):
            match = re.fullmatch(r'\s*<([^<>]+)>;\s*rel="([^"]+)"\s*', item)
            if not match:
                if item.strip():
                    raise CiReadError('invalid_response')
                continue
            if 'next' not in match[2].split():
                continue
            current, target = urlsplit(path), urlsplit(match[1])
            query, expected = parse_qs(target.query, keep_blank_values=True), parse_qs(current.query, keep_blank_values=True)
            if (found or target.scheme != 'https' or target.netloc != 'api.github.com'
                    or target.fragment or target.path != current.path
                    or set(query) != {'page', 'per_page'} or 'page' not in expected
                    or query['per_page'] != expected['per_page']
                    or query['page'] != [str(int(expected['page'][0])+1)]):
                raise CiReadError('invalid_response')
            found = True
        return found

    def get_json(self, path, budget):
        self._path(path)
        token = self._token(budget)
        data, link = self._exchange('api.github.com', path, self._headers(token), budget)
        return CiJsonPage(data, self._next(link, path), (token,))

    def get_log(self, path, budget):
        self._path(path, logs=True)
        if not self.policy.hosts:
            raise CiReadError('redirect_rejected')
        token = self._token(budget)
        destination = self._exchange('api.github.com', path, self._headers(token), budget, redirect=True)
        host, download_path = self.policy.destination(destination)
        budget.begin_request()
        capture = self._exchange(host, download_path, {'Accept-Encoding': 'identity',
                                 'User-Agent': 'RepoCare-CI-evidence'}, budget, logs=True)
        return replace(capture, sensitive_values=(token,))
