"""Non-interactive campaign execution — the ``linkedin-run`` entry point's core.

Extracted from the classic InquirerPy CLI (``linkedin_cli.py``, retired by the
issue #47 single-UI cutover) so the headless ``run`` path carries no
InquirerPy/Rich menu dependency. Drives the same automation core the TUI's
"Run now" action uses (``LinkedInAutomation.search_and_connect``) and the same
shared helpers (``cli.helpers``, ``cli.automation_errors``).
"""

from __future__ import annotations

import asyncio
import sys

from automation.diagnostics import _artifacts_dir
from automation.linkedin import LinkedInAutomation
from cli.automation_errors import describe_automation_error
from cli.helpers import campaign_get_field, effective_daily_limit
from config.settings import AppSettings
from database.operations import DatabaseManager
from utils.logging import get_logger

logger = get_logger(__name__)


class CampaignRunner:
    """Resolves and executes one campaign's search-and-connect pass, headless."""

    def __init__(self):
        logger.info("Initializing non-interactive campaign runner")
        try:
            self.settings = AppSettings()
            self.db_manager = DatabaseManager(str(self.settings.db_path))
        except Exception as e:
            logger.error(f"Error initializing components: {e}", exc_info=True)
            self.db_manager = None
            self.settings = None

    async def _run_campaign_automation(
        self, campaign, search_limit, progress_update, max_sends=None
    ):
        """Run one campaign's search-and-connect pass — the shared run core.

        Login, search, and all rate-limit/daily-cap/session behavior stay in
        the automation layer (card-first connect from the result cards,
        falling back to the profile-page path for cards with no Connect
        control — issue #25). ``search_limit`` caps the results scanned;
        ``max_sends`` (optional) additionally caps the invitations sent this
        run. Returns the automation result dict with a ``status`` key
        (``safety_stop`` when the run was cut short by a CAPTCHA/challenge to
        protect the account).
        """
        async with LinkedInAutomation(self.db_manager, self.settings) as automation:
            progress_update("Launching browser and attaching to Chrome...")
            login_ok = await automation.login(progress_update)
            if not login_ok:
                return {"status": "login_failed"}

            progress_update(
                f"Searching for up to {search_limit} targeted profiles..."
            )
            results = await automation.search_and_connect(
                campaign,
                limit=search_limit,
                progress_callback=progress_update,
                max_sends=max_sends,
            )

            # A protective stop (inline CAPTCHA / challenge wall) must never be
            # reported as a clean run — checked before the empty-scan mapping so
            # a first-page CAPTCHA doesn't masquerade as "no profiles".
            if results.get("stopped_reason"):
                results.update(
                    {
                        "status": "safety_stop",
                        "profiles": results.get("scanned", 0),
                    }
                )
                return results

            if results.get("scanned", 0) == 0:
                return {"status": "no_profiles", "profiles": 0}

            results.update(
                {
                    "status": "success",
                    "profiles": results.get("scanned", 0),
                }
            )
            return results

    def _resolve_campaign(self, reference):
        """Resolve a campaign by numeric id or by name.

        A numeric ``reference`` is looked up by id first; otherwise (or if no
        campaign has that id) it is matched against campaign names, exact match
        first then case-insensitive. Returns the campaign or ``None``.

        ``Campaign.name`` is not unique in the schema, so a name matching more
        than one campaign raises :class:`ValueError` (naming the candidate ids)
        instead of silently running whichever row came back first — an
        unattended scheduler must never target the wrong audience.
        """
        ref = str(reference).strip()

        if ref.isdigit():
            campaign = self.db_manager.get_campaign(int(ref))
            if campaign is not None:
                return campaign

        def _ambiguous(matches):
            ids = ", ".join(str(campaign_get_field(c, "id", "?")) for c in matches)
            return ValueError(
                f"campaign name '{ref}' is ambiguous (ids: {ids}); "
                "use --campaign <id> instead."
            )

        campaigns = self.db_manager.get_campaigns(active_only=False)
        exact = [c for c in campaigns if campaign_get_field(c, "name") == ref]
        if len(exact) > 1:
            raise _ambiguous(exact)
        if exact:
            return exact[0]
        lowered = ref.lower()
        loose = [
            c
            for c in campaigns
            if (campaign_get_field(c, "name") or "").lower() == lowered
        ]
        if len(loose) > 1:
            raise _ambiguous(loose)
        if loose:
            return loose[0]
        return None

    def run_noninteractive(self, campaign_reference, max_invites=None):
        """Execute a campaign without prompts — the ``linkedin-run`` entry point.

        Resolves the campaign by id or name and drives the same automation as
        the TUI's "Run now" action via :meth:`_run_campaign_automation`. The
        scan uses the same ``search_limit`` setting as the interactive flow;
        invitations *sent* are capped at ``max_invites`` (default: the
        campaign's effective daily limit — its ``daily_limit``, or
        ``DAILY_CONNECTION_LIMIT`` when the campaign has no valid positive
        value; the same shared rule enforcement uses). Progress goes to
        stdout; failures print to stderr. Returns a process exit code (0
        success, non-zero on any failure — including a protective
        CAPTCHA/challenge stop, so schedulers can alert).
        """
        if not self.db_manager or not self.settings:
            print(
                "Error: automation requires database access and app settings.",
                file=sys.stderr,
            )
            return 1

        # Missing credentials only warn: login() resumes a saved session
        # (session.json or the persistent Chrome profile) without them, which
        # is a primary unattended workflow — log in once interactively, then
        # schedule `run`. If no valid session remains either, the login step
        # fails *bounded* (headless raises immediately; otherwise the manual-
        # login wait times out) and the run exits non-zero below.
        if not self.settings.validate_credentials():
            print(
                "Warning: LINKEDIN_EMAIL/LINKEDIN_PASSWORD are not set. The "
                "run will reuse a saved LinkedIn session if one is still "
                "valid; otherwise it will fail.",
                file=sys.stderr,
            )

        try:
            campaign = self._resolve_campaign(campaign_reference)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if campaign is None:
            print(
                f"Error: no campaign matching '{campaign_reference}'.",
                file=sys.stderr,
            )
            return 1

        campaign_name = campaign_get_field(campaign, "name", "campaign")

        # An unattended run must respect deactivation: the interactive/TUI
        # flows only ever offer active campaigns, so a paused campaign must not
        # keep sending from cron either.
        if not campaign_get_field(campaign, "active", True):
            print(
                f"Error: campaign '{campaign_name}' is inactive. Reactivate it "
                "before scheduling runs.",
                file=sys.stderr,
            )
            return 1
        # --max caps invitations SENT; the scan budget stays the interactive
        # flow's search_limit setting so repeat runs can skip past
        # already-contacted results instead of burning the cap on them.
        automation_settings = self.settings.get_automation_settings()
        # Default the cap through the shared effective-daily-limit rule — the
        # same one in-run enforcement uses — so a campaign with an invalid
        # daily_limit gets the env fallback rather than a 0/None cap that would
        # silently send nothing (issue #46).
        max_sends = (
            max_invites
            if max_invites is not None
            else effective_daily_limit(
                campaign_get_field(campaign, "daily_limit", None),
                automation_settings.get("daily_connection_limit", 20),
            )
        )
        search_limit = automation_settings.get("search_limit", 100)

        def progress_update(message: str) -> None:
            print(message, flush=True)

        progress_update(
            f"Starting run for '{campaign_name}' "
            f"(up to {max_sends} invitations this run)..."
        )

        try:
            result = asyncio.run(
                self._run_campaign_automation(
                    campaign, search_limit, progress_update, max_sends=max_sends
                )
            )
        except Exception as exc:
            # Keep the traceback in the file logs; the console stays clean.
            logger.info(
                "Automation stopped during non-interactive run: %s", exc,
                exc_info=True,
            )
            headline, evidence_ref = describe_automation_error(
                exc, "campaign execution", artifacts_dir=_artifacts_dir
            )
            print(headline, file=sys.stderr)
            print(evidence_ref, file=sys.stderr)
            return 1

        status = result.get("status") if result else None

        if status == "success":
            sent = result.get("sent", 0)
            possibly_sent = result.get("possibly_sent", 0)
            failed = result.get("failed", 0)
            existing = result.get("existing", 0)
            profiles_found = result.get("profiles", 0)
            total = result.get(
                "total_processed", sent + possibly_sent + failed + existing
            )
            progress_update(
                "Run complete — "
                f"scanned {profiles_found}, sent {sent}, "
                f"possibly sent {possibly_sent}, already contacted {existing}, "
                f"failures {failed}, total processed {total}."
            )
            return 0
        if status == "login_failed":
            print(
                "Error: login to LinkedIn failed. Verify credentials and any "
                "multi-factor prompts.",
                file=sys.stderr,
            )
            return 1
        if status == "no_profiles":
            progress_update(
                "No profiles matched the campaign criteria. Review the filters."
            )
            return 0
        if status == "safety_stop":
            sent = result.get("sent", 0)
            possibly_sent = result.get("possibly_sent", 0)
            print(
                "Error: automation stopped early to protect the account "
                "(CAPTCHA or challenge detected). Resolve the challenge in the "
                f"browser before the next run. Progress so far was saved "
                f"(sent {sent}, possibly sent {possibly_sent}).",
                file=sys.stderr,
            )
            return 1
        print(f"Automation finished with status: {status}", file=sys.stderr)
        return 1
