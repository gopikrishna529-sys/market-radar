"""json.dumps that never writes NaN / Infinity (browsers reject them): they become null."""
import json
import math


def _clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):   # numpy scalars
        try:
            return _clean(o.item())
        except Exception:
            return o
    return o


def dumps(o, **kw):
    return json.dumps(_clean(o), allow_nan=False, **kw)
