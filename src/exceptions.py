"""Custom exceptions for LinkedIn networking CLI.

This module defines a hierarchy of custom exceptions for better error handling
and debugging throughout the application. All custom exceptions inherit from
LinkedInAutomationError for easy catching of all automation-related errors.

Exception Hierarchy:
    Exception (built-in)
    └── LinkedInAutomationError (base for all automation errors)
        ├── LinkedInAuthenticationError (base for authentication errors)
        │   ├── NotAuthenticatedException (session expired/not logged in)
        │   └── LoginFailedException (login attempt failed)
        ├── SelectorNotFoundException (element not found on page)
        ├── RateLimitExceededException (connection limits reached)
        └── CaptchaDetectedException (captcha challenge detected)
"""


class LinkedInAutomationError(Exception):
    """Base exception for all LinkedIn automation errors.

    All custom exceptions in this module inherit from this class,
    allowing for easy catching of any automation-related error.
    """
    pass


class LinkedInAuthenticationError(LinkedInAutomationError):
    """Base exception for authentication-related errors.

    Raised when there are issues with LinkedIn authentication,
    including login failures, session expiration, or credential problems.
    """
    pass


class NotAuthenticatedException(LinkedInAuthenticationError):
    """Exception raised when user is not authenticated.

    This exception is raised when:
    - Session has expired and user needs to login again
    - User is not logged in but trying to perform authenticated actions
    - Session cookies are invalid or missing

    Example:
        raise NotAuthenticatedException("Session expired. Please login again.")
    """
    pass


class LoginFailedException(LinkedInAuthenticationError):
    """Exception raised when login attempt fails.

    This exception is raised when:
    - Login credentials are incorrect
    - Login page fails to load
    - Login verification process fails
    - Two-factor authentication fails

    Example:
        raise LoginFailedException("Failed to login: Invalid credentials")
    """
    pass


class SelectorNotFoundException(LinkedInAutomationError):
    """Exception raised when a DOM selector cannot be found.

    This exception is raised when:
    - A required page element is not found within the timeout period
    - LinkedIn's page structure has changed
    - Network issues prevent page elements from loading

    Attributes:
        selector: The CSS selector that was not found
        timeout: The timeout period that was used (optional)

    Example:
        raise SelectorNotFoundException(
            "Profile element not found",
            selector="div.profile-card",
            timeout=30000
        )
    """

    def __init__(self, message: str, selector: str = None, timeout: int = None):
        """Initialize the exception with selector details.

        Args:
            message: Error message
            selector: The CSS selector that was not found
            timeout: The timeout period in milliseconds
        """
        super().__init__(message)
        self.selector = selector
        self.timeout = timeout

    def __str__(self):
        """Return a formatted error message with selector details."""
        base_msg = super().__str__()
        if self.selector:
            base_msg += f" (selector: '{self.selector}'"
            if self.timeout:
                base_msg += f", timeout: {self.timeout}ms"
            base_msg += ")"
        return base_msg


class RateLimitExceededException(LinkedInAutomationError):
    """Exception raised when LinkedIn rate limits are exceeded.

    This exception is raised when:
    - Daily connection request limit is reached
    - Weekly invitation limit is exceeded
    - LinkedIn displays rate limit warning modals
    - Too many actions performed in a short time period

    Attributes:
        limit_type: Type of limit exceeded (e.g., 'daily', 'weekly')
        retry_after: When the action can be retried (optional)

    Example:
        raise RateLimitExceededException(
            "Weekly invitation limit reached",
            limit_type="weekly"
        )
    """

    def __init__(self, message: str, limit_type: str = None, retry_after: str = None):
        """Initialize the exception with rate limit details.

        Args:
            message: Error message
            limit_type: Type of limit that was exceeded
            retry_after: When the action can be retried
        """
        super().__init__(message)
        self.limit_type = limit_type
        self.retry_after = retry_after

    def __str__(self):
        """Return a formatted error message with rate limit details."""
        base_msg = super().__str__()
        if self.limit_type:
            base_msg += f" (limit_type: '{self.limit_type}'"
            if self.retry_after:
                base_msg += f", retry_after: {self.retry_after}"
            base_msg += ")"
        return base_msg


class CaptchaDetectedException(LinkedInAutomationError):
    """Exception raised when a CAPTCHA challenge is detected.

    This exception is raised when:
    - LinkedIn presents a CAPTCHA challenge
    - reCAPTCHA is detected on the page
    - Human verification is required
    - Bot detection mechanisms are triggered

    Example:
        raise CaptchaDetectedException("CAPTCHA detected - manual verification required")
    """
    pass
