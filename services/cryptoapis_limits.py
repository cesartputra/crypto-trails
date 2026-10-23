"""Free-plan credit estimates and pacing shared by every event loop in this process."""
import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_CEILING
from math import isfinite
import threading
import time

PAGE_SIZE = 1
MAX_REQUESTS = 6
MAX_CREDITS_PER_SCAN = 2000
FREE_MONTHLY_CREDITS = 100000
SOFT_CREDITS_PER_SECOND = 100
HARD_CREDITS_PER_SECOND = 1000
MAX_RETRIES = 1

_BASE_CREDITS = {
    'transactions': {'ETHEREUM': 120, 'BSC': 300, 'POLYGON': 240, 'ARBITRUM': 192,
                     'OPTIMISM': 168, 'BASE': 144, 'BTC': 110, 'SOLANA': 90},
    'tokens-transfers': {'ETHEREUM': 220, 'BSC': 550, 'POLYGON': 440,
                        'ARBITRUM': 352, 'OPTIMISM': 308, 'BASE': 264},
}
_inflight_lock = threading.Lock()
_state_lock = threading.Lock()
_next_allowed = 0.0
_sleep = asyncio.sleep
_monotonic = time.monotonic


def estimate_request_credits(chain, category):
    """Reserve one returned result, including the isolated soft-limit penalty."""
    try:
        base = _BASE_CREDITS[category][chain]
    except KeyError:
        raise ValueError('Unsupported Crypto APIs chain/category') from None
    if base <= SOFT_CREDITS_PER_SECOND:
        return base
    charge = SOFT_CREDITS_PER_SECOND + (base - SOFT_CREDITS_PER_SECOND) * Decimal('2.1')
    return int(charge.to_integral_value(rounding=ROUND_CEILING))


def defer_requests(seconds):
    """Extend the shared cooldown even when the current caller will not retry."""
    global _next_allowed
    if not isfinite(seconds) or seconds < 0:
        raise ValueError('Crypto APIs cooldown must be finite and nonnegative')
    with _state_lock:
        _next_allowed = max(_next_allowed, _monotonic() + seconds)


@asynccontextmanager
async def request_slot():
    """Serialize attempts and leave 1.2 seconds after the preceding response ends."""
    global _next_allowed
    while not _inflight_lock.acquire(blocking=False):
        await _sleep(0.05)
    try:
        while True:
            with _state_lock:
                delay = _next_allowed - _monotonic()
            if delay <= 0:
                break
            await _sleep(delay)
        yield
    finally:
        with _state_lock:
            _next_allowed = max(_next_allowed, _monotonic() + 1.2)
        _inflight_lock.release()
