#!/usr/bin/env python3
"""One-off CLI to create the admin account in Supabase Auth via its Admin
API (requires the service-role key). There is no in-app signup route --
this is the only way an admin account gets created.

Usage:
  python scripts/create_admin_user.py --email admin@example.com --password 'change-me'
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    if not settings.supabase_url or not settings.supabase_service_role_key:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env first.")
        raise SystemExit(1)

    response = httpx.post(
        f"{settings.supabase_url}/auth/v1/admin/users",
        json={"email": args.email, "password": args.password, "email_confirm": True},
        headers={
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "Content-Type": "application/json",
        },
        timeout=10.0,
    )
    if response.status_code >= 300:
        print(f"Failed ({response.status_code}): {response.text}")
        raise SystemExit(1)

    print(f"Admin user created: {args.email}")


if __name__ == "__main__":
    main()
