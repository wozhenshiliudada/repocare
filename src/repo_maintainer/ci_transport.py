"""Internal CI read primitives and cooperative time/size/request budgets."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import BinaryIO, Callable, Protocol


_ERRORS = {
    'authentication_required': (401, 'GitHub authentication is required.'),
    'permission_denied': (403, 'GitHub read permission is insufficient.'),
    'not_found': (404, 'The resource is absent or inaccessible.'),
    'rate_limited': (429, 'GitHub read rate is limited; refresh later.'),
    'timeout': (504, 'The bounded CI read timed out.'),
    'network_error': (502, 'The CI source could not be read.'),
    'invalid_response': (502, 'The CI source returned invalid evidence.'),
    'identity_mismatch': (409, 'CI evidence identity changed or does not match.'),
    'membership_unverified': (409, 'Job membership could not be established.'),
    'size_limit': (413, 'The CI evidence read reached a size limit.'),
    'request_limit': (429, 'The CI observation reached its request limit.'),
    'redirect_rejected': (502, 'The log download destination was rejected.'),
    'logs_unavailable': (404, 'Logs are not available for this observation.'),
    'read_busy': (429, 'CI reading is busy; refresh explicitly later.'),
}


class CiReadError(Exception):
    """Only fixed messages may leave a transport, never raw HTTP exceptions."""

    def __init__(self, code: str):
        self.code = code if code in _ERRORS else 'network_error'
        self.http_status, message = _ERRORS[self.code]
        super().__init__(message)

    def record(self, stage: str) -> dict:
        return {'code': self.code, 'stage': stage, 'message': str(self),
                'http_status': self.http_status}


@dataclass(frozen=True)
class CiLimits:
    json_response_bytes: int = 512 * 1024
    jobs_per_page: int = 100
    max_pages: int = 5
    max_jobs: int = 500
    max_steps: int = 1000
    log_bytes: int = 1024 * 1024
    excerpt_characters: int = 65536
    line_characters: int = 4096
    response_bytes: int = 2 * 1024 * 1024
    socket_timeout_seconds: int = 10
    deadline_seconds: int = 60
    http_requests: int = 12

    def __post_init__(self):
        # Server-side experiments may lower limits, never expand the draft caps.
        for key, maximum in _LIMIT_CAPS.items():
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError('Invalid internal CI limits')
        if self.response_bytes < 4096:
            raise ValueError('CI response limit cannot hold the evidence envelope')

    def public(self):
        return asdict(self)


_LIMIT_CAPS = {field: value.default for field, value in CiLimits.__dataclass_fields__.items()}


class CiReadBudget:
    def __init__(self, limits: CiLimits, clock: Callable[[], float] = time.monotonic):
        self.limits, self.clock = limits, clock
        self.deadline = clock() + limits.deadline_seconds
        self.requests = 0

    def remaining(self) -> float:
        value = self.deadline - self.clock()
        if value <= 0:
            raise CiReadError('timeout')
        return value

    def timeout(self) -> float:
        return min(self.limits.socket_timeout_seconds, self.remaining())

    def begin_request(self):
        self.remaining()
        if self.requests >= self.limits.http_requests:
            raise CiReadError('request_limit')
        self.requests += 1


@dataclass(frozen=True)
class CiJsonPage:
    data: object
    has_next: bool = False
    sensitive_values: tuple[str, ...] = field(default=(), repr=False, compare=False)


@dataclass(frozen=True)
class CiLogCapture:
    raw: bytes = field(repr=False)
    truncated: bool
    remote_total_bytes: int | None = None
    sensitive_values: tuple[str, ...] = field(default=(), repr=False, compare=False)


class CiTransport(Protocol):
    """Paths are constructed by the reader; no user-provided download URLs.

    The reader charges each API request. An adapter must additionally
    charge download redirects, enforce bounded body reads, and reset socket
    timeout to budget.timeout() before every blocking network operation.
    """

    def get_json(self, path: str, budget: CiReadBudget) -> CiJsonPage: ...

    def get_log(self, path: str, budget: CiReadBudget) -> CiLogCapture: ...


def capture_prefix(stream: BinaryIO, limit: int, budget: CiReadBudget) -> CiLogCapture:
    """Read at most limit + one sentinel byte; never close a caller-owned stream.

    The stream adapter owns socket timeouts. This helper cannot interrupt a
    stream that violates its blocking/size contract.
    """
    if type(limit) is not int or not 1 <= limit <= budget.limits.log_bytes:
        raise ValueError('Invalid capture limit')
    parts, length = [], 0
    while length <= limit:
        budget.remaining()
        requested = min(65536, limit + 1 - length)
        chunk = stream.read(requested)
        budget.remaining()
        if not isinstance(chunk, bytes) or len(chunk) > requested:
            raise CiReadError('invalid_response')
        if not chunk:
            return CiLogCapture(b''.join(parts), False, length)
        parts.append(chunk)
        length += len(chunk)
    return CiLogCapture(b''.join(parts)[:limit], True)


def decode_json(raw: bytes, limits: CiLimits) -> object:
    if not isinstance(raw, bytes):
        raise CiReadError('invalid_response')
    if len(raw) > limits.json_response_bytes:
        raise CiReadError('size_limit')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError('non-finite JSON value')

    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                           parse_constant=invalid_constant)
        # Reject escaped lone surrogates and overflowed numeric literals too.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        return value
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise CiReadError('invalid_response') from None
