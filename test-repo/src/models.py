"""Data models."""

class User:
    def __init__(self, name):
        self.name = name

class Item:
    def __init__(self, title):
        self.title = title

    def to_dict(self):
        """Serialize user to dictionary."""
        return {"name": self.name}

    def to_dict(self):
        """Serialize item to dictionary."""
        return {"title": self.title}

    @property
    def display_name(self):
        """Get formatted display name."""
        return self.name.title()

    def copy(self):
        """Create a shallow copy of the item."""
        new = Item(self.title)
        return new

class Relationship:
    """Model relationship definition."""
    def __init__(self, source, target, type):
        self.source = source
        self.target = target
        self.type = type

    def add_index(self, field):
        """Add a search index to the model field."""
        self.__dict__.setdefault("indexes", []).append(field)

    def __init__(self, name, created_at=None, updated_at=None):
        self.name = name
        self.created_at = created_at
        self.updated_at = updated_at

class ModelVersion:
    """Track model schema versions."""
    CURRENT = 1
    def __init__(self, version=CURRENT):
        self.version = version

def create_user(data):
    """Factory function to create a User instance."""
    return User(name=data.get("name", "Unknown"))

    def get_cached(self):
        """Return a cached representation of the model."""
        return self._cache if hasattr(self, "_cache") else self.to_dict()

def validate_model(obj, fields):
    """Validate model instance has required fields."""
    missing = [f for f in fields if not hasattr(obj, f)]
    return len(missing) == 0

class ModelEvent:
    """Event emitted when a model changes."""
    def __init__(self, model, action):
        self.model = model
        self.action = action  # "created", "updated", "deleted"

    def soft_delete(self):
        """Mark model as deleted without removing."""
        self.deleted = True
        self.updated_at = __import__("datetime").datetime.now()

    def export_json(self):
        """Export model as JSON string."""
        import json
        return json.dumps(self.to_dict())
