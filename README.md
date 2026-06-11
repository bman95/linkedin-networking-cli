# LinkedIn Networking CLI

A professional, menu-driven command-line tool for automating LinkedIn networking campaigns.

## ⚠️ Disclaimer

Automating interactions with LinkedIn may violate [LinkedIn's User Agreement and Terms of Service](https://www.linkedin.com/legal/user-agreement) and can lead to account restrictions, temporary limits, or permanent bans. This project is intended for **educational and personal use only**. Use it responsibly: keep rate limits conservative, avoid aggressive automation, and understand that you assume all risk associated with using this tool. The authors accept no liability for any consequences to your account.

## Features

- 📋 **Campaign Management**: Full CRUD for networking campaigns with targeting criteria (keywords, location, industry, connection degree).
- 🤝 **Automated Connection Requests**: Send connection requests with smart, configurable rate limiting and randomized delays to reduce detection.
- 📊 **Analytics Dashboard**: Track campaign performance, connection counts, and success rates.
- 📤 **CSV Export**: Export collected contacts to CSV for use in other tools.
- 🗄️ **SQLite Storage**: Local persistence for campaigns, contacts, and analytics.
- 🔐 **Persistent Session Management**: Reuse an authenticated LinkedIn browser session across runs.

## Setup

1. **Install dependencies with uv**:
   ```bash
   uv sync
   ```

2. **Install the Playwright browser**:
   ```bash
   uv run python -m playwright install chrome
   ```
   By default the app uses Chrome. If you prefer the bundled Chromium, install it
   with `uv run python -m playwright install chromium` and set
   `PLAYWRIGHT_BROWSER_CHANNEL=chromium` (see [Browser configuration](#browser-configuration)).

3. **Set your LinkedIn credentials** via environment variables:
   ```bash
   export LINKEDIN_EMAIL="your-linkedin-email@example.com"
   export LINKEDIN_PASSWORD="your-password"
   ```

## Usage

Run the application:

```bash
uv run linkedin_cli.py
# or, via the installed entry point
linkedin-cli
```

Navigate with the **arrow keys**, press **Enter** to select, and **Ctrl+C** to exit.

From the main menu you can:

- **Dashboard** – view aggregate campaign statistics and analytics.
- **Create Campaign** – set up targeting (keywords, location, industry, connection degree).
- **Manage Campaigns** – view details, toggle active/inactive, edit settings, or delete a campaign (all changes persisted to SQLite).
- **Execute Campaign** – run the Playwright automation with rate limiting.
- **Check Connections** – monitor pending and accepted connection status.
- **Extract Profile Data** – pull detailed profile information.
- **Settings** – inspect credentials, browser, and rate-limit configuration.

## Browser configuration

- Defaults to Chrome via the Playwright channel `chrome`.
- Override the channel with `PLAYWRIGHT_BROWSER_CHANNEL` (e.g. `chromium` or `msedge`):
  ```bash
  export PLAYWRIGHT_BROWSER_CHANNEL=chromium
  ```
- Point at a specific browser binary with `PLAYWRIGHT_BROWSER_EXECUTABLE`:
  ```bash
  export PLAYWRIGHT_BROWSER_EXECUTABLE="/path/to/google-chrome"
  ```

## Development & Testing

Install the development dependencies and run the test suite:

```bash
uv sync --extra dev
uv run pytest
```

The test suite mocks the browser, so Playwright browsers are **not** required to run tests. A coverage report is generated automatically, including an HTML report under `htmlcov/` (open `htmlcov/index.html` in a browser).

## Data location

All application data is stored in your home directory under `~/.linkedin-networking-cli/`:

- `~/.linkedin-networking-cli/linkedin_networking.db` – SQLite database (campaigns, contacts, analytics).
- `~/.linkedin-networking-cli/browser_data/` – persistent browser/session data.
- Application logs.

## Docker

A `Dockerfile` is provided. Build the image and run the CLI, passing your credentials as environment variables:

```bash
# Build
docker build -t linkedin-networking-cli .

# Run (interactive)
docker run --rm -it \
  -e LINKEDIN_EMAIL="your-linkedin-email@example.com" \
  -e LINKEDIN_PASSWORD="your-password" \
  linkedin-networking-cli
```
