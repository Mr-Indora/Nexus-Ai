import hashlib
import json
import os
import shutil
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

from pids.persistence.database import ISOLATED_WORKSPACE
from pids.vault import (
    VaultError,
    VaultIntegrityError,
    VaultKeyError,
    VaultKeyManager,
    WindowsDPAPIVaultKeyManager,
    RawDataVault,
)


UTC = timezone.utc
ALEMBIC_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "pids"
    / "persistence"
    / "alembic.ini"
)


class MemoryKeyProvider:
    def __init__(self):
        self.keys: dict[str, bytes] = {}
        self.active_id: str | None = None

    def ensure_active_key(self) -> str:
        return self.active_id or self.generate_key()

    def get_key(self, key_id: str | None = None) -> bytes:
        resolved = key_id or self.ensure_active_key()
        try:
            return self.keys[resolved]
        except KeyError as exc:
            raise VaultKeyError("vault key is not available") from exc

    def generate_key(self, *, key_id: str | None = None) -> str:
        resolved = key_id or f"test-{uuid4().hex}"
        if resolved in self.keys:
            raise VaultKeyError("vault key id already exists")
        self.keys[resolved] = os.urandom(32)
        self.active_id = resolved
        return resolved

    def rotate(self, *, key_id: str | None = None) -> str:
        return self.generate_key(key_id=key_id)


@pytest.fixture
def vault():
    root = ISOLATED_WORKSPACE / f"security-vault-{uuid4().hex}"
    provider = MemoryKeyProvider()
    instance = RawDataVault(root, key_provider=provider)
    yield instance
    if root.exists():
        shutil.rmtree(root)


def _ciphertext(vault: RawDataVault, owner_id: int, digest: str) -> bytes:
    return vault._path(owner_id, digest).read_bytes()


def test_encrypted_first_write_and_existing_plaintext_upgrade(vault):
    encrypted_payload = b"first encrypted synthetic record"
    digest, _, _ = vault.store(811, encrypted_payload)
    ciphertext = _ciphertext(vault, 811, digest)
    assert ciphertext.startswith(b"PIDSENC1")
    assert encrypted_payload not in ciphertext
    assert vault.read(811, digest) == encrypted_payload

    plaintext = b"legacy plaintext synthetic record"
    legacy_digest, _, _ = vault.store(811, plaintext, encrypt=False)
    legacy_path = vault._path(811, legacy_digest)
    assert legacy_path.read_bytes() == plaintext
    vault.store(811, plaintext, encrypt=True)
    assert legacy_path.read_bytes().startswith(b"PIDSENC1")
    assert plaintext not in legacy_path.read_bytes()
    assert vault.read(811, legacy_digest) == plaintext

    magic_prefix = b"PIDSENC1 is valid synthetic plaintext"
    plain_digest, _, _ = vault.store(811, magic_prefix, encrypt=False)
    assert vault.read(811, plain_digest) == magic_prefix


def test_wrong_missing_invalid_nonce_and_corrupted_ciphertext_fail_closed(vault):
    payload = b"authenticated record"
    digest, _, _ = vault.store(812, payload, encrypt=True)
    original = _ciphertext(vault, 812, digest)

    wrong_provider = MemoryKeyProvider()
    wrong_provider.keys.update(vault.key_provider.keys)
    wrong_provider.active_id = vault.key_provider.active_id
    wrong_provider.keys[wrong_provider.active_id] = os.urandom(32)
    wrong_vault = RawDataVault(vault.root, key_provider=wrong_provider)
    with pytest.raises(VaultIntegrityError, match="authentication"):
        wrong_vault.read(812, digest)

    missing_provider = MemoryKeyProvider()
    with pytest.raises(VaultKeyError, match="not available"):
        RawDataVault(vault.root, key_provider=missing_provider).read(812, digest)

    path = vault._path(812, digest)
    magic_end = len(b"PIDSENC1")
    header_len = int.from_bytes(original[magic_end : magic_end + 4], "big")
    header_start = magic_end + 4
    header = json.loads(original[header_start : header_start + header_len])
    header["nonce"] = "invalid"
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("ascii")
    malformed = (
        original[:magic_end]
        + len(encoded).to_bytes(4, "big")
        + encoded
        + original[header_start + header_len :]
    )
    path.write_bytes(malformed)
    with pytest.raises(VaultIntegrityError, match="header is malformed"):
        vault.read(812, digest)

    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    with pytest.raises(VaultIntegrityError, match="authentication"):
        vault.read(812, digest)


