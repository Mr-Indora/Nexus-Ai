"""Repair missing Gmail thread IDs for one real user without running sync."""
import argparse
import sys

from sqlalchemy import select

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from auth.google import GoogleAuthError
from db import SessionLocal
from integrations.google_api import GoogleAPIError, GoogleClient
from models.models import User
from gmail_thread_repair import repair_missing_thread_ids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    args = parser.parse_args()

    db = SessionLocal()
    client = None
    try:
        user = db.scalar(select(User).where(User.id == args.user_id))
        if not user:
            raise SystemExit(f"User {args.user_id} not found")
        if user.is_demo:
            raise SystemExit("Refusing to repair a demo user")
        client = GoogleClient(user, db)
        print(repair_missing_thread_ids(db, user, client))
        return 0
    except (GoogleAuthError, GoogleAPIError, ValueError) as exc:
        db.rollback()
        print(f"Thread repair failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if client:
            client.close()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
