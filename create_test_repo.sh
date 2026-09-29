#!/bin/bash
set -e

REPO_DIR="D:/projects/opd/test-repo"

rm -rf "$REPO_DIR"
mkdir -p "$REPO_DIR"
cd "$REPO_DIR"
git init -b main

# Helper: commit with given author and date
do_commit() {
    local name="$1" email="$2" date="$3" msg="$4"
    git add -A
    GIT_AUTHOR_NAME="$name" GIT_AUTHOR_EMAIL="$email" \
    GIT_AUTHOR_DATE="$date" \
    GIT_COMMITTER_NAME="$name" GIT_COMMITTER_EMAIL="$email" \
    GIT_COMMITTER_DATE="$date" \
    git commit -m "$msg"
}

# --- Initial files ---
mkdir -p src tests

cat > src/__init__.py << 'EOF'
"""Application package."""
EOF

cat > src/main.py << 'EOF'
"""Main application entry point."""

def main():
    print("Hello, World!")

if __name__ == "__main__":
    main()
EOF

cat > src/database.py << 'EOF'
"""Database connection and operations."""

def get_connection():
    return None

def close_connection(conn):
    pass
EOF

cat > src/api.py << 'EOF'
"""API routes and handlers."""

def get_endpoint():
    return "/"

def handle_request(req):
    return "OK"
EOF

cat > src/models.py << 'EOF'
"""Data models."""

class User:
    def __init__(self, name):
        self.name = name

class Item:
    def __init__(self, title):
        self.title = title
EOF

cat > src/utils.py << 'EOF'
"""Utility functions."""

def log(message):
    print(f"[LOG] {message}")

def format_date(d):
    return d.strftime("%Y-%m-%d")
EOF

cat > tests/__init__.py << 'EOF'
"""Tests package."""
EOF

cat > tests/test_main.py << 'EOF'
"""Tests for main module."""

def test_main():
    assert True
EOF

cat > tests/test_api.py << 'EOF'
"""Tests for API module."""

def test_get_endpoint():
    assert True
EOF

cat > tests/test_models.py << 'EOF'
"""Tests for models module."""

def test_user():
    assert True
EOF

cat > config.py << 'EOF'
"""Application configuration."""

DEBUG = False
HOST = "localhost"
PORT = 8080
EOF

cat > requirements.txt << 'EOF'
flask==2.3.0
sqlalchemy==2.0.0
pytest==7.4.0
EOF

cat > README.md << 'EOF'
# Project

A sample project for testing.
EOF

do_commit "Alice Smith" "alice@example.com" "2026-03-30T10:00:00" "Initial project setup"

# --- Phase 1: April (commits 2-15) ---

echo '
def setup_logging():
    """Configure application logging."""
    import logging
    logging.basicConfig(level=logging.INFO)' >> src/main.py
do_commit "Alice Smith" "alice@example.com" "2026-03-31T14:22:00" "Add logging setup to main"

echo '
def validate_request(req):
    """Validate incoming request data."""
    if not req:
        raise ValueError("Empty request")
    return True' >> src/api.py
do_commit "Bob Johnson" "bob@example.com" "2026-04-01T09:15:00" "Add request validation to API"

echo '
    def to_dict(self):
        """Serialize user to dictionary."""
        return {"name": self.name}' >> src/models.py
do_commit "Carol Davis" "carol@example.com" "2026-04-02T11:30:00" "Add serialization to User model"

echo '
def query_users():
    """Fetch all users from database."""
    return []

def insert_user(name):
    """Insert a new user record."""
    pass' >> src/database.py
do_commit "Alice Smith" "alice@example.com" "2026-04-03T16:45:00" "Add user query and insert functions"

echo '
def handle_error(e):
    """Handle API errors gracefully."""
    return {"error": str(e), "status": 500}' >> src/api.py
do_commit "Bob Johnson" "bob@example.com" "2026-04-04T10:05:00" "Add error handling to API handlers"

