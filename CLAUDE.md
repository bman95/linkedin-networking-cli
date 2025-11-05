# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a LinkedIn networking automation CLI built with Python. The application uses Playwright for web automation, InquirerPy for the TUI interface, SQLModel for database operations, and includes both a main InquirerPy-based CLI and an experimental interactive CLI using prompt-toolkit.

## Essential Commands

### Setup and Installation
```bash
# Install dependencies and sync environment
uv sync

# Install required Playwright browsers
uv run python -m playwright install chromium

# Set required environment variables
export LINKEDIN_EMAIL="your-linkedin-email@example.com"
export LINKEDIN_PASSWORD="your-password"
```

### Running the Application
```bash
# Main InquirerPy-based CLI
uv run linkedin_cli.py

# Project script
linkedin-cli
```

## Architecture Overview

### Core Components

**Database Layer (`src/database/`)**:
- `models.py` - SQLModel definitions for Campaign, Contact, Analytics, Settings
- `operations.py` - DatabaseManager class with CRUD operations
- Uses SQLite for local storage at `~/.linkedin-networking-cli/linkedin_networking.db`

**Automation Layer (`src/automation/`)**:
- `linkedin.py` - LinkedInAutomation class using Playwright for web scraping
- `session.py` - Session management for persistent LinkedIn authentication
- Implements rate limiting, profile searching, and connection request automation

**UI Layer**:
- `linkedin_cli.py` - **Main application**: InquirerPy-based CLI with rich interactive menus

**Configuration (`src/config/`)**:
- `settings.py` - AppSettings class managing environment variables and app configuration
- Browser settings, automation parameters, and credential validation

### Data Flow

1. **Campaign Creation**: Users create campaigns with targeting criteria through InquirerPy forms
2. **Automation Execution**: LinkedInAutomation class processes campaigns using Playwright
3. **Data Storage**: All campaign data, contacts, and analytics stored in SQLite via SQLModel
4. **Progress Tracking**: Real-time updates through InquirerPy interface with rich formatting

### Key Design Patterns

- **Async Context Managers**: LinkedInAutomation uses `async with` pattern for resource management
- **Menu-based Navigation**: InquirerPy menus for intuitive CLI interaction
- **Database Sessions**: SQLModel sessions with proper cleanup using context managers
- **Environment-based Configuration**: Settings loaded from environment variables with fallbacks

### Dependencies

- **InquirerPy**: Primary CLI framework for interactive menus and forms
- **Playwright**: Web automation for LinkedIn interaction
- **SQLModel**: Type-safe database operations with SQLite
- **Rich**: Terminal formatting and progress display
- **Pydantic**: Data validation and settings management

### Browser Automation Notes

The LinkedIn automation requires careful handling:
- Uses persistent browser sessions stored in `~/.linkedin-networking-cli/browser_data/`
- Implements random delays between actions to avoid detection
- Requires manual login verification on first run
- Respects daily connection limits and rate limiting

### File Structure Context

- `linkedin_cli.py` - Main entry point with InquirerPy-based CLI interface
- Application data stored in user home directory under `.linkedin-networking-cli/`