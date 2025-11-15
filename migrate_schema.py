#!/usr/bin/env python3
"""
Database Schema Migration - Add new columns to Campaign table
==============================================================

This script adds the new columns (geo_urn, location_display, industry_ids, etc.)
to the existing Campaign table without losing data.

Usage:
    python migrate_schema.py [--dry-run]

Options:
    --dry-run    Show SQL statements without executing them
"""

import sys
import sqlite3
from pathlib import Path
import argparse

# Database paths (check multiple locations)
DB_PATH_HOME = Path.home() / ".linkedin-networking-cli" / "linkedin_networking.db"
DB_PATH_LOCAL = Path.cwd() / "linkedin_networking.db"

def get_database_path():
    """Find the actual database path"""
    if DB_PATH_LOCAL.exists():
        return DB_PATH_LOCAL
    elif DB_PATH_HOME.exists():
        return DB_PATH_HOME
    else:
        # Default to home directory path (will be created there)
        return DB_PATH_HOME

DB_PATH = get_database_path()

# SQL statements to add new columns
MIGRATION_STATEMENTS = [
    # Location fields (new format)
    "ALTER TABLE campaign ADD COLUMN geo_urn TEXT;",
    "ALTER TABLE campaign ADD COLUMN location_display TEXT;",

    # Industry fields (new format)
    "ALTER TABLE campaign ADD COLUMN industry_ids TEXT;",
    "ALTER TABLE campaign ADD COLUMN industry_display TEXT;",

    # Network filter (connection degree)
    "ALTER TABLE campaign ADD COLUMN network TEXT DEFAULT '[\"F\",\"S\"]';",
    "ALTER TABLE campaign ADD COLUMN network_display TEXT DEFAULT '1st + 2nd degree connections';",
]

def check_database_exists():
    """Check if database file exists"""
    if not DB_PATH.exists():
        print(f"❌ Database not found at: {DB_PATH}")
        print("   The database will be created automatically when you create your first campaign.")
        return False
    return True

def check_column_exists(cursor, table_name, column_name):
    """Check if a column exists in a table"""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    return column_name in columns

def migrate_schema(dry_run=False):
    """Migrate database schema by adding new columns"""

    print("=" * 60)
    print("LinkedIn Networking CLI - Schema Migration")
    print("=" * 60)
    print()
    print(f"Database: {DB_PATH}")
    print()

    if not check_database_exists():
        return

    if dry_run:
        print("🔍 DRY RUN MODE - No changes will be made")
        print()

    try:
        # Connect to database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Check which columns already exist
        print("📊 Checking current schema...")
        print()

        new_columns = [
            "geo_urn",
            "location_display",
            "industry_ids",
            "industry_display",
            "network",
            "network_display"
        ]

        columns_to_add = []
        columns_existing = []

        for col in new_columns:
            if check_column_exists(cursor, "campaign", col):
                columns_existing.append(col)
            else:
                columns_to_add.append(col)

        if columns_existing:
            print(f"✅ Already migrated columns: {', '.join(columns_existing)}")

        if not columns_to_add:
            print()
            print("✅ All columns already exist. No migration needed!")
            conn.close()
            return

        print(f"📝 Columns to add: {', '.join(columns_to_add)}")
        print()

        # Execute migration statements
        print("🔧 Executing migration...")
        print()

        statements_to_run = []
        for statement in MIGRATION_STATEMENTS:
            # Extract column name from statement
            col_name = statement.split("ADD COLUMN ")[1].split(" ")[0]
            if col_name in columns_to_add:
                statements_to_run.append(statement)

        if dry_run:
            print("SQL statements that would be executed:")
            print()
            for stmt in statements_to_run:
                print(f"  {stmt}")
            print()
        else:
            for stmt in statements_to_run:
                print(f"  Executing: {stmt}")
                cursor.execute(stmt)

            # Commit changes
            conn.commit()
            print()
            print("💾 Changes committed to database")

        # Verify migration
        print()
        print("🔍 Verifying migration...")
        cursor.execute("PRAGMA table_info(campaign)")
        columns = cursor.fetchall()

        print()
        print("Current Campaign table schema:")
        for col in columns:
            col_id, name, type_, notnull, default, pk = col
            nullable = "NOT NULL" if notnull else "NULL"
            default_str = f" DEFAULT {default}" if default else ""
            pk_str = " PRIMARY KEY" if pk else ""
            print(f"  {name}: {type_} {nullable}{default_str}{pk_str}")

        conn.close()

        print()
        print("=" * 60)
        if dry_run:
            print("✅ Dry run completed!")
            print("   Run without --dry-run to apply changes.")
        else:
            print("✅ Migration completed successfully!")
            print("   You can now create campaigns with the new filters.")
        print("=" * 60)

    except sqlite3.Error as e:
        print(f"❌ Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)

def reset_database():
    """Delete the database file to start fresh"""
    if DB_PATH.exists():
        print(f"Deleting database at: {DB_PATH}")
        DB_PATH.unlink()
        print("✅ Database deleted. A new one will be created on next run.")
    else:
        print("ℹ️  Database doesn't exist yet.")

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Migrate LinkedIn Networking CLI database schema"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete database and start fresh (WARNING: loses all data!)"
    )

    args = parser.parse_args()

    if args.reset:
        confirm = input("⚠️  This will DELETE all your campaigns! Are you sure? (yes/no): ")
        if confirm.lower() == "yes":
            reset_database()
        else:
            print("Cancelled.")
        return

    try:
        migrate_schema(dry_run=args.dry_run)
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
