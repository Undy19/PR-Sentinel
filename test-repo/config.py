"""Application configuration."""

DEBUG = False
HOST = "localhost"
PORT = 8080


def validate_config():
    """Validate configuration values."""
    if PORT < 1:
        raise ValueError("Invalid port")
    return True


def load_config():
    """Load configuration from environment."""
    import os

    return {
        "debug": os.getenv("DEBUG", "false"),
        "host": os.getenv("HOST", HOST),
    }


def reload_config():
    """Hot-reload configuration without restart."""
    return load_config()


class ConfigProfile:
    """Named configuration profiles."""

    def __init__(self, name, values):
        self.name = name
        self.values = values


def get_profile(name):
    """Get a configuration profile by name."""
    profiles = {"dev": ConfigProfile("dev", {"DEBUG": True})}
    return profiles.get(name)


def validate_port(port):
    """Validate that a port number is in range."""
    return isinstance(port, int) and 1 <= port <= 65535
