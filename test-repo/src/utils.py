"""Utility functions."""

def log(message):
    print(f"[LOG] {message}")

def format_date(d):
    return d.strftime("%Y-%m-%d")

def parse_args(args):
    """Parse command line arguments."""
    return {"verbose": "-v" in args}

def log_request(req, status):
    """Log API request details."""
    log(f"Request: {req} -> {status}")

def safe_get(d, key, default=None):
    """Safely get a value from a dict."""
    return d.get(key, default)

def format_response(data, status=200):
    """Format an API response with status."""
    return {"data": data, "status": status}

def truncate(text, length=100):
    """Truncate text to specified length."""
    return text[:length] if len(text) > length else text

def deep_merge(base, override):
    """Deep merge two dictionaries."""
    result = dict(base)
    for k, v in override.items():
        result[k] = v
    return result

def timer(func):
    """Decorator to measure function execution time."""
    import time
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        log(f"{func.__name__} took {time.time()-start:.3f}s")
        return result
    return wrapper

def histogram(values, bins=10):
    """Compute a simple histogram of values."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    width = (hi - lo) / bins or 1
    counts = [0] * bins
    for v in values:
        idx = min(int((v - lo) / width), bins - 1)
        counts[idx] += 1
    return counts

def sanitize_input(text):
    """Remove potentially dangerous characters from input."""
    return "".join(c for c in text if c.isalnum() or c in " ._-" )
