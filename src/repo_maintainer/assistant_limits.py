"""Conversation storage/transport byte bounds are separate from declared token counts."""
from .maintenance_limits import LIMITS

MAX_ASSISTANT_NATIVE_BYTES = LIMITS['native_session_max_bytes']
MAX_ASSISTANT_TEXT_BYTES = min(32 * 1024**2, MAX_ASSISTANT_NATIVE_BYTES // 8)
MAX_ASSISTANT_REPLY_BYTES = 64 * 1024**2
MAX_ASSISTANT_CATALOG_BYTES = 512 * 1024**2
MAX_ASSISTANT_HISTORY_BYTES = 1500000
# A single unknown turn must not reserve the whole durable model allowance.
MAX_ASSISTANT_REQUESTS_PER_TURN = 8
