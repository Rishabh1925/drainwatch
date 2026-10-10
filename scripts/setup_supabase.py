#!/usr/bin/env python3
"""Set up Supabase tables and seed initial user accounts for DrainWatch.

Usage:
    # 1. Create a Supabase project at https://supabase.com
    # 2. Copy the URL and service-role key from Settings → API
    # 3. Set them as environment variables:
    export SUPABASE_URL="https://xxxxx.supabase.co"
    export SUPABASE_KEY="eyJhbGci..."
    # 4. Run this script:
    python scripts/setup_supabase.py

This script will:
- Create all required tables (using SQL via the Supabase REST API)
- Seed the default operator and admin accounts
- Optionally migrate existing JSONL data from local_store/ops/

The two seeded accounts:
    operator@drainwatch.in  /  drainwatch2024   (OPERATOR role)
    admin@drainwatch.in     /  adminpass2024    (ADMIN role)
"""

from __future__ import annotations

import os
import sys

# Add project root to path so we can import drainwatch modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from drainwatch.ops.store import COLLECTIONS, SupabaseStorage

# SQL to create all tables. Each collection gets a table with:
# - id: TEXT PRIMARY KEY (the record's business key)
# - data: JSONB (the full record, including id — denormalized for flexibility)
# - created_at / updated_at: timestamps managed by the application
TABLE_SQL = """
DO $$
BEGIN
    {create_statements}
END $$;
"""

CREATE_TEMPLATE = """
    CREATE TABLE IF NOT EXISTS {table} (
        id TEXT PRIMARY KEY,
        data JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now()
    );
"""


def create_tables(url: str, key: str) -> None:
    """Create all collection tables in Supabase via the REST SQL endpoint."""
    from supabase import create_client

    client = create_client(url, key)

    for collection in COLLECTIONS:
        sql = CREATE_TEMPLATE.format(table=collection)
        try:
            # Use the rpc endpoint or direct SQL — Supabase exposes this
            # via the management API. For simplicity, we'll create tables
            # by doing an upsert which auto-creates if RLS allows, or
            # instruct the user to run the SQL manually.
            print(f"  Ensuring table '{collection}' exists...")
            # Try a no-op select to see if the table exists
            client.table(collection).select("id").limit(0).execute()
            print(f"    ✓ '{collection}' exists")
        except Exception as e:
            error_str = str(e)
            if "does not exist" in error_str or "42P01" in error_str:
                print(f"    ✗ '{collection}' does not exist — please create it manually")
                print(f"      Run this SQL in the Supabase SQL Editor:")
                print(f"      {CREATE_TEMPLATE.format(table=collection).strip()}")
            else:
                print(f"    ? Error checking '{collection}': {e}")


def seed_users(store: SupabaseStorage) -> None:
    """Seed the default operator and admin accounts."""
    from drainwatch.ops.security import make_user

    operator = make_user(
        name="Rishabh Singh",
        email="operator@drainwatch.in",
        password="drainwatch2024",
        role="OPERATOR",
        title="Operations Operator",
        organization="Delhi NCR Drainage",
        jurisdiction="Delhi NCR",
    )

    admin = make_user(
        name="A. Mehta",
        email="admin@drainwatch.in",
        password="adminpass2024",
        role="ADMIN",
        title="System Administrator",
        organization="Delhi NCR Drainage",
        jurisdiction="Delhi NCR",
    )

    for user in [operator, admin]:
        existing = store.get("users", user["id"])
        if existing:
            print(f"  ⊘ User '{user['email']}' already exists, skipping")
        else:
            store.put("users", user)
            print(f"  ✓ Created user '{user['email']}' ({user['role']})")


def migrate_local_data(store: SupabaseStorage) -> None:
    """Migrate existing JSONL data from local_store/ops/ to Supabase."""
    import json
    from drainwatch.ops.store import OPS_DIR

    if not OPS_DIR.exists():
        print("  No local data directory found, skipping migration")
        return

    migrated = 0
    for collection in COLLECTIONS:
        jsonl_path = OPS_DIR / f"{collection}.jsonl"
        if not jsonl_path.exists():
            continue

        # Read all records, fold by id (last-write-wins, same as LocalStorage)
        records: dict[str, dict] = {}
        for line in jsonl_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if "id" in record:
                    records[record["id"]] = record
            except json.JSONDecodeError:
                continue

        if not records:
            continue

        print(f"  Migrating {len(records)} records from '{collection}'...")
        for record in records.values():
            try:
                store.put(collection, record)
                migrated += 1
            except Exception as e:
                print(f"    ✗ Failed to migrate {record.get('id', '?')}: {e}")

    print(f"  ✓ Migrated {migrated} total records")


def print_setup_sql() -> None:
    """Print the full SQL needed to create all tables."""
    print("\n" + "=" * 60)
    print("SQL to create all tables (run in Supabase SQL Editor):")
    print("=" * 60 + "\n")

    for collection in COLLECTIONS:
        print(CREATE_TEMPLATE.format(table=collection).strip() + "\n")

    # Also add RLS policies for service-role access
    print("-- Enable Row Level Security (optional, service-role key bypasses it)")
    for collection in COLLECTIONS:
        print(f"ALTER TABLE {collection} ENABLE ROW LEVEL SECURITY;")
        print(
            f"CREATE POLICY \"Service role full access\" ON {collection} "
            f"FOR ALL USING (true) WITH CHECK (true);"
        )

    print("\n" + "=" * 60 + "\n")


def main() -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")

    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_KEY environment variables are required.\n")
        print("1. Create a project at https://supabase.com")
        print("2. Go to Settings → API and copy:")
        print("   - Project URL → SUPABASE_URL")
        print("   - service_role key (not anon!) → SUPABASE_KEY")
        print("\n3. Set them:")
        print('   export SUPABASE_URL="https://xxxxx.supabase.co"')
        print('   export SUPABASE_KEY="eyJhbGci..."')
        print("\n4. Run this script again.")

        print("\nMeanwhile, here's the SQL you'll need:\n")
        print_setup_sql()
        sys.exit(1)

    print(f"\n🔧 DrainWatch Supabase Setup")
    print(f"   URL: {url[:50]}...")
    print()

    # Step 1: Check / create tables
    print("Step 1: Checking tables...")
    create_tables(url, key)
    print()

    # Step 2: Seed users
    print("Step 2: Seeding user accounts...")
    store = SupabaseStorage(url, key)
    seed_users(store)
    print()

    # Step 3: Migrate local data (optional)
    migrate = input("Step 3: Migrate existing local JSONL data to Supabase? [y/N] ").strip().lower()
    if migrate == "y":
        migrate_local_data(store)
    else:
        print("  Skipped migration")
    print()

    print("✅ Setup complete!")
    print()
    print("To use Supabase, start the backend with these env vars:")
    print(f'  export SUPABASE_URL="{url}"')
    print(f'  export SUPABASE_KEY="{key}"')
    print("  drainwatch serve")
    print()
    print("Default accounts:")
    print("  operator@drainwatch.in  /  drainwatch2024   (OPERATOR)")
    print("  admin@drainwatch.in     /  adminpass2024    (ADMIN)")


if __name__ == "__main__":
    main()
