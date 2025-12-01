class SerenityError(Exception):
    """Base exception for Serenity errors."""

    pass


class ConfigurationError(SerenityError):
    """Exception raised for configuration-related errors."""

    pass


class DatabaseError(SerenityError):
    """Exception raised for database-related errors."""

    pass


class PermissionError(SerenityError):
    """Exception raised for permission-related errors."""

    pass


class CalculationError(SerenityError):
    """Exception raised for errors during slowmode calculations."""

    pass
