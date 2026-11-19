"""Create and restore passphrase-encrypted backups of the isolated PIDS store."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.engine import make_url

from alembic.config import Config
from alembic.script import ScriptDirectory
from config import settings
from pids.persistence.database import ISOLATED_WORKSPACE, validate_isolated_database_url
from pids.vault import RawDataVault, VaultKeyManager, WindowsDPAPIVaultKeyManager

_MAGIC = b"PIDSBAK1"
_MAX_BUNDLE_BYTES = 1024 * 1024 * 1024
_SCRYPT_N = 1 << 15


def _inside_workspace(path: Path, *, must_exist: bool = False) -> Path:
    if not path.is_absolute():
        raise ValueError("backup paths must be absolute")
    resolved = path.resolve(strict=must_exist)
    try:
        resolved.relative_to(ISOLATED_WORKSPACE.resolve())
    except ValueError as exc:
        raise ValueError("backup paths must stay inside pids-isolated-work") from exc
    current = ISOLATED_WORKSPACE.resolve()
    relative = resolved.relative_to(current)
    for part in (current, *(current / piece for piece in relative.parts)):
        try:
            details = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("backup path contains a symbolic link or reparse point")
    if must_exist and not resolved.is_file():
        raise ValueError("backup input must be a regular file")
    return resolved


def _database_path(database_url: str) -> Path:
    validated = validate_isolated_database_url(database_url)
    url = make_url(validated)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise ValueError("PIDS encrypted backup currently supports isolated SQLite only")
    return _inside_workspace(Path(url.database), must_exist=True)


def _check_passphrase(passphrase: str) -> None:
    if not isinstance(passphrase, str) or len(passphrase) < 12:
        raise ValueError("backup passphrase must be at least 12 characters")


def _digest_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _known_migration_revisions() -> set[str]:
    script = ScriptDirectory.from_config(
        Config(str(Path(__file__).resolve().parent / "persistence" / "alembic.ini"))
    )
    return {revision.revision for revision in script.walk_revisions()}


def _encrypted_bytes(plaintext: bytes, passphrase: str) -> bytes:
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=8, p=1, dklen=32, maxmem=128 * 1024 * 1024)
    prefix = _MAGIC + salt + nonce
    return prefix + AESGCM(key).encrypt(nonce, plaintext, prefix)


def _decrypt_bytes(envelope: bytes, passphrase: str) -> bytes:
    _check_passphrase(passphrase)
    if len(envelope) < len(_MAGIC) + 16 + 12 + 16 or not envelope.startswith(_MAGIC):
        raise ValueError("backup file format is invalid")
    cursor = len(_MAGIC)
    salt = envelope[cursor:cursor + 16]
    cursor += 16
    nonce = envelope[cursor:cursor + 12]
    cursor += 12
    prefix = envelope[:cursor]
    key = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=8, p=1, dklen=32, maxmem=128 * 1024 * 1024)
    try:
        return AESGCM(key).decrypt(nonce, envelope[cursor:], prefix)
    except Exception as exc:
        raise ValueError("backup authentication failed; check the passphrase or file integrity") from exc


def create_backup(
    destination: Path | str,
    passphrase: str,
    *,
    database_url: str | None = None,
    vault_root: Path | str | None = None,
    key_path: Path | str | None = None,
) -> dict:
    """Snapshot PIDS SQLite plus the raw vault and key metadata into an encrypted file."""
    _check_passphrase(passphrase)
    database_path = _database_path(database_url or settings.PIDS_DATABASE_URL)
    source_vault = _inside_workspace(Path(vault_root or settings.PIDS_VAULT_DIR))
    source_key = _inside_workspace(Path(key_path or settings.PIDS_VAULT_KEY_PATH))
    target = _inside_workspace(Path(destination))
    if target.exists() or target in {database_path, source_vault, source_key}:
        raise FileExistsError("backup output must be a new file inside pids-isolated-work")
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".pids-backup-", dir=ISOLATED_WORKSPACE) as temporary:
        work = Path(temporary)
        snapshot = work / "database.sqlite"
        source_db = sqlite3.connect(database_path)
        backup_db = sqlite3.connect(snapshot)
        try:
            source_db.backup(backup_db)
        finally:
            backup_db.close()
            source_db.close()
        snapshot_db = sqlite3.connect(snapshot)
        try:
            tables = {row[0] for row in snapshot_db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "pids_alembic_version" not in tables:
                raise ValueError("PIDS database migration state is missing")
            revision = snapshot_db.execute("SELECT version_num FROM pids_alembic_version").fetchone()[0]
        finally:
            snapshot_db.close()
        if revision not in _known_migration_revisions():
            raise ValueError("PIDS database has an unknown migration revision; refusing backup")

        key_provider = (
            WindowsDPAPIVaultKeyManager(source_key)
            if os.name == "nt"
            else VaultKeyManager(source_key)
        )
        vault = RawDataVault(source_vault, key_provider=key_provider)
        files: dict[str, Path] = {"database.sqlite": snapshot}
        with vault._exclusive_lock():
            if source_vault.exists():
                for current, directories, filenames in os.walk(source_vault, followlinks=False):
                    current_path = Path(current)
                    directories[:] = [name for name in directories if name != "__pycache__"]
                    for filename in filenames:
                        file_path = current_path / filename
                        details = file_path.lstat()
                        if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                            raise ValueError("vault backup source contains a symbolic link or reparse point")
                        if filename.endswith(".lock"):
                            continue
                        relative = file_path.relative_to(source_vault).as_posix()
                        files[f"vault/{relative}"] = file_path
            with key_provider._exclusive_key_lock():
                if source_key.exists():
                    portable_keyring = work / "vault-keys.json"
                    portable_keyring.write_text(
                        json.dumps(key_provider._load(), indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    files["vault-keys.json"] = portable_keyring

        metadata_files = {}
        total_bytes = 0
        for name, file_path in files.items():
            digest, size = _digest_file(file_path)
            total_bytes += size
            if total_bytes > _MAX_BUNDLE_BYTES:
                raise ValueError("PIDS backup exceeds the 1 GiB local backup limit")
            metadata_files[name] = {"sha256": digest, "size": size}
        manifest = {
            "format": "pids-backup-v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "migration_revision": revision,
            "files": metadata_files,
        }
        archive_path = work / "bundle.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True, separators=(",", ":")))
            for name, file_path in files.items():
                archive.write(file_path, name)
        if archive_path.stat().st_size > _MAX_BUNDLE_BYTES:
            raise ValueError("compressed PIDS backup exceeds the 1 GiB local backup limit")
        encrypted = _encrypted_bytes(archive_path.read_bytes(), passphrase)
        temporary_output = target.with_name(f".{target.name}.tmp")
        try:
            with temporary_output.open("xb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_output, target)
        finally:
            temporary_output.unlink(missing_ok=True)
    return {"path": str(target), "migrationRevision": revision, "files": len(files), "sourceBytes": total_bytes}


def restore_backup(
    source: Path | str,
    *,
    target_database: Path | str,
    target_vault: Path | str,
    target_key: Path | str,
    passphrase: str,
) -> dict:
    """Restore to new isolated paths; existing databases and vaults are never overwritten."""
    _check_passphrase(passphrase)
    backup_path = _inside_workspace(Path(source), must_exist=True)
    database_target = _inside_workspace(Path(target_database))
    vault_target = _inside_workspace(Path(target_vault))
    key_target = _inside_workspace(Path(target_key))
    if database_target.exists() or vault_target.exists() or key_target.exists():
        raise FileExistsError("restore destination already exists; choose new isolated paths")
    if len({database_target, vault_target, key_target}) != 3:
        raise ValueError("database, vault and key restore paths must be distinct")
    envelope_size = backup_path.stat().st_size
    if envelope_size > _MAX_BUNDLE_BYTES + 64:
        raise ValueError("encrypted backup exceeds the 1 GiB restore limit")
    plaintext = _decrypt_bytes(backup_path.read_bytes(), passphrase)
    if len(plaintext) > _MAX_BUNDLE_BYTES:
        raise ValueError("decrypted bundle exceeds the 1 GiB restore limit")

    moved: list[Path] = []
    with tempfile.TemporaryDirectory(prefix=".pids-restore-", dir=ISOLATED_WORKSPACE) as temporary:
        staging = Path(temporary)
        extracted = staging / "contents"
        extracted.mkdir()
        with zipfile.ZipFile(__import__("io").BytesIO(plaintext)) as archive:
            names = set()
            total_size = 0
            for info in archive.infolist():
                name = info.filename
                relative = PurePosixPath(name)
                mode = info.external_attr >> 16
                if ("\\" in name or relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts)
                        or stat.S_ISLNK(mode) or name in names):
                    raise ValueError("backup archive contains an unsafe or duplicate path")
                names.add(name)
                total_size += info.file_size
                if total_size > _MAX_BUNDLE_BYTES:
                    raise ValueError("backup archive exceeds the restore size limit")
                destination = extracted.joinpath(*relative.parts)
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                if name not in {"manifest.json", "database.sqlite", "vault-keys.json"} and not name.startswith("vault/"):
                    raise ValueError("backup archive contains an unexpected file")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source_stream, destination.open("xb") as target_stream:
                    shutil.copyfileobj(source_stream, target_stream)

        manifest = json.loads((extracted / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format") != "pids-backup-v1":
            raise ValueError("backup manifest format is unsupported")
        expected_files = manifest.get("files")
        if not isinstance(expected_files, dict) or "database.sqlite" not in expected_files:
            raise ValueError("backup manifest is incomplete")
        for name, expected in expected_files.items():
            file_path = extracted.joinpath(*PurePosixPath(name).parts)
            if not file_path.is_file():
                raise ValueError("backup is missing a manifested file")
            digest, size = _digest_file(file_path)
            if digest != expected.get("sha256") or size != expected.get("size"):
                raise ValueError("backup file integrity check failed")

        restored_db = extracted / "database.sqlite"
        connection = sqlite3.connect(restored_db)
        try:
            revision_row = connection.execute("SELECT version_num FROM pids_alembic_version").fetchone()
        finally:
            connection.close()
        if (not revision_row or revision_row[0] not in _known_migration_revisions()
                or manifest.get("migration_revision") != revision_row[0]):
            raise ValueError("backup database has an unknown or inconsistent migration revision")

        database_target.parent.mkdir(parents=True, exist_ok=True)
        vault_target.parent.mkdir(parents=True, exist_ok=True)
        key_target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(restored_db, database_target)
            moved.append(database_target)
            extracted_vault = extracted / "vault"
            if extracted_vault.exists():
                os.replace(extracted_vault, vault_target)
                moved.append(vault_target)
            extracted_key = extracted / "vault-keys.json"
            if extracted_key.exists():
                os.replace(extracted_key, key_target)
                moved.append(key_target)
        except Exception:
            for path in reversed(moved):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            raise
    return {"database": str(database_target), "vault": str(vault_target), "keyFile": str(key_target),
            "migrationRevision": revision_row[0]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("create", help="create an encrypted, consistent local PIDS backup")
    create.add_argument("--output", required=True, help="new path under pids-isolated-work")
    restore = commands.add_parser("restore", help="restore an encrypted backup to new paths")
    restore.add_argument("backup", help="encrypted backup file under pids-isolated-work")
    restore.add_argument("--database", required=True, help="new database path under pids-isolated-work")
    restore.add_argument("--vault", required=True, help="new vault directory under pids-isolated-work")
    restore.add_argument("--key-file", required=True, help="new vault key file path under pids-isolated-work")
    args = parser.parse_args()
    passphrase = getpass("Backup passphrase (at least 12 characters): ")
    if args.action == "create":
        if passphrase != getpass("Confirm backup passphrase: "):
            raise SystemExit("Passphrases did not match.")
        result = create_backup(Path(args.output), passphrase)
    else:
        result = restore_backup(
            Path(args.backup), target_database=Path(args.database),
            target_vault=Path(args.vault), target_key=Path(args.key_file),
            passphrase=passphrase,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