def test_rotation_preserves_old_key_and_reencrypts_with_new_key(vault):
    payload = b"rotation-safe synthetic data"
    digest, _, _ = vault.store(813, payload, encrypt=True)
    old_key_id = vault.key_provider.active_id
    new_key_id = vault.key_provider.rotate()
    assert old_key_id != new_key_id
    assert vault.read(813, digest) == payload
    assert vault.reencrypt(813, digest) == new_key_id
    encoded = _ciphertext(vault, 813, digest)
    header_start = len(b"PIDSENC1") + 4
    header_len = int.from_bytes(encoded[len(b"PIDSENC1") : header_start], "big")
    header = json.loads(encoded[header_start : header_start + header_len])
    assert header["key_id"] == new_key_id
    assert old_key_id in vault.key_provider.keys
    assert vault.read(813, digest) == payload


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI is Windows-only")
def test_windows_dpapi_provider_migrates_legacy_key_ring_without_changing_key(tmp_path):
    key_path = ISOLATED_WORKSPACE / f"dpapi-keys-{uuid4().hex}.json"
    root = ISOLATED_WORKSPACE / f"dpapi-vault-{uuid4().hex}"
    legacy = VaultKeyManager(key_path)
    key_id = legacy.ensure_active_key()
    key = legacy.get_key(key_id)

    try:
        protected = WindowsDPAPIVaultKeyManager(key_path)
        assert key_path.read_bytes().startswith(WindowsDPAPIVaultKeyManager._magic)
        assert protected.ensure_active_key() == key_id
        assert protected.get_key(key_id) == key
        vault = RawDataVault(root, key_provider=protected, production=True)
        digest, _, _ = vault.store(820, b"local DPAPI synthetic test", encrypt=True)
        assert vault.read(820, digest) == b"local DPAPI synthetic test"
    finally:
        if root.exists():
            shutil.rmtree(root)
        key_path.unlink(missing_ok=True)
        key_path.with_name(f"{key_path.name}.lock").unlink(missing_ok=True)

def test_export_and_delete_are_owner_scoped(vault, tmp_path):
    payload = b"owner scoped synthetic payload"
    digest, _, _ = vault.store(814, payload, encrypt=True)
    with pytest.raises(VaultIntegrityError, match="missing"):
        vault.export(815, digest)
    assert vault.delete(815, digest) is False
    assert vault.read(814, digest) == payload
    copied_path = vault._path(815, digest)
    copied_path.parent.mkdir(parents=True)
    copied_path.write_bytes(vault._path(814, digest).read_bytes())
    with pytest.raises(VaultIntegrityError, match="header is malformed"):
        vault.read(815, digest)
    destination = tmp_path / "synthetic-export.bin"
    assert vault.export(814, digest, destination) == destination
    assert destination.read_bytes() == payload


def test_retention_dry_run_and_enforcement_protect_referenced_payloads(vault):
    expired_at = datetime.now(UTC) - timedelta(days=2)
    used_digest, _, _ = vault.store(
        816, b"referenced synthetic evidence", retention_expires_at=expired_at
    )
    orphan_digest, _, _ = vault.store(
        816, b"unreferenced synthetic evidence", retention_expires_at=expired_at
    )
    now = datetime.now(UTC)
    dry_run = vault.enforce_retention(
        {(816, used_digest)}, now=now, minimum_age=timedelta(0)
    )
    assert dry_run.orphaned_refs == (vault.reference(816, orphan_digest),)
    assert dry_run.protected_refs == (vault.reference(816, used_digest),)
    assert dry_run.removed_refs == ()

    enforced = vault.enforce_retention(
        {(816, used_digest)},
        now=now,
        dry_run=False,
        minimum_age=timedelta(0),
    )
    assert enforced.removed_refs == (vault.reference(816, orphan_digest),)
    assert vault.read(816, used_digest) == b"referenced synthetic evidence"
    with pytest.raises(VaultIntegrityError, match="missing"):
        vault.read(816, orphan_digest)
    assert not vault._meta_path(816, orphan_digest).exists()


