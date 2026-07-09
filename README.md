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

3. **Authenticate with LinkedIn** in one of two ways:

   - **Automatic** – set your credentials via environment variables and the app
     fills the login form for you:
     ```bash
     export LINKEDIN_EMAIL="your-linkedin-email@example.com"
     export LINKEDIN_PASSWORD="your-password"
     ```
   - **Manual** – leave the variables unset. The first time you run a campaign a
     Chrome window opens; sign in there yourself (including any 2FA / checkpoint
     step). The app detects when you reach the feed and continues automatically.

   Either way the session is persisted so **subsequent runs stay logged in** and
   you won't need to authenticate again until the session expires. Persistence
   uses one of two complementary mechanisms depending on how the browser
   launches: with a real Chrome install (the default), login state lives in the
   persistent browser profile under `~/.linkedin-networking-cli/browser_data/`;
   on the transient (non-persistent) fallback it is loaded from Playwright
   `storage_state` in `~/.linkedin-networking-cli/session.json`. Only one is
   *read* per run, but `session.json` is always refreshed on exit (persistent
   runs included) so either path can resume the other's session.

## Usage

Launch the full-screen TUI:

```bash
uv run linkedin_tui.py
# or, via the installed entry point
linkedin-tui
```

Navigate with the **arrow keys**, press **Enter** to select, and **Esc** to go back (**q** to quit). The number keys **1**–**4** jump straight to a destination from the home screen, and `Ctrl+P` opens the command palette.

From the home screen you can:

- **Dashboard** – view aggregate campaign statistics and analytics.
- **Create Campaign** – set up targeting (keywords, location, industry, connection degree).
- **Manage Campaigns** – view a campaign's details and, per campaign, run the automation, check connection acceptances, edit settings, toggle active/inactive, export contacts to CSV, or delete it (all changes persisted to SQLite).
- **Settings** – inspect credentials, browser, and rate-limit configuration.

### Scheduled / headless runs

For cron or a systemd timer, use the non-interactive entry point instead — it
runs one campaign's search-and-connect pass without any prompts and exits with
a process exit code a scheduler can alert on:

```bash
uv run linkedin_run.py --campaign "Tech Leads" [--max 5]
# or, via the installed entry point
linkedin-run --campaign "Tech Leads" [--max 5]
```

`--campaign` accepts either the campaign's numeric id or its name. `--max`
caps invitations *sent* this run (default: the campaign's daily limit). All
rate-limit, daily-cap and session logic is respected, and the command exits
non-zero on failure — including a protective CAPTCHA/challenge stop.

> **Note on connection messages:** LinkedIn restricts *personalized* invitation
> notes to Premium accounts (free accounts get only a small monthly quota). When
> a campaign defines a message template but a note can't be attached, the app
> sends the invitation **without** a note so the connection request still goes
> out. A campaign's message template is therefore best-effort, not guaranteed.

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
