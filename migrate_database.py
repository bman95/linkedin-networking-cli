#!/usr/bin/env python3
"""
Database Migration Script
=========================

This script migrates existing campaigns from the old schema to the new schema.

OLD SCHEMA:
- location: str (e.g., "San Francisco, CA")
- industry: str (e.g., "Technology")
- network: hardcoded in code

NEW SCHEMA:
- geo_urn: str (e.g., "90000084")
- location_display: str (e.g., "San Francisco Bay Area")
- industry_ids: str (e.g., "4")
- industry_display: str (e.g., "Computer Software")
- network: str (e.g., '["F","S"]')
- network_display: str (e.g., "1st + 2nd degree connections")

Usage:
    python migrate_database.py [--dry-run]

Options:
    --dry-run    Show what would be migrated without making changes
"""

import sys
from pathlib import Path
import argparse

# Add src directory to path
sys.path.append(str(Path(__file__).parent / "src"))

from database.operations import DatabaseManager
from database.models import Campaign
from sqlmodel import select

# Simple mappings for common legacy values
LEGACY_LOCATION_MAPPING = {
    "San Francisco, CA": ("90000084", "San Francisco Bay Area"),
    "New York, NY": ("102571732", "New York City Metropolitan Area"),
    "Los Angeles, CA": ("102448103", "Greater Los Angeles Area"),
    "Chicago, IL": ("103112676", "Greater Chicago Area"),
    "Austin, TX": ("102748797", "Austin, Texas Area"),
    "Seattle, WA": ("103658393", "Greater Seattle Area"),
    "Boston, MA": ("105646813", "Greater Boston Area"),
}

LEGACY_INDUSTRY_MAPPING = {
    "Technology": ("4", "Computer Software"),
    "Finance": ("43", "Financial Services"),
    "Healthcare": ("14", "Hospital & Health Care"),
    "Education": ("69", "Higher Education"),
    "Marketing": ("80", "Marketing & Advertising"),
    "Sales": ("137", "Sales"),
    "Consulting": ("11", "Management Consulting"),
}


def migrate_campaigns(db_manager: DatabaseManager, dry_run: bool = False):
    """Migrate existing campaigns to new schema"""

    print("=" * 60)
    print("LinkedIn Networking CLI - Database Migration")
    print("=" * 60)
    print()

    if dry_run:
        print("🔍 DRY RUN MODE - No changes will be made")
        print()

    try:
        # Get all campaigns
        with db_manager.get_session() as session:
            statement = select(Campaign)
            campaigns = session.exec(statement).all()

            if not campaigns:
                print("✅ No campaigns found. Nothing to migrate.")
                return

            print(f"📊 Found {len(campaigns)} campaign(s) to analyze\n")

            migrated_count = 0
            skipped_count = 0

            for campaign in campaigns:
                needs_migration = False
                migration_details = []

                print(f"📋 Campaign: {campaign.name} (ID: {campaign.id})")

                # Check if already migrated (has new fields)
                if hasattr(campaign, 'geo_urn') and campaign.geo_urn:
                    print("   ✅ Already has geo_urn - skipping")
                    skipped_count += 1
                    print()
                    continue

                # Migrate location
                if campaign.location:
                    if campaign.location in LEGACY_LOCATION_MAPPING:
                        geo_urn, location_display = LEGACY_LOCATION_MAPPING[campaign.location]
                        migration_details.append(
                            f"   📍 Location: '{campaign.location}' → '{location_display}' (geoUrn: {geo_urn})"
                        )
                        if not dry_run:
                            campaign.geo_urn = geo_urn
                            campaign.location_display = location_display
                        needs_migration = True
                    else:
                        print(f"   ⚠️  Unknown location: '{campaign.location}' - manual mapping needed")
                        migration_details.append(
                            f"   📍 Location: '{campaign.location}' → (needs manual mapping)"
                        )
                else:
                    migration_details.append("   📍 Location: None")

                # Migrate industry
                if campaign.industry:
                    if campaign.industry in LEGACY_INDUSTRY_MAPPING:
                        industry_id, industry_display = LEGACY_INDUSTRY_MAPPING[campaign.industry]
                        migration_details.append(
                            f"   🏢 Industry: '{campaign.industry}' → '{industry_display}' (ID: {industry_id})"
                        )
                        if not dry_run:
                            campaign.industry_ids = industry_id
                            campaign.industry_display = industry_display
                        needs_migration = True
                    else:
                        print(f"   ⚠️  Unknown industry: '{campaign.industry}' - manual mapping needed")
                        migration_details.append(
                            f"   🏢 Industry: '{campaign.industry}' → (needs manual mapping)"
                        )
                else:
                    migration_details.append("   🏢 Industry: None")

                # Set default network if not set
                if not hasattr(campaign, 'network') or not campaign.network:
                    migration_details.append(
                        "   🔗 Network: (not set) → '1st + 2nd degree connections'"
                    )
                    if not dry_run:
                        campaign.network = '["F","S"]'
                        campaign.network_display = "1st + 2nd degree connections"
                    needs_migration = True

                # Print migration details
                if migration_details:
                    print("\n".join(migration_details))

                if needs_migration:
                    if not dry_run:
                        session.add(campaign)
                        migrated_count += 1
                        print("   ✅ Migrated")
                    else:
                        print("   📝 Would be migrated")
                        migrated_count += 1
                else:
                    print("   ℹ️  No migration needed")
                    skipped_count += 1

                print()

            # Commit changes if not dry run
            if not dry_run and migrated_count > 0:
                session.commit()
                print("💾 Changes committed to database")

            # Summary
            print("=" * 60)
            print("Migration Summary")
            print("=" * 60)
            print(f"Total campaigns: {len(campaigns)}")
            print(f"Migrated: {migrated_count}")
            print(f"Skipped: {skipped_count}")

            if dry_run and migrated_count > 0:
                print()
                print("ℹ️  This was a dry run. Run without --dry-run to apply changes.")

    except Exception as e:
        print(f"❌ Error during migration: {e}")
        raise


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Migrate LinkedIn Networking CLI database to new schema"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without making changes"
    )

    args = parser.parse_args()

    try:
        # Initialize database manager
        db_manager = DatabaseManager()

        # Run migration
        migrate_campaigns(db_manager, dry_run=args.dry_run)

        print()
        print("✅ Migration completed successfully!")

    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
