class MindgraphError(Exception):
    """Base exception for all MindGraph errors."""


class DatabaseError(MindgraphError):
    """Raised when a database connection, schema, or write operation fails."""


class IngestionError(MindgraphError):
    """Raised when ingesting a file fails. Carries the offending path."""

    def __init__(self, message: str, path: str | None = None):
        super().__init__(message)
        self.path = path

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base} (path={self.path})" if self.path else base


class ParseError(IngestionError):
    """Raised when a file cannot be parsed (frontmatter, structure, etc.)."""