echo '
    def to_dict(self):
        """Serialize item to dictionary."""
        return {"title": self.title}' >> src/models.py
do_commit "Carol Davis" "carol@example.com" "2026-04-05T13:20:00" "Add serialization to Item model"

echo '
def validate_config():
    """Validate configuration values."""
    if PORT < 1:
        raise ValueError("Invalid port")
    return True' >> config.py
do_commit "Alice Smith" "alice@example.com" "2026-04-08T09:30:00" "Add config validation function"

echo '
def apply_middleware(req, handler):
    """Apply middleware chain to request."""
    return handler(req)' >> src/api.py
echo '
def init_app():
    """Initialize application components."""
    setup_logging()' >> src/main.py
do_commit "Bob Johnson" "bob@example.com" "2026-04-09T15:10:00" "Add middleware support and app init"

echo '
    @property
    def display_name(self):
        """Get formatted display name."""
        return self.name.title()' >> src/models.py
do_commit "Carol Davis" "carol@example.com" "2026-04-10T08:45:00" "Add display_name property to User"

echo '
def parse_args(args):
    """Parse command line arguments."""
    return {"verbose": "-v" in args}' >> src/utils.py
do_commit "Alice Smith" "alice@example.com" "2026-04-11T11:00:00" "Add argument parsing utility"

echo 'requests==2.31.0' >> requirements.txt
do_commit "Bob Johnson" "bob@example.com" "2026-04-14T14:30:00" "Add requests dependency"

echo '
## Getting Started

Run `python src/main.py` to start the application.' >> README.md
do_commit "Carol Davis" "carol@example.com" "2026-04-15T10:15:00" "Add getting started section to README"

echo '
def create_migration_table():
    """Create the migrations tracking table."""
    pass' >> src/database.py
do_commit "Alice Smith" "alice@example.com" "2026-04-16T16:20:00" "Add migration table helper"

echo '
class RateLimiter:
    """Simple rate limiter for API endpoints."""
    def __init__(self, max_requests=100):
        self.max_requests = max_requests
        self.count = 0' >> src/api.py
do_commit "Bob Johnson" "bob@example.com" "2026-04-17T09:50:00" "Add rate limiter class to API"

echo '
    def copy(self):
        """Create a shallow copy of the item."""
        new = Item(self.title)
        return new' >> src/models.py
do_commit "Carol Davis" "carol@example.com" "2026-04-18T12:40:00" "Add copy method to Item model"

