# LinkedIn Networking CLI

A professional command-line tool for LinkedIn networking automation with an interactive menu-driven interface.

## Features

- <¯ **Campaign Management**: Create and manage networking campaigns with targeting criteria
- =€ **Automated Execution**: Send connection requests with smart rate limiting
- =Ê **Analytics Dashboard**: Track campaign performance and success rates
- <¨ **Beautiful CLI**: Interactive interface with progress tracking and real-time updates
- =¾ **SQLite Database**: Local storage for campaigns, contacts, and analytics
- = **Session Management**: Persistent LinkedIn authentication

## Setup

1. **Install with uv**:
   ```bash
   cd linkedin-networking-cli
   uv sync
   ```

2. **Install Playwright Chrome**:
   ```bash
   uv run python -m playwright install chrome
   ```
   (Optional) Set `PLAYWRIGHT_BROWSER_CHANNEL=chromium` if you prefer the bundled Chromium browser.

3. **Set environment variables**:
   ```bash
   export LINKEDIN_EMAIL="your-linkedin-email@example.com"
   export LINKEDIN_PASSWORD="your-password"
   ```

## Usage

Run the application:
```bash
uv run src/main.py
```

Navigate with arrow keys, Enter to select, Ctrl+C to exit.
## Browser configuration

- Defaults to Chrome via Playwright channel `chrome`.
- Override with `PLAYWRIGHT_BROWSER_CHANNEL` (e.g. set to `chromium` or `msedge`).
- Provide a full path with `PLAYWRIGHT_BROWSER_EXECUTABLE` to use a specific Chrome binary.