def test_orphan_cleanup_removes_metadata_and_keeps_fresh_files(vault):
    digest, reference, _ = vault.store(
        817,
        b"orphan with metadata",
        encrypt=True,
        retention_expires_at=datetime.now(UTC) + timedelta(days=2),
    )
    report = vault.cleanup_orphans(set(), dry_run=False, minimum_age=timedelta(0))
    assert report.removed_refs == (reference,)
    assert not vault._path(817, digest).exists()
    assert not vault._meta_path(817, digest).exists()

    fresh_digest, fresh_reference, _ = vault.store(817, b"fresh during cleanup")
    report = vault.cleanup_orphans(set(), dry_run=False)
    assert fresh_reference in report.orphaned_refs
    assert report.removed_refs == ()
    assert vault.read(817, fresh_digest) == b"fresh during cleanup"


def test_concurrent_ingestion_and_orphan_cleanup_respect_lock_and_grace(vault, monkeypatch):
    entered_write = threading.Event()
    release_write = threading.Event()
    original_write = vault._write_blob_atomic

    def paused_write(destination: Path, blob: bytes):
        original_write(destination, blob)
        entered_write.set()
        assert release_write.wait(timeout=5)

    monkeypatch.setattr(vault, "_write_blob_atomic", paused_write)
    payload = b"concurrent ingestion record"
    with ThreadPoolExecutor(max_workers=2) as pool:
        store_future = pool.submit(vault.store, 818, payload)
        assert entered_write.wait(timeout=5)
        cleanup_future = pool.submit(vault.cleanup_orphans, set(), dry_run=False)
        release_write.set()
        digest, reference, _ = store_future.result(timeout=5)
        cleanup = cleanup_future.result(timeout=5)

    assert reference in cleanup.orphaned_refs
    assert reference not in cleanup.removed_refs
    assert vault.read(818, digest) == payload


