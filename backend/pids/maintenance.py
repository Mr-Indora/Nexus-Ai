"""Explicit, dry-run-first PIDS retention and vault cleanup command."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from pids.persistence.models import RawRecordRow
from pids.runtime import pids_session
from pids.vault import RawDataVault


def run(*, apply: bool = False) -> dict:
    with pids_session() as session:
        vault = RawDataVault()
        now = datetime.now(timezone.utc)
        expired_rows = session.scalars(select(RawRecordRow).where(
            RawRecordRow.retention_expires_at.is_not(None),
        )).all()
        expired_rows = [row for row in expired_rows if row.retention_expires_at <= now]
        if not any(row.content_ref for row in expired_rows):
            return {"expiredRecords": len(expired_rows), "eligibleBlobs": 0, "deletedBlobs": 0}

        referenced = set(session.execute(select(
            RawRecordRow.owner_id, RawRecordRow.content_sha256,
        ).where(
            RawRecordRow.content_ref.is_not(None),
            RawRecordRow.retention_expires_at.is_(None)
            | (RawRecordRow.retention_expires_at > now),
        )).all())
        report = vault.enforce_retention(
            referenced, now=now, dry_run=not apply,
            minimum_age=timedelta(days=1),
        )
        eligible_refs = {
            reference for reference in (report.removed_refs if apply else report.orphaned_refs)
        }
        eligible = {
            (int(parts[1]), parts[3]) for reference in eligible_refs
            if len(parts := reference.split("/")) == 4
        }
        if apply and eligible:
            for row in expired_rows:
                if (row.owner_id, row.content_sha256) in eligible:
                    row.content_ref = None
                    row.payload_size_bytes = None
            session.commit()
        return {
            "expiredRecords": len(expired_rows),
            "eligibleBlobs": len(eligible),
            "deletedBlobs": len(eligible) if apply else 0,
            "mode": "apply" if apply else "dry-run",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete eligible expired raw payloads")
    args = parser.parse_args()
    print(run(apply=args.apply))


if __name__ == "__main__":
    main()
