"""Centralized logging configuration for the LinkedIn networking CLI."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


class LoggerSetup:
    """Configure and manage application-wide logging."""

    _initialized = False
    _log_dir: Path | None = None

    @classmethod
    def setup(
        cls,
        log_dir: Path | None = None,
        log_level: int = logging.INFO,
        console_output: bool = True,
        file_output: bool = True,
        console_level: int = logging.WARNING,
    ) -> None:
        """
        Configure logging for the entire application.

        Args:
            log_dir: Directory for log files (default: ~/.linkedin-networking-cli/logs)
            log_level: Logging level (default: INFO)
            console_output: Enable console logging (default: True)
            file_output: Enable file logging (default: True)
            console_level: Logging level for console output (default: WARNING, so
                INFO startup noise stays out of the terminal but is kept in files)
        """
        if cls._initialized:
            return

        # Set up log directory
        if log_dir is None:
            log_dir = Path.home() / ".linkedin-networking-cli" / "logs"

        cls._log_dir = log_dir
        cls._log_dir.mkdir(parents=True, exist_ok=True)

        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)

        # Remove any existing handlers
        root_logger.handlers.clear()

        # Create formatters
        detailed_formatter = logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        simple_formatter = logging.Formatter(
            fmt="%(levelname)s - %(name)s - %(message)s"
        )

        # Console handler
        if console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(console_level)
            console_handler.setFormatter(simple_formatter)
            # Per-record console opt-out: log with extra={"console": False} to
            # keep a record in the file logs (e.g. errors.log) without echoing
            # it to stdout — entry points that own their stdout/stderr
            # contract (linkedin-run) report failures on stderr themselves.
            console_handler.addFilter(
                lambda record: getattr(record, "console", True)
            )
            root_logger.addHandler(console_handler)

        # File handlers
        if file_output:
            # Main log file (rotating)
            main_log_file = cls._log_dir / "linkedin_cli.log"
            main_file_handler = RotatingFileHandler(
                main_log_file,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
                encoding="utf-8"
            )
            main_file_handler.setLevel(log_level)
            main_file_handler.setFormatter(detailed_formatter)
            root_logger.addHandler(main_file_handler)

            # Error log file (rotating, errors and above only)
            error_log_file = cls._log_dir / "errors.log"
            error_file_handler = RotatingFileHandler(
                error_log_file,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
                encoding="utf-8"
            )
            error_file_handler.setLevel(logging.ERROR)
            error_file_handler.setFormatter(detailed_formatter)
            root_logger.addHandler(error_file_handler)

        # Suppress overly verbose third-party loggers
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("selenium").setLevel(logging.WARNING)
        logging.getLogger("playwright").setLevel(logging.WARNING)

        cls._initialized = True

        # Log initialization
        logger = logging.getLogger(__name__)
        logger.info("Logging system initialized")
        if file_output:
            logger.info(f"Log files location: {cls._log_dir}")

    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        """
        Get a logger instance with the specified name.

        Args:
            name: Logger name (typically __name__ from calling module)

        Returns:
            Configured logger instance
        """
        # Initialize with defaults if not already done
        if not cls._initialized:
            cls.setup()

        return logging.getLogger(name)


def get_logger(name: str) -> logging.Logger:
    """
    Convenience function to get a logger instance.

    Args:
        name: Logger name (typically __name__ from calling module)

    Returns:
        Configured logger instance
    """
    return LoggerSetup.get_logger(name)
