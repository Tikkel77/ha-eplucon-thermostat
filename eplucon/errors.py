class EpluconError(Exception):
    """Base error for the Eplucon client."""


class AuthenticationError(EpluconError):
    """Raised when authentication fails."""


class ApiError(EpluconError):
    """Raised when the API returns an invalid response."""


class NotFoundError(EpluconError):
    """Raised when requested data cannot be found."""


class WriteError(EpluconError):
    """Raised when a write endpoint does not apply the requested change."""
