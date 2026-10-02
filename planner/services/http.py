import hashlib
from functools import lru_cache

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter


@lru_cache(maxsize=1)
def get_session():
    """Shared session so connections to the routing/geocoding APIs are kept alive."""
    session = requests.Session()
    session.headers["User-Agent"] = settings.FUEL_PLANNER["HTTP_USER_AGENT"]
    session.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=16))
    return session


def make_cache_key(prefix, *parts):
    """Case/whitespace-insensitive cache key that is safe for any cache backend."""
    raw = "|".join(" ".join(str(p).lower().split()) for p in parts)
    return f"{prefix}:{hashlib.sha1(raw.encode()).hexdigest()}"
