#!/usr/bin/env python3
"""LinkedIn Networking CLI - interactive menu-driven interface."""

import argparse
import asyncio
import sys
from collections import namedtuple
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from InquirerPy.separator import Separator
from rich.align import Align
from rich.box import ROUNDED
from rich.console import Console, Group
from rich.markup import escape as _rich_escape
from rich.panel import Panel
from rich.text import Text

# LinkedIn brand blue, used across the welcome banner
BRAND_BLUE = "#0A66C2"

# ANSI Shadow font (6 rows per glyph) for the startup banner's big ASCII title
_FONT = {
    "N": ["███╗   ██╗", "████╗  ██║", "██╔██╗ ██║", "██║╚██╗██║", "██║ ╚████║", "╚═╝  ╚═══╝"],
    "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
    "T": ["████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "],
    "W": ["██╗    ██╗", "██║    ██║", "██║ █╗ ██║", "██║███╗██║", "╚███╔███╔╝", " ╚══╝╚══╝ "],
    "O": [" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    "K": ["██╗  ██╗", "██║ ██╔╝", "█████╔╝ ", "██╔═██╗ ", "██║  ██╗", "╚═╝  ╚═╝"],
    "I": ["██╗", "██║", "██║", "██║", "██║", "╚═╝"],
    "G": [" ██████╗ ", "██╔════╝ ", "██║  ███╗", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "C": [" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"],
    "L": ["██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"],
    " ": ["   "] * 6,
}


def _render_word(word: str, gap: str = " ") -> str:
    """Render a word as 6-row ASCII art using the ANSI Shadow font."""
    rows = [gap.join(_FONT[ch][r] for ch in word) for r in range(6)]
    return "\n".join(rows)


def _ascii_width(word: str, gap: str = " ") -> int:
    """Return the rendered pixel width (columns) of an ASCII-art word."""
    return len(gap.join(_FONT[ch][0] for ch in word))


def _app_version() -> str:
    """Return the installed package version, falling back gracefully."""
    try:
        return _pkg_version("linkedin-networking-cli")
    except PackageNotFoundError:
        return "0.1.0"

# Initialize logging system first
from utils.logging import LoggerSetup, get_logger

LoggerSetup.setup()
logger = get_logger(__name__)

from automation.diagnostics import _artifacts_dir
from automation.linkedin import LinkedInAutomation
from automation.linkedin_mappings import (
    get_industry_display_names,
    get_industry_id,
    get_industry_name_from_id,
    get_location_display_names,
    get_location_name_from_urn,
    get_location_urn,
    get_network_display_names,
    get_network_name_from_value,
    get_network_value,
)
from cli.automation_errors import describe_automation_error, evidence_reference
from cli.helpers import (
    acceptance_rate,
    campaign_get_field,
    contacts_csv_filename,
    csv_value,
    mask_email,
    write_contacts_csv,
)
from config.settings import AppSettings
from database.operations import DatabaseManager


class LinkedInCLI:
    """LinkedIn Networking CLI with InquirerPy interface"""

    def __init__(self):
        self.console = Console()
        logger.info("Initializing LinkedIn CLI application")
        # Initialize real components
        try:
            self.settings = AppSettings()
            self.db_manager = DatabaseManager(str(self.settings.db_path))
            logger.info("LinkedIn CLI components initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing components: {e}", exc_info=True)
            self.console.print(f"[red]Error initializing components: {e}[/red]")
            self.console.print("[yellow]Running in demo mode with mock data[/yellow]")
            self.db_manager = None
            self.settings = None

    @staticmethod
    def _campaign_get_field(campaign, attr, default=None):
        """Read campaign attribute regardless of backing type.

        Delegates to :func:`cli.helpers.campaign_get_field`; kept as a static
        method so existing call sites and the class surface are unchanged.
        """
        return campaign_get_field(campaign, attr, default)

    @staticmethod
    def _format_evidence_reference(exc=None):
        """Describe where the saved diagnostics evidence lives, for the user.

        Delegates to :func:`cli.automation_errors.evidence_reference`, passing
        the diagnostics layer's ``_artifacts_dir`` (resolved from the module at
        call time so tests can monkeypatch it) as the directory resolver.
        """
        return evidence_reference(exc, artifacts_dir=_artifacts_dir)

    def _report_automation_failure(self, exc, action_label):
        """Map an automation failure to a distinct, user-friendly stop message.

        The (headline, evidence-reference) mapping is shared with the TUI via
        :func:`cli.automation_errors.describe_automation_error`; this method
        keeps only the CLI's presentation: traceback logging and Rich escaping.
        Prints the message and returns; the caller then hard-stops the run with
        no interactive waiting and no traceback shown to the user.
        """
        # Keep the full traceback in the file logs for postmortem, but off the
        # user's console: the console handler is at WARNING, so logging this at
        # INFO preserves it in linkedin_cli.log without dumping a traceback to
        # the terminal (the user only sees the friendly message below).
        logger.info(
            "Automation stopped during %s: %s", action_label, exc, exc_info=True
        )
        headline, evidence_ref = describe_automation_error(
            exc, action_label, artifacts_dir=_artifacts_dir
        )

        # Escape both lines: the fixed headlines carry no brackets (escaping is
        # a no-op for them), but the dynamic exception text can contain square
        # brackets (e.g. a CSS attribute selector ``a[href]``) and evidence_ref
        # holds filesystem paths — Rich would otherwise parse those as markup
        # tags and silently drop them.
        self.console.print(f"[red]{_rich_escape(headline)}[/red]")
        self.console.print(f"[yellow]{_rich_escape(evidence_ref)}[/yellow]")

    def _welcome_badge(self):
        """Rounded blue 'in' badge with the small 'LinkedIn' label beside it."""
        badge = Text()
        badge.append("╭────╮\n", style=BRAND_BLUE)
        badge.append("│ ", style=BRAND_BLUE)
        badge.append("in", style="bold white")
        badge.append(" │", style=BRAND_BLUE)
        badge.append("   ")
        badge.append("LinkedIn", style="bold white")
        badge.append("\n")
        badge.append("╰────╯", style=BRAND_BLUE)
        return badge

    def _welcome_hero(self, content_width):
        """Big ASCII title, scaled to the available width."""
        hero_style = f"bold {BRAND_BLUE}"
        # Widest first: full "NETWORKING CLI" on one line.
        if content_width >= _ascii_width("NETWORKING CLI"):
            return Align.center(Text(_render_word("NETWORKING CLI"), style=hero_style))
        # Otherwise stack the two words, both still large.
        if content_width >= _ascii_width("NETWORKING"):
            return Group(
                Align.center(Text(_render_word("NETWORKING"), style=hero_style)),
                Text(""),
                Align.center(Text(_render_word("CLI"), style=hero_style)),
            )
        # Too narrow for ASCII art: plain bold wordmark.
        return Align.center(Text("Networking CLI", style=hero_style))

    def display_welcome(self):
        """Display a styled, width-aware welcome banner."""
        version = _app_version()
        # Panel uses padding=(1, 2) plus 1-col borders, so usable width = width - 6.
        content_width = self.console.width - 6

        subtitle = Text.assemble(
            ("Professional networking automation", "italic white"),
            ("   ·   ", "dim"),
            (f"v{version}", f"bold {BRAND_BLUE}"),
        )
        body = Group(
            Align.center(self._welcome_badge()),
            Text(""),
            self._welcome_hero(content_width),
            Text(""),
            Align.center(subtitle),
        )

        self.console.print(
            Panel(
                body,
                border_style=BRAND_BLUE,
                padding=(1, 2),
                box=ROUNDED,
            )
        )
        self.console.print(
            Align.center(
                Text("Press ↑ ↓ to navigate · Enter to select", style="dim")
            )
        )
        print()

    def main_menu(self):
        """Main menu with InquirerPy"""
        while True:
            choice = inquirer.select(
                message="What would you like to do?",
                choices=[
                    Choice(
                        value="dashboard",
                        name="📊 Dashboard - View campaign statistics",
                    ),
                    Choice(
                        value="create",
                        name="🎯 Create Campaign - Setup new networking campaign",
                    ),
                    Choice(
                        value="manage",
                        name="📋 Manage Campaigns - View and edit existing campaigns",
                    ),
                    Choice(
                        value="execute",
                        name="🚀 Execute Campaign - Run networking automation",
                    ),
                    Choice(
                        value="checker",
                        name="🔍 Check Connections - Monitor pending connection status",
                    ),
                    Choice(
                        value="extract_profiles",
                        name="📊 Extract Profile Data - Get detailed profile information",
                    ),
                    Separator(),
                    Choice(
                        value="settings", name="🔧 Settings - Configure application"
                    ),
                    Separator(),
                    Choice(value="exit", name="❌ Exit"),
                ],
                default="dashboard",
            ).execute()

            if choice == "dashboard":
                self.show_dashboard()
            elif choice == "create":
                self.create_campaign()
            elif choice == "manage":
                self.manage_campaigns()
            elif choice == "execute":
                self.execute_campaign()
            elif choice == "checker":
                self.connection_checker()
            elif choice == "extract_profiles":
                self.extract_profile_data()
            elif choice == "settings":
                self.show_settings()
            elif choice == "exit":
                self.console.print("[yellow]Goodbye! 👋[/yellow]")
                break

    def show_dashboard(self):
        """Display dashboard with campaign statistics"""
        if self.db_manager:
            try:
                stats = self.db_manager.get_dashboard_stats()
            except Exception as e:
                self.console.print(f"[red]Error loading stats: {e}[/red]")
                stats = {
                    "active_campaigns": 0,
                    "total_campaigns": 0,
                    "total_sent": 0,
                    "total_accepted": 0,
                    "acceptance_rate": 0,
                }
        else:
            # Mock data fallback
            stats = {
                "active_campaigns": 2,
                "total_campaigns": 5,
                "total_sent": 45,
                "total_accepted": 12,
                "acceptance_rate": 26.7,
            }

        footer = (
            "\n\n[dim]💡 Demo mode with mock data (database unavailable)[/dim]"
            if not self.db_manager
            else ""
        )
        self.console.print(
            Panel(
                f"[bold]📊 Dashboard Statistics[/bold]\n\n"
                f"[cyan]Active Campaigns:[/cyan] {stats['active_campaigns']}/{stats['total_campaigns']}\n"
                f"[cyan]Total Connections:[/cyan] {stats['total_sent']} sent, {stats['total_accepted']} accepted\n"
                f"[cyan]Success Rate:[/cyan] {stats['acceptance_rate']}%"
                f"{footer}",
                title="LinkedIn Networking Dashboard",
                border_style="blue",
            )
        )

        inquirer.confirm(message="Press Enter to continue...", default=True).execute()

    def create_campaign(self):
        """Create new campaign with InquirerPy forms"""
        self.console.print("[bold cyan]🎯 Creating New Campaign[/bold cyan]\n")

        # Campaign name
        name = inquirer.text(
            message="Campaign name:",
            validate=lambda x: len(x.strip()) > 0 or "Campaign name cannot be empty",
        ).execute()

        # Description
        description = inquirer.text(
            message="Campaign description (optional):",
        ).execute()

        # Targeting criteria
        keywords = inquirer.text(
            message="Target keywords (e.g., 'software engineer', optional):",
        ).execute()

        # Location filter with proper geoUrn mapping.
        # Dynamic online search is offered as an option (requires login).
        SEARCH_ONLINE = "🔎 Search location online (requires login)"
        CUSTOM_GEO = "Other (enter custom geoUrn)"
        location_choices = get_location_display_names()
        location_choices.append(SEARCH_ONLINE)
        location_choices.append(CUSTOM_GEO)

        location_display = inquirer.select(
            message="Target location:",
            choices=location_choices,
            default="Any",
        ).execute()

        # Handle custom geoUrn input
        custom_geo_urn = None

        if location_display == SEARCH_ONLINE:
            loc_name, geo = self._search_location_online()
            if geo:
                custom_geo_urn = geo
                location_display = loc_name
            else:
                # Fall back to manual entry if the search yielded nothing.
                location_display = CUSTOM_GEO

        if location_display == CUSTOM_GEO:
            self.console.print()
            self.console.print("[yellow]💡 Tip: Find geoUrn codes by:[/yellow]")
            self.console.print("[dim]   1. Search on LinkedIn with location filter[/dim]")
            self.console.print("[dim]   2. Look at URL: geoUrn=[\"CODE\"][/dim]")
            self.console.print("[dim]   3. Use the CODE in the URL[/dim]")
            self.console.print()

            custom_geo_urn = inquirer.text(
                message="Enter geoUrn code (e.g., '90000084'):",
                validate=lambda x: x.strip().isdigit() or "Must be a numeric code",
            ).execute()

            custom_location_name = inquirer.text(
                message="Enter location name (for display):",
                default=f"Custom Location ({custom_geo_urn})",
            ).execute()

            location_display = custom_location_name

        # Network filter (connection degree)
        network_choices = get_network_display_names()
        network_display = inquirer.select(
            message="Connection degree:",
            choices=network_choices,
            default="1st + 2nd degree connections",
        ).execute()

        # Industry filter with proper ID mapping
        industry_choices = get_industry_display_names()
        industry_display = inquirer.select(
            message="Target industry:",
            choices=industry_choices,
            default="Any",
        ).execute()

        # Campaign settings
        daily_limit = inquirer.number(
            message="Daily connection limit:",
            min_allowed=1,
            max_allowed=100,
            default=20,
        ).execute()

        message_template = inquirer.text(
            message="Connection message template:",
            default="Hi {name}, I'd like to connect with you!",
            validate=lambda x: "{name}" in x
            or "Message must contain {name} placeholder",
        ).execute()

        # Get the actual values from display names
        if custom_geo_urn:
            # User entered a custom geoUrn
            geo_urn = custom_geo_urn.strip()
        else:
            # Use mapped location
            geo_urn = get_location_urn(location_display) if location_display != "Any" else None

        network_value = get_network_value(network_display)
        industry_id = get_industry_id(industry_display) if industry_display != "Any" else None

        # Show campaign summary
        self.console.print(
            Panel(
                f"[bold]📋 Campaign Summary[/bold]\n\n"
                f"[cyan]Name:[/cyan] {name}\n"
                f"[cyan]Description:[/cyan] {description or 'None'}\n"
                f"[cyan]Keywords:[/cyan] {keywords or 'Any'}\n"
                f"[cyan]Location:[/cyan] {location_display}\n"
                f"[cyan]Connection Degree:[/cyan] {network_display}\n"
                f"[cyan]Industry:[/cyan] {industry_display}\n"
                f"[cyan]Daily Limit:[/cyan] {daily_limit}\n"
                f"[cyan]Message:[/cyan] {message_template}",
                title="Campaign Created",
                border_style="green",
            )
        )

        # Create campaign data with new field structure
        campaign_data = {
            "name": name,
            "description": description or None,
            "keywords": keywords or None,
            # New fields
            "geo_urn": geo_urn,
            "location_display": location_display if location_display != "Any" else None,
            "network": network_value,
            "network_display": network_display,
            "industry_ids": industry_id,  # Single ID for now (could be multiple in future)
            "industry_display": industry_display if industry_display != "Any" else None,
            # Settings
            "daily_limit": daily_limit,
            "message_template": message_template,
        }

        if self.db_manager:
            try:
                campaign = self.db_manager.create_campaign(campaign_data)
                self.console.print(
                    f"[green]✅ Campaign '{campaign.name}' created successfully![/green]"
                )
                self.console.print(f"[blue]Campaign ID: {campaign.id}[/blue]")
            except Exception as e:
                self.console.print(f"[red]❌ Error creating campaign: {e}[/red]")
        else:
            self.console.print("[green]✅ Campaign created successfully![/green]")
            self.console.print(
                "[blue]💡 Demo mode: Would save to SQLite database[/blue]"
            )

        inquirer.confirm(message="Press Enter to continue...").execute()

    def manage_campaigns(self):
        """Manage existing campaigns (looped: stays here until you go back)."""
        while True:
            if self.db_manager:
                try:
                    campaigns = self.db_manager.get_campaigns(active_only=False)
                except Exception as e:
                    self.console.print(f"[red]Error loading campaigns: {e}[/red]")
                    campaigns = []
            else:
                # Mock campaigns for demo
                Campaign = namedtuple(
                    "Campaign",
                    ["id", "name", "active", "total_sent", "total_accepted", "daily_limit"],
                )
                campaigns = [
                    Campaign(1, "Tech Professionals", True, 25, 8, 20),
                    Campaign(2, "Marketing Leads", False, 20, 4, 15),
                    Campaign(3, "Sales Prospects", True, 30, 12, 25),
                ]

            if not campaigns:
                self.console.print(
                    "[yellow]No campaigns yet. Use 'Create Campaign' to add one.[/yellow]"
                )
                inquirer.confirm(message="Press Enter to continue...").execute()
                return

            # Select campaign to manage
            campaign_choices = []
            for campaign in campaigns:
                status = "🟢 Active" if campaign.active else "🔴 Inactive"
                rate = acceptance_rate(campaign.total_sent, campaign.total_accepted)
                campaign_choices.append(
                    Choice(
                        value=campaign,
                        name=f"{campaign.name} - {status} ({campaign.total_sent} sent, {rate:.1f}% rate)",
                    )
                )

            campaign_choices.append(Separator())
            campaign_choices.append(Choice(value="back", name="🔙 Back to main menu"))

            selected = inquirer.select(
                message="Select campaign to manage:", choices=campaign_choices
            ).execute()

            if selected == "back":
                return

            # Campaign actions
            action = inquirer.select(
                message=f"What would you like to do with '{selected.name}'?",
                choices=[
                    Choice(value="view", name="📊 View detailed statistics"),
                    Choice(value="toggle", name="🔄 Toggle active/inactive status"),
                    Choice(value="edit", name="📝 Edit campaign settings"),
                    Choice(value="export", name="📤 Export contacts to CSV"),
                    Choice(value="delete", name="🗑️ Delete campaign"),
                    Separator(),
                    Choice(value="back", name="🔙 Back to campaign list"),
                ],
            ).execute()

            if action == "back":
                # Return to the campaign list (loop reloads fresh data).
                continue
            elif action == "view":
                self.view_campaign_details(selected)
            elif action == "toggle":
                self.toggle_campaign(selected)
            elif action == "edit":
                self.edit_campaign(selected)
            elif action == "export":
                self.export_contacts(selected)
            elif action == "delete":
                self.delete_campaign(selected)

            inquirer.confirm(message="Press Enter to continue...").execute()
            # Loop continues -> reload the campaign list, reflecting any changes.

    def toggle_campaign(self, campaign):
        """Toggle a campaign's active/inactive status in the database."""
        new_active = not campaign.active
        new_status = "activated" if new_active else "deactivated"

        if not self.db_manager:
            self.console.print(
                f"[green]✅ Campaign '{campaign.name}' {new_status}![/green]"
            )
            self.console.print("[blue]💡 Demo mode: Would update database[/blue]")
            return

        try:
            updated = self.db_manager.update_campaign(campaign.id, {"active": new_active})
            if updated:
                self.console.print(
                    f"[green]✅ Campaign '{campaign.name}' {new_status}![/green]"
                )
            else:
                self.console.print(
                    f"[red]❌ Campaign '{campaign.name}' not found.[/red]"
                )
        except Exception as e:
            self.console.print(f"[red]❌ Error updating campaign: {e}[/red]")

    def edit_campaign(self, campaign):
        """Edit an existing campaign's settings and persist the changes."""
        self.console.print(f"[bold cyan]📝 Editing '{campaign.name}'[/bold cyan]\n")
        self.console.print("[dim]Press Enter to keep the current value.[/dim]\n")

        name = inquirer.text(
            message="Campaign name:",
            default=campaign.name or "",
            validate=lambda x: len(x.strip()) > 0 or "Campaign name cannot be empty",
        ).execute()

        description = inquirer.text(
            message="Description:",
            default=self._campaign_get_field(campaign, "description", "") or "",
        ).execute()

        # --- Targeting filters ---
        keywords = inquirer.text(
            message="Target keywords (optional):",
            default=self._campaign_get_field(campaign, "keywords", "") or "",
        ).execute()

        # Location: preselect the current location based on the stored geo_urn.
        SEARCH_ONLINE = "🔎 Search location online (requires login)"
        current_geo_urn = self._campaign_get_field(campaign, "geo_urn", None)
        current_location_name = (
            get_location_name_from_urn(current_geo_urn) if current_geo_urn else "Any"
        )
        location_choices = get_location_display_names()
        if current_location_name not in location_choices:
            location_choices.append(current_location_name)
        location_choices.append(SEARCH_ONLINE)
        location_display = inquirer.select(
            message="Target location:",
            choices=location_choices,
            default=current_location_name,
        ).execute()

        edited_geo_urn = None  # set only when an online search picks a result
        if location_display == SEARCH_ONLINE:
            loc_name, geo = self._search_location_online()
            if geo:
                edited_geo_urn = geo
                location_display = loc_name
            else:
                # Keep the previous location if the search was cancelled.
                location_display = current_location_name

        # Connection degree.
        current_network = self._campaign_get_field(campaign, "network", '["F","S"]')
        network_display = inquirer.select(
            message="Connection degree:",
            choices=get_network_display_names(),
            default=get_network_name_from_value(current_network),
        ).execute()

        # Industry.
        current_industry_id = self._campaign_get_field(campaign, "industry_ids", None)
        current_industry_name = (
            get_industry_name_from_id(current_industry_id)
            if current_industry_id
            else "Any"
        )
        industry_choices = get_industry_display_names()
        if current_industry_name not in industry_choices:
            industry_choices.append(current_industry_name)
        industry_display = inquirer.select(
            message="Target industry:",
            choices=industry_choices,
            default=current_industry_name,
        ).execute()

        daily_limit = inquirer.number(
            message="Daily connection limit:",
            min_allowed=1,
            max_allowed=100,
            default=campaign.daily_limit,
        ).execute()

        message_template = inquirer.text(
            message="Connection message template:",
            default=self._campaign_get_field(
                campaign, "message_template", "Hi {name}, I'd like to connect with you!"
            ),
            validate=lambda x: "{name}" in x
            or "Message must contain {name} placeholder",
        ).execute()

        # Resolve display names back into stored values. An online-search pick
        # provides its geoUrn directly; otherwise map from the curated list.
        if edited_geo_urn:
            geo_urn = edited_geo_urn
        else:
            geo_urn = get_location_urn(location_display) if location_display != "Any" else None
        network_value = get_network_value(network_display)
        industry_id = (
            get_industry_id(industry_display) if industry_display != "Any" else None
        )

        updates = {
            "name": name.strip(),
            "description": description.strip() or None,
            "keywords": keywords.strip() or None,
            "geo_urn": geo_urn,
            "location_display": location_display if location_display != "Any" else None,
            "network": network_value,
            "network_display": network_display,
            "industry_ids": industry_id,
            "industry_display": industry_display if industry_display != "Any" else None,
            "daily_limit": int(daily_limit),
            "message_template": message_template,
        }

        if not self.db_manager:
            self.console.print("[green]✅ Campaign updated![/green]")
            self.console.print("[blue]💡 Demo mode: Would save to database[/blue]")
            return

        try:
            updated = self.db_manager.update_campaign(campaign.id, updates)
            if updated:
                self.console.print(
                    f"[green]✅ Campaign '{updated.name}' updated successfully![/green]"
                )
            else:
                self.console.print(
                    f"[red]❌ Campaign '{campaign.name}' not found.[/red]"
                )
        except Exception as e:
            self.console.print(f"[red]❌ Error updating campaign: {e}[/red]")

    def delete_campaign(self, campaign):
        """Delete a campaign (and its contacts). Returns True if it was removed."""
        confirm = inquirer.confirm(
            message=f"⚠️  Are you sure you want to delete '{campaign.name}'? "
            "This also removes its contacts.",
            default=False,
        ).execute()

        if not confirm:
            self.console.print("[yellow]Deletion cancelled.[/yellow]")
            return False

        if not self.db_manager:
            self.console.print(f"[red]🗑️ Campaign '{campaign.name}' deleted.[/red]")
            self.console.print("[blue]💡 Demo mode: Would delete from database[/blue]")
            return False

        try:
            deleted = self.db_manager.delete_campaign(campaign.id)
            if deleted:
                self.console.print(
                    f"[red]🗑️ Campaign '{campaign.name}' deleted.[/red]"
                )
                return True
            else:
                self.console.print(
                    f"[red]❌ Campaign '{campaign.name}' not found.[/red]"
                )
                return False
        except Exception as e:
            self.console.print(f"[red]❌ Error deleting campaign: {e}[/red]")
            return False

    def export_contacts(self, campaign):
        """Export a campaign's contacts to a CSV file."""
        if not self.db_manager:
            self.console.print(
                "[blue]💡 Demo mode: contact export requires a database.[/blue]"
            )
            return

        try:
            contacts = self.db_manager.get_contacts(campaign_id=campaign.id)
        except Exception as e:
            self.console.print(f"[red]❌ Error loading contacts: {e}[/red]")
            return

        if not contacts:
            self.console.print(
                f"[yellow]No contacts found for '{campaign.name}' yet.[/yellow]"
            )
            return

        default_path = str(Path.cwd() / contacts_csv_filename(campaign.name))

        output_path = inquirer.text(
            message="Save CSV to:",
            default=default_path,
        ).execute()

        try:
            path = Path(output_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            # Field list and writing logic are shared with the TUI export
            # (cli.helpers.write_contacts_csv); only the path prompt is ours.
            write_contacts_csv(path, contacts)
            self.console.print(
                f"[green]✅ Exported {len(contacts)} contacts to {path}[/green]"
            )
        except Exception as e:
            self.console.print(f"[red]❌ Error writing CSV: {e}[/red]")

    @staticmethod
    def _csv_value(value):
        """Normalize a value for CSV output.

        Delegates to :func:`cli.helpers.csv_value`.
        """
        return csv_value(value)

    def view_campaign_details(self, campaign):
        """View detailed campaign information"""
        rate = acceptance_rate(campaign.total_sent, campaign.total_accepted)

        self.console.print(
            Panel(
                f"[bold]📊 Campaign Details[/bold]\n\n"
                f"[cyan]Name:[/cyan] {campaign.name}\n"
                f"[cyan]Status:[/cyan] {'🟢 Active' if campaign.active else '🔴 Inactive'}\n"
                f"[cyan]Daily Limit:[/cyan] {campaign.daily_limit}\n\n"
                f"[cyan]Connections Sent:[/cyan] {campaign.total_sent}\n"
                f"[cyan]Connections Accepted:[/cyan] {campaign.total_accepted}\n"
                f"[cyan]Acceptance Rate:[/cyan] {rate:.1f}%\n"
                f"[cyan]Connections Pending:[/cyan] {campaign.total_sent - campaign.total_accepted}",
                title=f"Campaign: {campaign.name}",
                border_style="blue",
            )
        )
        # Note: the caller (manage_campaigns) prompts to continue.

    def execute_campaign(self):
        """Execute campaign selection"""
        if self.db_manager:
            try:
                campaigns = self.db_manager.get_campaigns(active_only=True)
            except Exception as e:
                self.console.print(f"[red]Error loading campaigns: {e}[/red]")
                campaigns = []
        else:
            # Mock active campaigns
            campaigns = [
                {"name": "Tech Professionals", "daily_limit": 20},
                {"name": "Sales Prospects", "daily_limit": 25},
            ]

        if not campaigns:
            self.console.print("[yellow]No active campaigns found.[/yellow]")
            inquirer.confirm(message="Press Enter to continue...").execute()
            return

        campaign_choices = []
        for campaign in campaigns:
            name = self._campaign_get_field(campaign, "name", "Unnamed campaign")
            daily_limit = self._campaign_get_field(campaign, "daily_limit", 0)
            campaign_choices.append(
                Choice(value=campaign, name=f"{name} (Daily limit: {daily_limit})")
            )

        selected = inquirer.select(
            message="Select campaign to execute:", choices=campaign_choices
        ).execute()

        # Execution confirmation
        selected_name = self._campaign_get_field(selected, "name", "Unnamed campaign")
        confirm = inquirer.confirm(
            message=f"⚠️  Start automation for '{selected_name}'? This will interact with LinkedIn.",
            default=True,
        ).execute()

        if confirm:
            if not self.db_manager or not self.settings:
                self.console.print(
                    "[red]Automation requires database access and app settings. Please check your installation.[/red]"
                )
                inquirer.confirm(message="Press Enter to continue...").execute()
                return

            if not self.settings.validate_credentials():
                self.console.print(
                    "[yellow]LinkedIn credentials env vars not set. We will wait for you to complete login manually in Chrome.[/yellow]"
                )

            self.console.print(
                f"[green]🚀 Starting execution for '{selected_name}'...[/green]"
            )

            def progress_update(message: str) -> None:
                self.console.print(f"[cyan]{message}[/cyan]")

            # The interactive limit stays the campaign-wide search cap; the run
            # core (shared with the non-interactive `run` subcommand) applies it
            # and owns all rate-limit/daily-cap/session behavior via the
            # automation layer. Reading settings here is a side-effect-free peek.
            search_limit = self.settings.get_automation_settings().get(
                "search_limit", 100
            )

            try:
                # By the time this runs, InquirerPy's prompt loop has finished
                # and no event loop is running, so a plain asyncio.run is safe —
                # the same shape connection_checker and extract_profile_data use.
                automation_result = asyncio.run(
                    self._run_campaign_automation(
                        selected, search_limit, progress_update
                    )
                )
            except Exception as automation_error:
                # Hard-stop with evidence: distinct message per typed exception,
                # generic fallback otherwise. No interactive wait, no traceback.
                self._report_automation_failure(
                    automation_error, "campaign execution"
                )
                return

            status = automation_result.get("status") if automation_result else None

            if status == "login_failed":
                self.console.print(
                    "[red]Login to LinkedIn failed. Verify credentials and any multi-factor prompts.[/red]"
                )
            elif status == "no_profiles":
                self.console.print(
                    "[yellow]No profiles matched the campaign criteria. Review your filters and try again.[/yellow]"
                )
            elif status == "safety_stop":
                sent = automation_result.get("sent", 0)
                possibly_sent = automation_result.get("possibly_sent", 0)
                self.console.print(
                    "[red]Automation stopped early: LinkedIn presented a "
                    "CAPTCHA/challenge. Resolve it in the browser before running "
                    f"again. Progress so far was saved (sent {sent}, possibly "
                    f"sent {possibly_sent}).[/red]"
                )
            elif status == "success":
                sent = automation_result.get("sent", 0)
                possibly_sent = automation_result.get("possibly_sent", 0)
                failed = automation_result.get("failed", 0)
                existing = automation_result.get("existing", 0)
                profiles_found = automation_result.get("profiles", 0)
                total = automation_result.get(
                    "total_processed", sent + possibly_sent + failed + existing
                )
                summary_lines = [
                    "[bold]Automation summary[/bold]",
                    "",
                    f"Profiles scanned: {profiles_found}",
                    f"Requests sent: {sent}",
                ]
                # Only surface the conservative "possibly sent" line when it
                # happened, so a clean run's summary stays uncluttered.
                if possibly_sent:
                    summary_lines.append(f"Possibly sent (renderer wedged): {possibly_sent}")
                summary_lines += [
                    f"Already contacted: {existing}",
                    f"Failures: {failed}",
                    f"Total processed: {total}",
                ]
                summary = "\n".join(summary_lines)
                self.console.print(
                    Panel(summary, title="Run complete", border_style="green")
                )
            else:
                self.console.print(
                    f"[yellow]Automation finished with status: {status}[/yellow]"
                )
        else:
            self.console.print("[yellow]Execution cancelled.[/yellow]")

        inquirer.confirm(message="Press Enter to continue...").execute()

    async def _run_campaign_automation(
        self, campaign, search_limit, progress_update, max_sends=None
    ):
        """Run one campaign's search-and-connect pass — the shared run core.

        Both the interactive Execute Campaign flow and the non-interactive
        ``run`` subcommand call this. It bypasses only the interactive prompts;
        login, search, and all rate-limit/daily-cap/session behavior stay in the
        automation layer (card-first connect from the result cards, falling back
        to the profile-page path for cards with no Connect control — issue #25).
        ``search_limit`` caps the results scanned; ``max_sends`` (optional)
        additionally caps the invitations sent this run. Returns the automation
        result dict with a ``status`` key (``safety_stop`` when the run was cut
        short by a CAPTCHA/challenge to protect the account).
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
        """
        ref = str(reference).strip()

        if ref.isdigit():
            campaign = self.db_manager.get_campaign(int(ref))
            if campaign is not None:
                return campaign

        campaigns = self.db_manager.get_campaigns(active_only=False)
        for campaign in campaigns:
            if self._campaign_get_field(campaign, "name") == ref:
                return campaign
        lowered = ref.lower()
        for campaign in campaigns:
            name = self._campaign_get_field(campaign, "name") or ""
            if name.lower() == lowered:
                return campaign
        return None

    def run_noninteractive(self, campaign_reference, max_invites=None):
        """Execute a campaign without prompts — the ``run`` subcommand path.

        Resolves the campaign by id or name and drives the same automation as
        the interactive flow via :meth:`_run_campaign_automation`. The scan uses
        the same ``search_limit`` setting as the interactive flow; invitations
        *sent* are capped at ``max_invites`` (default: the campaign's
        ``daily_limit``). Progress goes to stdout; failures print to stderr.
        Returns a process exit code (0 success, non-zero on any failure —
        including a protective CAPTCHA/challenge stop, so schedulers can alert).
        """
        if not self.db_manager or not self.settings:
            print(
                "Error: automation requires database access and app settings.",
                file=sys.stderr,
            )
            return 1

        # Non-interactive runs cannot fall back to a manual browser login, so
        # configured credentials are mandatory here (unlike the interactive
        # flow, which only warns and waits for a human).
        if not self.settings.validate_credentials():
            print(
                "Error: LinkedIn credentials are not configured. Set "
                "LINKEDIN_EMAIL and LINKEDIN_PASSWORD before running.",
                file=sys.stderr,
            )
            return 1

        campaign = self._resolve_campaign(campaign_reference)
        if campaign is None:
            print(
                f"Error: no campaign matching '{campaign_reference}'.",
                file=sys.stderr,
            )
            return 1

        campaign_name = self._campaign_get_field(campaign, "name", "campaign")
        # --max caps invitations SENT; the scan budget stays the interactive
        # flow's search_limit setting so repeat runs can skip past
        # already-contacted results instead of burning the cap on them.
        max_sends = (
            max_invites
            if max_invites is not None
            else self._campaign_get_field(campaign, "daily_limit", 20)
        )
        search_limit = self.settings.get_automation_settings().get(
            "search_limit", 100
        )

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

    def show_settings(self):
        """Show application settings"""
        setting = inquirer.select(
            message="Settings & Configuration:",
            choices=[
                Choice(value="credentials", name="🔐 LinkedIn credentials status"),
                Choice(value="browser", name="🌐 Browser automation settings"),
                Choice(value="limits", name="⚡ Rate limiting settings"),
                Choice(value="data", name="📁 Data directory information"),
                Choice(value="location_lookup", name="🔎 Look up location code (online)"),
                Separator(),
                Choice(value="back", name="🔙 Back to main menu"),
            ],
        ).execute()

        if setting == "credentials":
            self.show_credentials_settings()
        elif setting == "browser":
            self.show_browser_settings()
        elif setting == "limits":
            self.show_limits_settings()
        elif setting == "data":
            self.show_data_settings()
        elif setting == "location_lookup":
            self.location_lookup()

        if setting != "back":
            inquirer.confirm(message="Press Enter to continue...").execute()

    @staticmethod
    def _mask_email(email):
        """Mask an email for display, e.g. 'joh***@example.com'.

        Delegates to :func:`cli.helpers.mask_email`.
        """
        return mask_email(email)

    def show_credentials_settings(self):
        """Show real LinkedIn credential status from the environment."""
        if not self.settings:
            self.console.print(
                Panel(
                    "[bold]🔐 LinkedIn Credentials[/bold]\n\n"
                    "[cyan]Status:[/cyan] 🔴 Not configured (demo mode)\n\n"
                    "[dim]Set via environment variables:\n"
                    'export LINKEDIN_EMAIL="your-email"\n'
                    'export LINKEDIN_PASSWORD="your-password"[/dim]',
                    title="Credentials Status",
                    border_style="blue",
                )
            )
            return

        email = self.settings.linkedin_email
        has_password = bool(self.settings.linkedin_password)
        configured = bool(email) and has_password
        status = "🟢 Configured" if configured else "🔴 Not configured"

        self.console.print(
            Panel(
                "[bold]🔐 LinkedIn Credentials[/bold]\n\n"
                f"[cyan]Status:[/cyan] {status}\n"
                f"[cyan]Email:[/cyan] {self._mask_email(email)}\n"
                f"[cyan]Password:[/cyan] {'Set' if has_password else 'Not set'}\n\n"
                "[dim]Set via environment variables:\n"
                'export LINKEDIN_EMAIL="your-email"\n'
                'export LINKEDIN_PASSWORD="your-password"[/dim]',
                title="Credentials Status",
                border_style="blue",
            )
        )

    def show_browser_settings(self):
        """Show the real browser configuration."""
        if not self.settings:
            self.console.print(
                "[yellow]Browser settings unavailable in demo mode.[/yellow]"
            )
            return

        b = self.settings.get_browser_settings()
        viewport = b.get("viewport", {})
        self.console.print(
            Panel(
                "[bold]🌐 Browser Settings[/bold]\n\n"
                f"[cyan]Channel:[/cyan] {b.get('channel') or 'bundled Chromium'}\n"
                f"[cyan]Executable:[/cyan] {b.get('executable_path') or 'default'}\n"
                f"[cyan]Headless Mode:[/cyan] {b.get('headless')}\n"
                f"[cyan]Viewport:[/cyan] {viewport.get('width')}x{viewport.get('height')}\n"
                f"[cyan]User Data Dir:[/cyan] {b.get('user_data_dir')}",
                title="Browser Configuration",
                border_style="blue",
            )
        )

    def show_limits_settings(self):
        """Show the real rate-limiting / automation configuration."""
        if not self.settings:
            self.console.print(
                "[yellow]Rate limiting settings unavailable in demo mode.[/yellow]"
            )
            return

        a = self.settings.get_automation_settings()
        daily_limit = a.get("daily_connection_limit")

        # Show today's persisted usage so the user can see remaining quota.
        used_today_line = ""
        if self.db_manager:
            from datetime import date

            used_today = self.db_manager.get_daily_connection_count(
                date.today().isoformat()
            )
            used_today_line = (
                f"[cyan]Used Today:[/cyan] {used_today}/{daily_limit}\n"
            )

        self.console.print(
            Panel(
                "[bold]⚡ Rate Limiting[/bold]\n\n"
                f"[cyan]Connection Delay:[/cyan] {a.get('connection_delay_min')}-{a.get('connection_delay_max')} seconds\n"
                f"[cyan]Daily Connection Limit:[/cyan] {daily_limit}\n"
                f"{used_today_line}"
                f"[cyan]Inter-session Cooldown:[/cyan] {a.get('connection_cooldown')} seconds\n"
                f"[cyan]Search Limit:[/cyan] {a.get('search_limit')}",
                title="Rate Limiting Settings",
                border_style="blue",
            )
        )

    def show_data_settings(self):
        """Show the real data storage locations."""
        if not self.settings:
            self.console.print(
                "[yellow]Data directory information unavailable in demo mode.[/yellow]"
            )
            return

        self.console.print(
            Panel(
                "[bold]📁 Data Storage[/bold]\n\n"
                f"[cyan]App Directory:[/cyan] {self.settings.app_dir}\n"
                f"[cyan]Database:[/cyan] {self.settings.db_path}\n"
                f"[cyan]Session Data:[/cyan] {self.settings.session_path}\n"
                f"[cyan]Browser Data:[/cyan] {Path(self.settings.app_dir) / 'browser_data'}",
                title="Data Directory Information",
                border_style="blue",
            )
        )

    def location_lookup(self):
        """Look up a LinkedIn location geoUrn code online via the Voyager API.

        Requires an authenticated session. Useful for finding codes that are
        not in the curated list so they can be pasted into a campaign's
        "Other (enter custom geoUrn)" option.
        """
        if not self.db_manager or not self.settings:
            self.console.print(
                "[red]Location lookup requires database access and app settings.[/red]"
            )
            return

        query = inquirer.text(
            message="Search location (e.g. 'Madrid', 'Greater Tokyo'):",
            validate=lambda x: len(x.strip()) > 0 or "Enter a search term",
        ).execute()

        results = self._run_location_search(query.strip())
        if results is None:
            self.console.print("[red]❌ Could not authenticate with LinkedIn.[/red]")
            return
        if not results:
            self.console.print(f"[yellow]No locations found for '{query}'.[/yellow]")
            return

        lines = ["[bold]🔎 Location results[/bold]\n"]
        for item in results:
            lines.append(
                f"[cyan]{item.get('name', '?')}[/cyan]  →  geoUrn [green]{item.get('geoUrn', '?')}[/green]"
            )
        lines.append(
            "\n[dim]Use a code via Create/Edit Campaign → 'Other (enter custom geoUrn)'.[/dim]"
        )
        self.console.print(
            Panel("\n".join(lines), title="Location Lookup", border_style="blue")
        )

    def _run_location_search(self, query):
        """Authenticate and query LinkedIn's typeahead for locations.

        Returns a list of ``{"name", "geoUrn"}`` dicts, an empty list when
        nothing matched, or ``None`` when authentication failed.
        """
        if not self.db_manager or not self.settings:
            return None

        self.console.print(
            "[cyan]Opening LinkedIn to search locations (login may be required)...[/cyan]"
        )

        def progress_update(message: str) -> None:
            self.console.print(f"[cyan]{message}[/cyan]")

        async def run_lookup():
            async with LinkedInAutomation(self.db_manager, self.settings) as automation:
                login_ok = await automation.login(progress_update)
                if not login_ok:
                    return None
                return await automation.search_location(query)

        try:
            return asyncio.run(run_lookup())
        except Exception as e:
            # Hard-stop with evidence: distinct message per typed exception.
            # Returns [] so the caller renders "no results" without hanging.
            self._report_automation_failure(e, "location search")
            return []

    def _search_location_online(self):
        """Prompt for a query, search LinkedIn, and let the user pick a result.

        Returns a ``(display_name, geo_urn)`` tuple, or ``(None, None)`` if the
        search failed or was cancelled.
        """
        if not self.db_manager or not self.settings:
            self.console.print(
                "[yellow]Online search requires database access and app settings.[/yellow]"
            )
            return None, None

        query = inquirer.text(
            message="Search location (e.g. 'Madrid', 'Greater Tokyo'):",
            validate=lambda x: len(x.strip()) > 0 or "Enter a search term",
        ).execute()

        results = self._run_location_search(query.strip())
        if results is None:
            self.console.print("[red]❌ Could not authenticate with LinkedIn.[/red]")
            return None, None
        if not results:
            self.console.print(f"[yellow]No locations found for '{query}'.[/yellow]")
            return None, None

        choices = [
            Choice(value=item, name=f"{item.get('name', '?')} (geoUrn {item.get('geoUrn', '?')})")
            for item in results
        ]
        choices.append(Choice(value=None, name="🔙 Cancel"))
        selected = inquirer.select(
            message="Select a location:",
            choices=choices,
        ).execute()

        if not selected:
            return None, None
        return selected.get("name"), selected.get("geoUrn")

    def connection_checker(self):
        """Check pending connections using smart checker"""
        if not self.db_manager or not self.settings:
            self.console.print(
                "[red]Connection checker requires database access and app settings. Please check your installation.[/red]"
            )
            inquirer.confirm(message="Press Enter to continue...").execute()
            return

        # Get campaigns with pending connections
        try:
            campaigns = self.db_manager.get_campaigns(active_only=False)
            campaigns_with_pending = []

            for campaign in campaigns:
                # "possibly_sent" (issue #31) is an assumed-sent invite awaiting
                # acceptance, so it's pending alongside "sent".
                pending_count = len(
                    self.db_manager.get_contacts_by_status(campaign.id, "sent")
                    + self.db_manager.get_contacts_by_status(campaign.id, "possibly_sent")
                )
                if pending_count > 0:
                    campaigns_with_pending.append((campaign, pending_count))

            if not campaigns_with_pending:
                self.console.print("[yellow]No campaigns with pending connections found.[/yellow]")
                inquirer.confirm(message="Press Enter to continue...").execute()
                return

            # Select campaign to check
            campaign_choices = []
            for campaign, pending_count in campaigns_with_pending:
                campaign_choices.append(
                    Choice(
                        value=campaign,
                        name=f"{campaign.name} ({pending_count} pending connections)"
                    )
                )

            campaign_choices.append(Separator())
            campaign_choices.append(Choice(value="all", name="🔍 Check all campaigns"))
            campaign_choices.append(Choice(value="back", name="🔙 Back to main menu"))

            selected = inquirer.select(
                message="Select campaign to check:", choices=campaign_choices
            ).execute()

            if selected == "back":
                return

            # Select checker type
            checker_type = inquirer.select(
                message="Choose checker method:",
                choices=[
                    Choice(value="smart", name="🧠 Smart Checker - Monitor LinkedIn connections page"),
                    Choice(value="direct", name="🎯 Direct Checker - Visit each profile individually"),
                    Choice(value="back", name="🔙 Back"),
                ]
            ).execute()

            if checker_type == "back":
                return

            # Run the checker
            def progress_update(message: str) -> None:
                self.console.print(f"[cyan]{message}[/cyan]")

            async def run_checker():
                async with LinkedInAutomation(self.db_manager, self.settings) as automation:
                    progress_update("Launching browser and logging in...")
                    login_ok = await automation.login(progress_update)
                    if not login_ok:
                        return {"status": "login_failed"}

                    if selected == "all":
                        total_stats = {"total_checked": 0, "total_newly_accepted": 0}
                        for campaign, _ in campaigns_with_pending:
                            progress_update(f"Checking campaign: {campaign.name}")

                            if checker_type == "smart":
                                stats = await automation.smart_connection_checker(
                                    campaign.id, progress_update
                                )
                            else:  # direct
                                pending_contacts = (
                                    self.db_manager.get_contacts_by_status(campaign.id, "sent")
                                    + self.db_manager.get_contacts_by_status(campaign.id, "possibly_sent")
                                )
                                stats = await automation.check_connection_status(
                                    pending_contacts, progress_update
                                )
                                stats = {"checked": len(pending_contacts), "newly_accepted": stats}

                            total_stats["total_checked"] += stats.get("checked", 0)
                            total_stats["total_newly_accepted"] += stats.get("newly_accepted", 0)

                        return {"status": "success", **total_stats}
                    else:
                        if checker_type == "smart":
                            stats = await automation.smart_connection_checker(
                                selected.id, progress_update
                            )
                        else:  # direct
                            pending_contacts = (
                                self.db_manager.get_contacts_by_status(selected.id, "sent")
                                + self.db_manager.get_contacts_by_status(selected.id, "possibly_sent")
                            )
                            newly_accepted = await automation.check_connection_status(
                                pending_contacts, progress_update
                            )
                            stats = {"checked": len(pending_contacts), "newly_accepted": newly_accepted}

                        return {"status": "success", **stats}

            try:
                result = asyncio.run(run_checker())
                if result["status"] == "success":
                    checked = result.get("total_checked") or result.get("checked", 0)
                    accepted = result.get("total_newly_accepted") or result.get("newly_accepted", 0)

                    self.console.print(
                        Panel(
                            f"[bold]🔍 Connection Check Complete[/bold]\n\n"
                            f"[cyan]Contacts Checked:[/cyan] {checked}\n"
                            f"[cyan]Newly Accepted:[/cyan] {accepted}\n"
                            f"[cyan]Acceptance Rate:[/cyan] {acceptance_rate(checked, accepted):.1f}%",
                            title="Checker Results",
                            border_style="green",
                        )
                    )
                else:
                    self.console.print("[red]Connection checker failed. Please try again.[/red]")

            except Exception as e:
                # Hard-stop with evidence: distinct message per typed exception,
                # generic fallback otherwise. No interactive wait, no traceback.
                self._report_automation_failure(e, "connection check")
                return

        except Exception as e:
            self.console.print(f"[red]Error loading campaigns: {e}[/red]")

        inquirer.confirm(message="Press Enter to continue...").execute()

    def extract_profile_data(self):
        """Extract detailed profile data from LinkedIn profiles"""
        if not self.db_manager or not self.settings:
            self.console.print(
                "[red]Profile extraction requires database access and app settings. Please check your installation.[/red]"
            )
            inquirer.confirm(message="Press Enter to continue...").execute()
            return

        # Select extraction mode
        extraction_mode = inquirer.select(
            message="Choose profile extraction mode:",
            choices=[
                Choice(value="campaign", name="📋 Extract from campaign contacts"),
                Choice(value="manual", name="🎯 Extract specific profile URL"),
                Choice(value="back", name="🔙 Back to main menu"),
            ]
        ).execute()

        if extraction_mode == "back":
            return

        if extraction_mode == "campaign":
            # Select campaign
            try:
                campaigns = self.db_manager.get_campaigns(active_only=False)
                if not campaigns:
                    self.console.print("[yellow]No campaigns found.[/yellow]")
                    inquirer.confirm(message="Press Enter to continue...").execute()
                    return

                campaign_choices = []
                for campaign in campaigns:
                    contact_count = len(self.db_manager.get_contacts(campaign.id))
                    campaign_choices.append(
                        Choice(
                            value=campaign,
                            name=f"{campaign.name} ({contact_count} contacts)"
                        )
                    )

                campaign_choices.append(Choice(value="back", name="🔙 Back"))

                selected_campaign = inquirer.select(
                    message="Select campaign:", choices=campaign_choices
                ).execute()

                if selected_campaign == "back":
                    return

                contacts = self.db_manager.get_contacts(selected_campaign.id)
                if not contacts:
                    self.console.print("[yellow]No contacts found in this campaign.[/yellow]")
                    inquirer.confirm(message="Press Enter to continue...").execute()
                    return

                profile_urls = [contact.profile_url for contact in contacts if contact.profile_url]

            except Exception as e:
                self.console.print(f"[red]Error loading campaign data: {e}[/red]")
                inquirer.confirm(message="Press Enter to continue...").execute()
                return

        else:  # manual
            profile_url = inquirer.text(
                message="Enter LinkedIn profile URL:",
                validate=lambda x: "linkedin.com/in/" in x or "Please enter a valid LinkedIn profile URL"
            ).execute()

            if not profile_url:
                return

            profile_urls = [profile_url]

        # Confirm extraction
        confirm = inquirer.confirm(
            message=f"Extract detailed data from {len(profile_urls)} profile(s)?",
            default=True
        ).execute()

        if not confirm:
            return

        # Run extraction
        def progress_update(message: str) -> None:
            self.console.print(f"[cyan]{message}[/cyan]")

        async def run_extraction():
            async with LinkedInAutomation(self.db_manager, self.settings) as automation:
                progress_update("Launching browser and logging in...")
                login_ok = await automation.login(progress_update)
                if not login_ok:
                    return {"status": "login_failed"}

                extracted_profiles = []
                failed_count = 0

                for i, url in enumerate(profile_urls):
                    try:
                        progress_update(f"Extracting profile {i+1}/{len(profile_urls)}")
                        profile_data = await automation.extract_detailed_profile(url, progress_update)

                        if profile_data:
                            extracted_profiles.append(profile_data)
                        else:
                            failed_count += 1

                    except Exception as e:
                        progress_update(f"Failed to extract {url}: {str(e)}")
                        failed_count += 1

                return {
                    "status": "success",
                    "extracted": len(extracted_profiles),
                    "failed": failed_count,
                    "profiles": extracted_profiles
                }

        try:
            result = asyncio.run(run_extraction())
            if result["status"] == "success":
                self.console.print(
                    Panel(
                        f"[bold]📊 Profile Extraction Complete[/bold]\n\n"
                        f"[cyan]Profiles Extracted:[/cyan] {result['extracted']}\n"
                        f"[cyan]Failed Extractions:[/cyan] {result['failed']}\n"
                        f"[cyan]Success Rate:[/cyan] {(result['extracted']/(result['extracted']+result['failed'])*100) if (result['extracted']+result['failed']) > 0 else 0:.1f}%",
                        title="Extraction Results",
                        border_style="green",
                    )
                )

                # Optionally show sample extracted data
                if result['profiles']:
                    show_sample = inquirer.confirm(
                        message="Show sample extracted data?", default=False
                    ).execute()

                    if show_sample:
                        sample = result['profiles'][0]
                        self.console.print(
                            Panel(
                                f"[bold]Sample Profile Data[/bold]\n\n"
                                f"[cyan]Profession:[/cyan] {sample.get('profession', 'N/A')}\n"
                                f"[cyan]Location:[/cyan] {sample.get('location', 'N/A')}\n"
                                f"[cyan]Experience Items:[/cyan] {len(sample.get('experience', []))}\n"
                                f"[cyan]Education Items:[/cyan] {len(sample.get('education', []))}\n"
                                f"[cyan]Email:[/cyan] {sample.get('contact_info', {}).get('email', 'N/A')}\n"
                                f"[cyan]Open to Work:[/cyan] {sample.get('open_to_work', 'N/A')}",
                                title=f"Profile: {sample.get('profile_url', '')[:50]}...",
                                border_style="blue",
                            )
                        )
            else:
                self.console.print("[red]Profile extraction failed. Please try again.[/red]")

        except Exception as e:
            # Hard-stop with evidence: distinct message per typed exception,
            # generic fallback otherwise. No interactive wait, no traceback.
            self._report_automation_failure(e, "profile extraction")
            return

        inquirer.confirm(message="Press Enter to continue...").execute()


def _positive_int(value):
    """argparse type for ``--max``: an integer >= 1."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _build_parser():
    """Build the top-level argument parser.

    No subcommand keeps the classic interactive menu; the ``run`` subcommand
    adds a non-interactive path suitable for cron / systemd-timer scheduling.
    """
    parser = argparse.ArgumentParser(
        prog="linkedin-cli",
        description=(
            "LinkedIn Networking CLI. With no subcommand, launches the "
            "interactive menu. Use `run` to execute a campaign non-interactively "
            "(for scheduled, headless batches)."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser(
        "run",
        help="Execute a campaign non-interactively (for cron/systemd-timer).",
        description=(
            "Run a campaign without prompts, sending a small batch of "
            "invitations. All rate-limit, daily-cap and session logic is "
            "respected. Exits 0 on success, non-zero on failure."
        ),
    )
    run_parser.add_argument(
        "--campaign",
        required=True,
        metavar="ID-OR-NAME",
        help="Campaign to run, given as its numeric id or its name.",
    )
    run_parser.add_argument(
        "--max",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "Cap on invitations sent this run "
            "(default: the campaign's daily_limit)."
        ),
    )
    return parser


def main(argv=None):
    """Main entry point.

    Returns a process exit code (``None``/``0`` on the interactive path). With
    the ``run`` subcommand, dispatches to the non-interactive executor and
    propagates its exit code.
    """
    args = _build_parser().parse_args(argv)

    if getattr(args, "command", None) == "run":
        return LinkedInCLI().run_noninteractive(args.campaign, args.max)

    # No subcommand: the classic interactive experience, unchanged.
    console = Console()
    try:
        logger.info("LinkedIn CLI application starting")
        cli = LinkedInCLI()
        cli.display_welcome()
        cli.main_menu()
    except KeyboardInterrupt:
        logger.info("Application terminated by user (KeyboardInterrupt)")
        console.print("\n[yellow]Goodbye! 👋[/yellow]")
    except Exception as e:
        logger.error(f"Unhandled exception in main: {e}", exc_info=True)
        console.print(f"\n[red]Error: {e}[/red]")


if __name__ == "__main__":
    sys.exit(main())