echo '
def handle_exceptions(fn):
    """Decorator to catch and log exceptions."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            log(f"Error: {e}")
            return None
    return wrapper' >> src/main.py
do_commit "Alice Smith" "alice@example.com" "2026-04-21T10:30:00" "Add exception handler decorator to main"

echo '
def log_request(req, status):
    """Log API request details."""
    log(f"Request: {req} -> {status}")' >> src/utils.py
do_commit "Bob Johnson" "bob@example.com" "2026-04-22T14:15:00" "Add request logging utility"

echo '
class Relationship:
    """Model relationship definition."""
    def __init__(self, source, target, type):
        self.source = source
        self.target = target
        self.type = type' >> src/models.py
do_commit "Carol Davis" "carol@example.com" "2026-04-23T11:25:00" "Add Relationship model"

echo '
def load_config():
    """Load configuration from environment."""
    import os
    return {
        "debug": os.getenv("DEBUG", "false"),
        "host": os.getenv("HOST", HOST),
    }' >> config.py
echo '
def safe_get(d, key, default=None):
    """Safely get a value from a dict."""
    return d.get(key, default)' >> src/utils.py
do_commit "Alice Smith" "alice@example.com" "2026-04-24T15:45:00" "Add config loader and safe_get utility"

# --- Phase 2: May (commits 26-35) ---

echo '
def create_pool(size=5):
    """Create a connection pool."""
    return {"size": size, "connections": []}' >> src/database.py
do_commit "Alice Smith" "alice@example.com" "2026-05-01T09:00:00" "Add connection pooling to database"

echo '
def health_check():
    """Return API health status."""
    return {"status": "healthy", "uptime": 0}' >> src/api.py
do_commit "Bob Johnson" "bob@example.com" "2026-05-02T10:30:00" "Add health check endpoint"

echo '
    def add_index(self, field):
        """Add a search index to the model field."""
        self.__dict__.setdefault("indexes", []).append(field)' >> src/models.py
do_commit "Carol Davis" "carol@example.com" "2026-05-03T14:20:00" "Add index support to User model"

echo '
def run_with_args(argv):
    """Run the application with parsed arguments."""
    config = parse_args(argv)
    main()' >> src/main.py
do_commit "Alice Smith" "alice@example.com" "2026-05-05T11:10:00" "Add CLI argument support to main"

echo '
def paginate(results, page=1, per_page=20):
    """Paginate a list of results."""
    start = (page - 1) * per_page
    return results[start:start + per_page]' >> src/api.py
do_commit "Bob Johnson" "bob@example.com" "2026-05-06T16:40:00" "Add pagination to API"

echo '
    def __init__(self, name, created_at=None, updated_at=None):
        self.name = name
        self.created_at = created_at
        self.updated_at = updated_at' >> src/models.py
do_commit "Carol Davis" "carol@example.com" "2026-05-07T09:55:00" "Add audit timestamps to User model"

echo '
def begin_transaction(conn):
    """Start a database transaction."""
    pass

def commit_transaction(conn):
    """Commit the current transaction."""
    pass' >> src/database.py
do_commit "Alice Smith" "alice@example.com" "2026-05-08T13:30:00" "Add transaction management functions"

echo '
def format_response(data, status=200):
    """Format an API response with status."""
    return {"data": data, "status": status}' >> src/utils.py
do_commit "Bob Johnson" "bob@example.com" "2026-05-09T10:20:00" "Add response formatting utility"

echo '
class ModelVersion:
    """Track model schema versions."""
    CURRENT = 1
    def __init__(self, version=CURRENT):
        self.version = version' >> src/models.py
do_commit "Carol Davis" "carol@example.com" "2026-05-12T15:00:00" "Add model versioning class"

echo '
def reload_config():
    """Hot-reload configuration without restart."""
    return load_config()' >> config.py
do_commit "Alice Smith" "alice@example.com" "2026-05-13T11:45:00" "Add config hot-reload function"

# --- Phase 3: June (commits 36-45) ---

echo '
def backup_database(path):
    """Create a backup of the database."""
    return {"path": path, "status": "created"}' >> src/database.py
do_commit "Alice Smith" "alice@example.com" "2026-06-01T09:30:00" "Add database backup function"

echo '
class ResponseCache:
    """Cache API responses for repeated requests."""
    def __init__(self, ttl=300):
        self.ttl = ttl
        self.cache = {}' >> src/api.py
do_commit "Bob Johnson" "bob@example.com" "2026-06-02T14:15:00" "Add response cache to API"

echo '
def create_user(data):
    """Factory function to create a User instance."""
    return User(name=data.get("name", "Unknown"))' >> src/models.py
do_commit "Carol Davis" "carol@example.com" "2026-06-03T10:40:00" "Add user factory function"

echo '
def shutdown():
    """Graceful shutdown handler."""
    log("Shutting down...")
    close_all_connections()' >> src/main.py
echo '
def close_all_connections():
    """Close all active database connections."""
    pass' >> src/database.py
do_commit "Alice Smith" "alice@example.com" "2026-06-05T16:20:00" "Add graceful shutdown handling"

echo '
def compress_response(data):
    """Compress response payload for transfer."""
    import gzip
    return gzip.compress(bytes(data, "utf-8"))' >> src/api.py
echo '
def truncate(text, length=100):
    """Truncate text to specified length."""
    return text[:length] if len(text) > length else text' >> src/utils.py
do_commit "Bob Johnson" "bob@example.com" "2026-06-06T09:50:00" "Add response compression and text truncation"

echo '
    def get_cached(self):
        """Return a cached representation of the model."""
        return self._cache if hasattr(self, "_cache") else self.to_dict()' >> src/models.py
do_commit "Carol Davis" "carol@example.com" "2026-06-09T13:30:00" "Add model caching support"

echo '
def retry_query(fn, max_retries=3):
    """Retry a database query on failure."""
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception:
            if attempt == max_retries - 1:
                raise' >> src/database.py
do_commit "Alice Smith" "alice@example.com" "2026-06-10T11:15:00" "Add query retry logic to database"

echo '
def set_cors_headers(headers):
    """Set CORS headers on response."""
    headers["Access-Control-Allow-Origin"] = "*"
    return headers' >> src/api.py
do_commit "Bob Johnson" "bob@example.com" "2026-06-11T15:45:00" "Add CORS header support"

echo '
def validate_model(obj, fields):
    """Validate model instance has required fields."""
    missing = [f for f in fields if not hasattr(obj, f)]
    return len(missing) == 0' >> src/models.py
do_commit "Carol Davis" "carol@example.com" "2026-06-12T10:30:00" "Add model field validation"

echo '
class ConfigProfile:
    """Named configuration profiles."""
    def __init__(self, name, values):
        self.name = name
        self.values = values

def get_profile(name):
    """Get a configuration profile by name."""
    profiles = {"dev": ConfigProfile("dev", {"DEBUG": True})}
    return profiles.get(name)' >> config.py
echo '
def deep_merge(base, override):
    """Deep merge two dictionaries."""
    result = dict(base)
    for k, v in override.items():
        result[k] = v
    return result' >> src/utils.py
do_commit "Alice Smith" "alice@example.com" "2026-06-15T14:10:00" "Add config profiles and deep merge utility"

# --- Phase 4: July (commits 46-52) ---

echo '
class Webhook:
    """Manage outbound webhooks."""
    def __init__(self, url, events):
        self.url = url
        self.events = events' >> src/api.py
do_commit "Bob Johnson" "bob@example.com" "2026-07-01T09:40:00" "Add webhook support to API"

echo '
class ModelEvent:
    """Event emitted when a model changes."""
    def __init__(self, model, action):
        self.model = model
        self.action = action  # "created", "updated", "deleted"' >> src/models.py
do_commit "Carol Davis" "carol@example.com" "2026-07-02T11:25:00" "Add model change events"

echo '
def encrypt_value(val, key):
    """Encrypt a sensitive value."""
    return f"enc:{key}:{val}"

def decrypt_value(encrypted, key):
    """Decrypt an encrypted value."""
    parts = encrypted.split(":")
    return parts[2] if len(parts) == 3 else encrypted' >> src/database.py
do_commit "Alice Smith" "alice@example.com" "2026-07-03T15:30:00" "Add field encryption to database layer"

echo '
def batch_process(items, handler, batch_size=50):
    """Process items in batches."""
    results = []
    for i in range(0, len(items), batch_size):
        results.extend(handler(items[i:i+batch_size]))
    return results' >> src/api.py
echo '
def batch_handler(batch):
    """Process a batch of items."""
    return [item for item in batch]' >> src/main.py
do_commit "Bob Johnson" "bob@example.com" "2026-07-07T10:15:00" "Add batch processing to API and main"

echo '
    def soft_delete(self):
        """Mark model as deleted without removing."""
        self.deleted = True
        self.updated_at = __import__("datetime").datetime.now()' >> src/models.py
do_commit "Carol Davis" "carol@example.com" "2026-07-08T14:50:00" "Add soft delete to User model"

echo '
def check_health():
    """Check application health status."""
    return {"status": "ok", "checks": {"db": "connected"}}' >> src/main.py
echo '
def timer(func):
    """Decorator to measure function execution time."""
    import time
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        log(f"{func.__name__} took {time.time()-start:.3f}s")
        return result
    return wrapper' >> src/utils.py
do_commit "Alice Smith" "alice@example.com" "2026-07-09T09:20:00" "Add health check and timer utility"

echo 'jinja2==3.1.2' >> requirements.txt
do_commit "Bob Johnson" "bob@example.com" "2026-07-10T16:00:00" "Add jinja2 template dependency"

# --- Phase 5: August (commits 53-55) ---

echo '
def monitor_connections():
    """Monitor active database connections."""
    return {"active": 0, "idle": 0, "max": 5}' >> src/database.py
do_commit "Alice Smith" "alice@example.com" "2026-08-01T10:30:00" "Add connection monitoring to database"

echo '
    def export_json(self):
        """Export model as JSON string."""
        import json
        return json.dumps(self.to_dict())' >> src/models.py
echo '
## API

The REST API is available at `/api/v1`. See `src/api.py` for endpoints.' >> README.md
do_commit "Carol Davis" "carol@example.com" "2026-08-05T13:45:00" "Add model JSON export and API docs"

echo '
def stream_response(generator):
    """Stream a response from a generator."""
    for chunk in generator:
        yield chunk' >> src/api.py
do_commit "Bob Johnson" "bob@example.com" "2026-08-10T11:10:00" "Add streaming response support"

# --- Phase 6: September - recent commits (commits 56-60) ---
# Last 2 weeks: Alice and Bob only

echo '
def handle_signal(sig, frame):
    """Handle OS signals for graceful shutdown."""
    log(f"Received signal {sig}")
    shutdown()' >> src/main.py
do_commit "Alice Smith" "alice@example.com" "2026-09-01T09:15:00" "Add signal handling to main"

echo '
def collect_metrics():
    """Collect API performance metrics."""
    return {"requests": 0, "errors": 0, "avg_latency": 0.0}' >> src/api.py
echo '
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
    return counts' >> src/utils.py
do_commit "Bob Johnson" "bob@example.com" "2026-09-05T14:30:00" "Add metrics collection and histogram utility"

echo '
def get_pool_stats():
    """Get connection pool statistics."""
    return {"created": 0, "in_use": 0, "available": 0}' >> src/database.py
do_commit "Alice Smith" "alice@example.com" "2026-09-15T10:45:00" "Add pool stats to database"

echo '
def trace_request(req):
    """Add tracing metadata to a request."""
    import uuid
    req["trace_id"] = str(uuid.uuid4())
    return req' >> src/api.py
do_commit "Bob Johnson" "bob@example.com" "2026-09-18T11:20:00" "Add request tracing to API"

echo '
def validate_port(port):
    """Validate that a port number is in range."""
    return isinstance(port, int) and 1 <= port <= 65535' >> config.py
echo '
def sanitize_input(text):
    """Remove potentially dangerous characters from input."""
    return "".join(c for c in text if c.isalnum() or c in " ._-" )' >> src/utils.py
do_commit "Alice Smith" "alice@example.com" "2026-09-22T15:00:00" "Add port validation and input sanitization"

echo '
def gzip_response(data):
    """Compress and encode response data."""
    import base64
    compressed = compress_response(data)
    return base64.b64encode(compressed).decode("utf-8")' >> src/api.py
do_commit "Bob Johnson" "bob@example.com" "2026-09-25T09:30:00" "Add gzip encoding to API responses"

echo '
def get_version():
    """Return the application version."""
    return "1.0.0"

def print_banner():
    """Print application startup banner."""
    log(f"Starting app v{get_version()}")' >> src/main.py
do_commit "Alice Smith" "alice@example.com" "2026-09-28T14:45:00" "Add version and startup banner to main"

echo "Done! Repository created at $REPO_DIR"
