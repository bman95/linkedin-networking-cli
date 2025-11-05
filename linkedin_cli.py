#!/usr/bin/env python3
"""Simple InquirerPy CLI demonstration"""

from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from InquirerPy.separator import Separator
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from collections import namedtuple
import asyncio
import sys
from pathlib import Path

# Add src directory to path for imports
sys.path.append(str(Path(__file__).parent / "src"))

from database.operations import DatabaseManager
from database.models import Campaign
from config.settings import AppSettings
from automation.linkedin import LinkedInAutomation


class LinkedInCLI:
    """LinkedIn Networking CLI with InquirerPy interface"""

    def __init__(self):
        self.console = Console()
        # Initialize real components
        try:
            self.db_manager = DatabaseManager()
            self.settings = AppSettings()
        except Exception as e:
            self.console.print(f"[red]Error initializing components: {e}[/red]")
            self.console.print("[yellow]Running in demo mode with mock data[/yellow]")
            self.db_manager = None
            self.settings = None

    @staticmethod
    def _campaign_get_field(campaign, attr, default=None):
        """Read campaign attribute regardless of backing type"""
        if isinstance(campaign, dict):
            return campaign.get(attr, default)
        return getattr(campaign, attr, default)

    def display_welcome(self):
        """Display welcome banner"""
        self.console.print(
            Panel.fit(
                "[bold cyan]LinkedIn Networking CLI[/bold cyan]\n"
                "[dim]Professional networking automation with InquirerPy interface[/dim]",
                border_style="cyan",
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
                    Choice(
                        value="file_editor",
                        name="📝 File Editor Demo - Edit files with syntax highlighting",
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
            elif choice == "file_editor":
                self.file_editor_demo()
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

        self.console.print(
            Panel(
                f"[bold]📊 Dashboard Statistics[/bold]\n\n"
                f"[cyan]Active Campaigns:[/cyan] {stats['active_campaigns']}/{stats['total_campaigns']}\n"
                f"[cyan]Total Connections:[/cyan] {stats['total_sent']} sent, {stats['total_accepted']} accepted\n"
                f"[cyan]Success Rate:[/cyan] {stats['acceptance_rate']}%\n\n"
                f"[dim]💡 This is a demo with mock data[/dim]",
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
            message="Target keywords (comma-separated, optional):",
        ).execute()

        location = inquirer.select(
            message="Target location:",
            choices=[
                "Any",
                "San Francisco, CA",
                "New York, NY",
                "Los Angeles, CA",
                "Chicago, IL",
                "Austin, TX",
                "Seattle, WA",
                "Boston, MA",
                "Other",
            ],
            default="Any",
        ).execute()

        industry = inquirer.select(
            message="Target industry:",
            choices=[
                "Any",
                "Technology",
                "Finance",
                "Healthcare",
                "Education",
                "Marketing",
                "Sales",
                "Consulting",
                "Other",
            ],
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

        # Show campaign summary
        self.console.print(
            Panel(
                f"[bold]📋 Campaign Summary[/bold]\n\n"
                f"[cyan]Name:[/cyan] {name}\n"
                f"[cyan]Description:[/cyan] {description or 'None'}\n"
                f"[cyan]Keywords:[/cyan] {keywords or 'Any'}\n"
                f"[cyan]Location:[/cyan] {location}\n"
                f"[cyan]Industry:[/cyan] {industry}\n"
                f"[cyan]Daily Limit:[/cyan] {daily_limit}\n"
                f"[cyan]Message:[/cyan] {message_template}",
                title="Campaign Created",
                border_style="green",
            )
        )

        # Create campaign data
        campaign_data = {
            "name": name,
            "description": description or None,
            "keywords": keywords or None,
            "location": location if location != "Any" else None,
            "industry": industry if industry != "Any" else None,
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
        """Manage existing campaigns"""
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

        # Select campaign to manage
        campaign_choices = []
        for campaign in campaigns:
            status = "🟢 Active" if campaign.active else "🔴 Inactive"
            acceptance_rate = (
                (campaign.total_accepted / campaign.total_sent * 100)
                if campaign.total_sent > 0
                else 0
            )
            campaign_choices.append(
                Choice(
                    value=campaign,
                    name=f"{campaign.name} - {status} ({campaign.total_sent} sent, {acceptance_rate:.1f}% rate)",
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
                Choice(value="delete", name="🗑️ Delete campaign"),
                Separator(),
                Choice(value="back", name="🔙 Back to campaign list"),
            ],
        ).execute()

        if action == "view":
            self.view_campaign_details(selected)
        elif action == "toggle":
            new_status = "activated" if not selected.active else "deactivated"
            self.console.print(
                f"[green]✅ Campaign '{selected.name}' {new_status}![/green]"
            )
            self.console.print("[blue]💡 In real app: Would update database[/blue]")
        elif action == "edit":
            self.console.print(f"[blue]📝 Editing '{selected.name}'...[/blue]")
            self.console.print("[blue]💡 In real app: Would show edit form[/blue]")
        elif action == "delete":
            confirm = inquirer.confirm(
                message=f"⚠️  Are you sure you want to delete '{selected.name}'?",
                default=False,
            ).execute()
            if confirm:
                self.console.print(f"[red]🗑️ Campaign '{selected.name}' deleted.[/red]")
                self.console.print(
                    "[blue]💡 In real app: Would delete from database[/blue]"
                )

        if action != "back":
            inquirer.confirm(message="Press Enter to continue...").execute()

    def view_campaign_details(self, campaign):
        """View detailed campaign information"""
        acceptance_rate = (
            (campaign.total_accepted / campaign.total_sent * 100)
            if campaign.total_sent > 0
            else 0
        )

        self.console.print(
            Panel(
                f"[bold]📊 Campaign Details[/bold]\n\n"
                f"[cyan]Name:[/cyan] {campaign.name}\n"
                f"[cyan]Status:[/cyan] {'🟢 Active' if campaign.active else '🔴 Inactive'}\n"
                f"[cyan]Daily Limit:[/cyan] {campaign.daily_limit}\n\n"
                f"[cyan]Connections Sent:[/cyan] {campaign.total_sent}\n"
                f"[cyan]Connections Accepted:[/cyan] {campaign.total_accepted}\n"
                f"[cyan]Acceptance Rate:[/cyan] {acceptance_rate:.1f}%\n"
                f"[cyan]Connections Pending:[/cyan] {campaign.total_sent - campaign.total_accepted}",
                title=f"Campaign: {campaign.name}",
                border_style="blue",
            )
        )

        inquirer.confirm(message="Press Enter to continue...").execute()

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

            if isinstance(selected, dict):
                self.console.print(
                    "[yellow]Automation is only available when connected to the real database. Switch out of demo mode to run campaigns.[/yellow]"
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

            async def run_automation():
                async with LinkedInAutomation(self.db_manager, self.settings) as automation:
                    progress_update("Launching browser and attaching to Chrome...")
                    login_ok = await automation.login(progress_update)
                    if not login_ok:
                        return {"status": "login_failed"}

                    automation_settings = self.settings.get_automation_settings()
                    search_limit = automation_settings.get("search_limit", 100)
                    progress_update(
                        f"Searching for up to {search_limit} targeted profiles..."
                    )
                    profiles = await automation.search_profiles(
                        selected, limit=search_limit, progress_callback=progress_update
                    )

                    if not profiles:
                        return {"status": "no_profiles", "profiles": 0}

                    progress_update("Sending connection requests...")
                    results = await automation.send_connection_requests(
                        selected, profiles, progress_callback=progress_update
                    )
                    results.update(
                        {
                            "status": "success",
                            "profiles": len(profiles),
                        }
                    )
                    return results

            try:
                automation_result = asyncio.run(run_automation())
            except RuntimeError as runtime_error:
                if "asyncio.run()" in str(runtime_error):
                    loop = asyncio.new_event_loop()
                    try:
                        asyncio.set_event_loop(loop)
                        automation_result = loop.run_until_complete(run_automation())
                    finally:
                        loop.close()
                else:
                    self.console.print(f"[red]Automation failed: {runtime_error}[/red]")
                    inquirer.confirm(message="Press Enter to continue...").execute()
                    return
            except Exception as automation_error:
                self.console.print(f"[red]Automation failed: {automation_error}[/red]")
                inquirer.confirm(message="Press Enter to continue...").execute()
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
            elif status == "success":
                sent = automation_result.get("sent", 0)
                failed = automation_result.get("failed", 0)
                existing = automation_result.get("existing", 0)
                profiles_found = automation_result.get("profiles", 0)
                total = automation_result.get("total_processed", sent + failed + existing)
                summary_lines = [
                    "[bold]Automation summary[/bold]",
                    "",
                    f"Profiles scanned: {profiles_found}",
                    f"Requests sent: {sent}",
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

    def show_settings(self):
        """Show application settings"""
        setting = inquirer.select(
            message="Settings & Configuration:",
            choices=[
                Choice(value="credentials", name="🔐 LinkedIn credentials status"),
                Choice(value="browser", name="🌐 Browser automation settings"),
                Choice(value="limits", name="⚡ Rate limiting settings"),
                Choice(value="data", name="📁 Data directory information"),
                Separator(),
                Choice(value="back", name="🔙 Back to main menu"),
            ],
        ).execute()

        if setting == "credentials":
            self.console.print(
                Panel(
                    "[bold]🔐 LinkedIn Credentials[/bold]\n\n"
                    "[cyan]Status:[/cyan] 🔴 Not configured (demo mode)\n"
                    "[cyan]Email:[/cyan] Not set\n"
                    "[cyan]Password:[/cyan] Not set\n\n"
                    "[dim]Set via environment variables:\n"
                    'export LINKEDIN_EMAIL="your-email"\n'
                    'export LINKEDIN_PASSWORD="your-password"[/dim]',
                    title="Credentials Status",
                    border_style="blue",
                )
            )
        elif setting == "browser":
            self.console.print(
                Panel(
                    "[bold]🌐 Browser Settings[/bold]\n\n"
                    "[cyan]Browser:[/cyan] Chromium (Playwright)\n"
                    "[cyan]Headless Mode:[/cyan] True\n"
                    "[cyan]Viewport:[/cyan] 1920x1080\n"
                    "[cyan]User Data Dir:[/cyan] ~/.linkedin-networking-cli/browser_data/",
                    title="Browser Configuration",
                    border_style="blue",
                )
            )
        elif setting == "limits":
            self.console.print(
                Panel(
                    "[bold]⚡ Rate Limiting[/bold]\n\n"
                    "[cyan]Connection Delay:[/cyan] 2-5 seconds\n"
                    "[cyan]Daily Connection Limit:[/cyan] 20\n"
                    "[cyan]Search Limit:[/cyan] 100\n"
                    "[cyan]Retry Attempts:[/cyan] 3",
                    title="Rate Limiting Settings",
                    border_style="blue",
                )
            )
        elif setting == "data":
            self.console.print(
                Panel(
                    "[bold]📁 Data Storage[/bold]\n\n"
                    "[cyan]App Directory:[/cyan] ~/.linkedin-networking-cli/\n"
                    "[cyan]Database:[/cyan] linkedin_networking.db\n"
                    "[cyan]Session Data:[/cyan] session.json\n"
                    "[cyan]Browser Data:[/cyan] browser_data/",
                    title="Data Directory Information",
                    border_style="blue",
                )
            )

        if setting != "back":
            inquirer.confirm(message="Press Enter to continue...").execute()

    def file_editor_demo(self):
        """Demo file editor with syntax highlighting - recreating your original request"""
        sample_code = '''def is_palindrome(text: str) -> bool:
    """
    Check if the given text is a palindrome.

    Args:
        text: The input string to check

    Returns:
        bool: True if the string is a palindrome, False otherwise
    """
    # Clean the text: convert to lowercase and keep only alphanumeric characters
    cleaned_text = ''.join(char.lower() for char in text if char.isalnum())

    # Check if the cleaned text reads the same forward and backward
    return cleaned_text == cleaned_text[::-1]'''

        # Display file content with syntax highlighting
        syntax = Syntax(sample_code, "python", theme="monokai", line_numbers=True)

        self.console.print(
            Panel(syntax, title="📝 Edit file: palindrome.py", border_style="cyan")
        )

        # This recreates the interface from your original image
        action = inquirer.select(
            message="Do you want to make this edit to palindrome.py?",
            choices=[
                Choice(value="yes", name="1. Yes"),
                Choice(
                    value="yes_auto",
                    name="2. Yes, and don't ask again this session (shift+tab)",
                ),
                Choice(
                    value="no",
                    name="3. No, and tell Claude what to do differently (esc)",
                ),
            ],
        ).execute()

        if action in ["yes", "yes_auto"]:
            # Write the file
            with open("palindrome.py", "w", encoding="utf-8") as f:
                f.write(sample_code)

            mode_text = " (auto-confirm enabled)" if action == "yes_auto" else ""
            self.console.print(
                f"[green]✅ File saved: palindrome.py{mode_text}[/green]"
            )
        else:
            self.console.print("[yellow]Edit cancelled.[/yellow]")

        inquirer.confirm(message="Press Enter to continue...").execute()

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
                pending_count = len(self.db_manager.get_contacts_by_status(campaign.id, "sent"))
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
                                pending_contacts = self.db_manager.get_contacts_by_status(campaign.id, "sent")
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
                            pending_contacts = self.db_manager.get_contacts_by_status(selected.id, "sent")
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
                            f"[cyan]Acceptance Rate:[/cyan] {(accepted/checked*100) if checked > 0 else 0:.1f}%",
                            title="Checker Results",
                            border_style="green",
                        )
                    )
                else:
                    self.console.print("[red]Connection checker failed. Please try again.[/red]")

            except Exception as e:
                self.console.print(f"[red]Checker failed: {e}[/red]")

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
            self.console.print(f"[red]Extraction failed: {e}[/red]")

        inquirer.confirm(message="Press Enter to continue...").execute()


def main():
    """Main entry point"""
    try:
        cli = LinkedInCLI()
        cli.display_welcome()
        cli.main_menu()
    except KeyboardInterrupt:
        print("\n[yellow]Goodbye! 👋[/yellow]")
    except Exception as e:
        print(f"\n[red]Error: {e}[/red]")


if __name__ == "__main__":
    main()