def test_vault_write_failure_restores_existing_data_and_removes_new_partial_data(
    vault, monkeypatch
):
    payload = b"recoverable synthetic data"
    digest, _, _ = vault.store(819, payload)
    path = vault._path(819, digest)
    old_blob = path.read_bytes()

    def fail_metadata(*_args, **_kwargs):
        raise VaultError("synthetic metadata failure")

    monkeypatch.setattr(vault, "_write_metadata", fail_metadata)
    with pytest.raises(VaultError, match="synthetic metadata failure"):
        vault.store(
            819,
            payload,
            encrypt=True,
            retention_expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    assert path.read_bytes() == old_blob
    assert vault.read(819, digest) == payload

    fresh = b"new data must not be partially committed"
    fresh_digest = hashlib.sha256(fresh).hexdigest()
    with pytest.raises(VaultError, match="synthetic metadata failure"):
        vault.store(
            819,
            fresh,
            encrypt=True,
            retention_expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    assert not vault._path(819, fresh_digest).exists()


def test_key_metadata_rejects_malformed_material_and_is_not_exposed(tmp_path):
    key_path = ISOLATED_WORKSPACE / f"security-keys-{uuid4().hex}.json"
    manager = VaultKeyManager(key_path)
    key_id = manager.generate_key()
    assert manager.get_key(key_id)
    assert not hasattr(manager.list_keys()[0], "material_b64")

    original = json.loads(key_path.read_text(encoding="utf-8"))
    original["keys"][key_id]["material_b64"] = "not-base64!"
    key_path.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(VaultKeyError, match="malformed"):
        VaultKeyManager(key_path)
    key_path.unlink(missing_ok=True)


def test_concurrent_file_provider_rotation_keeps_all_keys_readable():
    key_path = ISOLATED_WORKSPACE / f"concurrent-keys-{uuid4().hex}.json"
    root = ISOLATED_WORKSPACE / f"concurrent-key-vault-{uuid4().hex}"
    first = VaultKeyManager(key_path)
    second = VaultKeyManager(key_path)
    vault = RawDataVault(root, key_provider=first)
    payload = b"key rotation concurrent synthetic payload"
    old_key_id = first.ensure_active_key()
    digest, _, _ = vault.store(822, payload)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            rotations = [
                pool.submit(first.rotate, key_id="rotation-a"),
                pool.submit(second.rotate, key_id="rotation-b"),
            ]
            rotated_ids = {future.result(timeout=5) for future in rotations}

        latest = VaultKeyManager(key_path)
        assert rotated_ids == {"rotation-a", "rotation-b"}
        assert {record.key_id for record in latest.list_keys()} == {
            old_key_id,
            "rotation-a",
            "rotation-b",
        }
        assert sum(record.status == "active" for record in latest.list_keys()) == 1
        assert vault.read(822, digest) == payload
    finally:
        if root.exists():
            shutil.rmtree(root)
        key_path.unlink(missing_ok=True)
        key_path.with_name(f"{key_path.name}.lock").unlink(missing_ok=True)


def test_corrupt_or_legacy_encryption_metadata_fails_closed(vault):
    payload = b"plaintext remains plaintext when metadata is invalid"
    digest, _, _ = vault.store(820, payload, encrypt=False)
    metadata_path = vault._meta_path(820, digest)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(
            {
                "owner_id": 820,
                "digest": digest,
                "encrypted": True,
                "key_id": "test-key",
                "nonce": None,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(VaultIntegrityError, match="metadata is incomplete"):
        vault.read(820, digest)
    with pytest.raises(VaultIntegrityError, match="metadata is incomplete"):
        vault.store(820, payload, encrypt=True)
    assert vault._path(820, digest).read_bytes() == payload

    metadata_path.write_text("{", encoding="utf-8")
    with pytest.raises(VaultIntegrityError, match="metadata is unreadable"):
        vault.read(820, digest)


def test_metadata_symlink_is_rejected_when_supported(vault):
    digest, _, _ = vault.store(821, b"metadata symlink target", encrypt=False)
    metadata_path = vault._meta_path(821, digest)
    target = vault.root / "synthetic-metadata-target.json"
    target.write_text(
        json.dumps({"owner_id": 821, "digest": digest}),
        encoding="utf-8",
    )
    try:
        metadata_path.symlink_to(target)
    except OSError:
        pytest.skip("file symlink creation is not permitted on this host")
    with pytest.raises(VaultIntegrityError, match="symbolic link|reparse point"):
        vault.read(821, digest)


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point attributes are platform-specific")
def test_windows_metadata_reparse_point_is_rejected_without_symlink_privilege(vault, monkeypatch):
    digest, _, _ = vault.store(823, b"synthetic metadata reparse point", encrypt=False)
    metadata_path = vault._meta_path(823, digest)
    original_lstat = Path.lstat

    def report_reparse_point(path):
        if path == Path(os.path.abspath(metadata_path)):
            return SimpleNamespace(
                st_mode=stat.S_IFREG,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", report_reparse_point)
    with pytest.raises(VaultIntegrityError, match="symbolic link or reparse point"):
        vault._assert_safe_path(metadata_path)


def test_production_use_and_migration_urls_fail_closed(vault):
    with pytest.raises(VaultKeyError, match="production-safe key provider"):
        RawDataVault(vault.root, key_provider=MemoryKeyProvider(), production=True)
    valid_path = ISOLATED_WORKSPACE / "safe-test.db"
    valid_url = f"sqlite:///{valid_path.as_posix()}"
    validated = vault.require_safe_migration(valid_url)
    assert Path(make_url(validated).database).resolve() == valid_path.resolve()
    with pytest.raises(ValueError, match="inside pids-isolated-work"):
        vault.require_safe_migration("sqlite:///C:/outside/unauthorized.db")


def test_isolated_migration_downgrade_is_destructive_and_reversible():
    ISOLATED_WORKSPACE.mkdir(parents=True, exist_ok=True)
    database_path = ISOLATED_WORKSPACE / f"migration-audit-{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url)
    try:
        command.upgrade(config, "head")
        assert "pids_evidence_references" in inspect(engine).get_table_names()
        command.downgrade(config, "base")
        assert "pids_evidence_references" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        assert "pids_evidence_references" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)


def test_encrypted_pids_backup_restores_database_vault_and_key():
    from pids.backup import create_backup, restore_backup

    ISOLATED_WORKSPACE.mkdir(parents=True, exist_ok=True)
    identity = uuid4().hex
    database_path = ISOLATED_WORKSPACE / f"backup-source-{identity}.db"
    vault_path = ISOLATED_WORKSPACE / f"backup-vault-{identity}"
    key_path = ISOLATED_WORKSPACE / f"backup-keys-{identity}.json"
    backup_path = ISOLATED_WORKSPACE / f"backup-{identity}.pidsbak"
    restored_database = ISOLATED_WORKSPACE / f"restored-{identity}.db"
    restored_vault = ISOLATED_WORKSPACE / f"restored-vault-{identity}"
    restored_key = ISOLATED_WORKSPACE / f"restored-keys-{identity}.json"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", database_url)
    source_vault = RawDataVault(vault_path, key_provider=VaultKeyManager(key_path))
    digest, _, _ = source_vault.store(881, b"synthetic encrypted backup payload")
    try:
        command.upgrade(config, "head")
        result = create_backup(
            backup_path,
            "test-only recovery phrase with enough length",
            database_url=database_url,
            vault_root=vault_path,
            key_path=key_path,
        )
        assert Path(result["path"]) == backup_path
        assert backup_path.read_bytes().startswith(b"PIDSBAK1")

        restored = restore_backup(
            backup_path,
            target_database=restored_database,
            target_vault=restored_vault,
            target_key=restored_key,
            passphrase="test-only recovery phrase with enough length",
        )
        assert Path(restored["database"]) == restored_database
        restored_store = RawDataVault(restored_vault, key_provider=VaultKeyManager(restored_key))
        assert restored_store.read(881, digest) == b"synthetic encrypted backup payload"
        with pytest.raises(ValueError, match="authentication failed"):
            restore_backup(
                backup_path,
                target_database=ISOLATED_WORKSPACE / f"wrong-{identity}.db",
                target_vault=ISOLATED_WORKSPACE / f"wrong-vault-{identity}",
                target_key=ISOLATED_WORKSPACE / f"wrong-key-{identity}.json",
                passphrase="incorrect recovery phrase",
            )
    finally:
        for path in (database_path, backup_path, restored_database):
            path.unlink(missing_ok=True)
        for path in (vault_path, restored_vault):
            if path.exists():
                shutil.rmtree(path)
        for path in (key_path, restored_key, key_path.with_suffix(key_path.suffix + ".lock"),
                     restored_key.with_suffix(restored_key.suffix + ".lock")):
            path.unlink(missing_ok=True)


def test_encrypted_pids_backup_accepts_known_older_migration_for_pre_upgrade_recovery():
    from pids.backup import create_backup, restore_backup

    ISOLATED_WORKSPACE.mkdir(parents=True, exist_ok=True)
    identity = uuid4().hex
    database_path = ISOLATED_WORKSPACE / f"backup-old-source-{identity}.db"
    vault_path = ISOLATED_WORKSPACE / f"backup-old-vault-{identity}"
    key_path = ISOLATED_WORKSPACE / f"backup-old-keys-{identity}.json"
    backup_path = ISOLATED_WORKSPACE / f"backup-old-{identity}.pidsbak"
    restored_database = ISOLATED_WORKSPACE / f"restored-old-{identity}.db"
    restored_vault = ISOLATED_WORKSPACE / f"restored-old-vault-{identity}"
    restored_key = ISOLATED_WORKSPACE / f"restored-old-keys-{identity}.json"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", database_url)
    passphrase = "test-only previous schema recovery phrase"
    try:
        command.upgrade(config, "0005_history_page_cursor")
        backup = create_backup(
            backup_path, passphrase, database_url=database_url,
            vault_root=vault_path, key_path=key_path,
        )
        assert backup["migrationRevision"] == "0005_history_page_cursor"
        restored = restore_backup(
            backup_path, target_database=restored_database, target_vault=restored_vault,
            target_key=restored_key, passphrase=passphrase,
        )
        assert restored["migrationRevision"] == "0005_history_page_cursor"
        restored_config = Config(str(ALEMBIC_CONFIG))
        restored_config.set_main_option("sqlalchemy.url", f"sqlite:///{restored_database.as_posix()}")
        command.upgrade(restored_config, "head")
    finally:
        for path in (database_path, backup_path, restored_database):
            path.unlink(missing_ok=True)
        for path in (vault_path, restored_vault):
            if path.exists():
                shutil.rmtree(path)
        for path in (key_path, restored_key, key_path.with_suffix(key_path.suffix + ".lock"),
                     restored_key.with_suffix(restored_key.suffix + ".lock")):
            path.unlink(missing_ok=True)
