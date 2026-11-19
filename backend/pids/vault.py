"""Content-addressed, owner-isolated raw payload storage for PIDS."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from pids.persistence.database import ISOLATED_WORKSPACE, validate_isolated_database_url

MAX_PAYLOAD_BYTES = 256 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_ENVELOPE_MAGIC = b"PIDSENC1"
_ENVELOPE_LENGTH_BYTES = 4
_DEFAULT_CLEANUP_GRACE = timedelta(days=1)
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}


def _thread_lock_for(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path))
    with _LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


class VaultError(RuntimeError):
    """Safe public error for raw-payload storage or integrity failures."""


class VaultIntegrityError(VaultError):
    pass


class VaultKeyError(VaultError):
    pass


class VaultKeyProvider(Protocol):
    """Key-provider contract; production providers must use an OS/KMS secret store."""

    def ensure_active_key(self) -> str: ...

    def get_key(self, key_id: str | None = None) -> bytes: ...

    def generate_key(self, *, key_id: str | None = None) -> str: ...

    def rotate(self, *, key_id: str | None = None) -> str: ...


@dataclass(frozen=True)
class VaultKeyRecord:
    key_id: str
    algorithm: str
    created_at: datetime
    status: str
    retired_at: datetime | None = None


@dataclass(frozen=True)
class VaultReport:
    orphaned_refs: tuple[str, ...] = ()
    missing_refs: tuple[str, ...] = ()
    removed_refs: tuple[str, ...] = ()
    temporary_files: tuple[str, ...] = ()
    protected_refs: tuple[str, ...] = ()


class VaultKeyManager:
    """Development-only JSON key provider; Base64 does not protect key material."""

    production_safe = False

    def __init__(self, path: Path | str | None = None):
        default_path = Path(path) if path is not None else ISOLATED_WORKSPACE / "vault-keys.json"
        self.path = default_path.absolute()
        try:
            resolved_workspace = ISOLATED_WORKSPACE.resolve()
            self.path.resolve().relative_to(resolved_workspace)
        except ValueError as exc:
            raise ValueError("vault key file must remain inside pids-isolated-work") from exc
        current = resolved_workspace
        for component in (current, *(current / part for part in self.path.relative_to(ISOLATED_WORKSPACE.absolute()).parts)):
            try:
                details = component.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(details.st_mode) or (
                getattr(details, "st_file_attributes", 0) & _REPARSE_POINT
            ):
                raise VaultIntegrityError("vault key path contains a symbolic link or reparse point")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state: dict[str, Any] = self._load()

    @contextmanager
    def _exclusive_key_lock(self):
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        try:
            resolved_workspace = ISOLATED_WORKSPACE.resolve()
            lock_path.resolve().relative_to(resolved_workspace)
        except (OSError, ValueError) as exc:
            raise VaultKeyError("vault key lock path is unsafe") from exc
        if lock_path.is_symlink() or (
            lock_path.exists()
            and getattr(lock_path.stat(), "st_file_attributes", 0) & _REPARSE_POINT
        ):
            raise VaultKeyError("vault key lock path is unsafe")
        with lock_path.open("a+b") as lock_file:
            if lock_file.seek(0, os.SEEK_END) == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"keys": {}, "active_key_id": None}
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError) as exc:
            raise VaultKeyError("vault key metadata is unreadable") from exc
        return self._validate_state(payload)

    @staticmethod
    def _validate_state(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise VaultKeyError("vault key metadata is malformed")
        keys = payload.get("keys", {})
        active_key_id = payload.get("active_key_id")
        if not isinstance(keys, dict) or (
            active_key_id is not None
            and (not isinstance(active_key_id, str) or not _KEY_ID.fullmatch(active_key_id))
        ):
            raise VaultKeyError("vault key metadata is malformed")
        for key_id, entry in keys.items():
            if (
                not isinstance(key_id, str)
                or not _KEY_ID.fullmatch(key_id)
                or not isinstance(entry, dict)
                or entry.get("key_id") != key_id
                or entry.get("algorithm") != "AES-256-GCM"
                or entry.get("status") not in {"active", "retired"}
            ):
                raise VaultKeyError("vault key metadata is malformed")
            try:
                created_at = datetime.fromisoformat(entry["created_at"])
                key = base64.b64decode(entry["material_b64"], validate=True)
                retired_at = (
                    datetime.fromisoformat(entry["retired_at"])
                    if entry.get("retired_at") is not None
                    else None
                )
            except (KeyError, TypeError, ValueError, binascii.Error) as exc:
                raise VaultKeyError("vault key metadata is malformed") from exc
            if (
                created_at.tzinfo is None
                or created_at.utcoffset() is None
                or len(key) != 32
                or (retired_at is not None and (
                    retired_at.tzinfo is None or retired_at.utcoffset() is None
                ))
            ):
                raise VaultKeyError("vault key metadata is malformed")
        if active_key_id is not None:
            active_entry = keys.get(active_key_id)
            if not isinstance(active_entry, dict) or active_entry.get("status") != "active":
                raise VaultKeyError("active vault key metadata is inconsistent")
        return {"keys": keys, "active_key_id": active_key_id}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(prefix=".pids-keys-", dir=self.path.parent)
            temp_path = Path(temp_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self._state, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError as exc:
            raise VaultKeyError("vault key metadata could not be saved") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def ensure_active_key(self) -> str:
        with self._exclusive_key_lock():
            self._state = self._load()
            active_id = self._state.get("active_key_id")
            if active_id and self._state["keys"].get(active_id):
                return active_id
            return self._generate_key_locked()

    def list_keys(self) -> tuple[VaultKeyRecord, ...]:
        self._state = self._load()
        records: list[VaultKeyRecord] = []
        for key_id, value in self._state.get("keys", {}).items():
            if not isinstance(value, dict):
                continue
            records.append(
                VaultKeyRecord(
                    key_id=str(key_id),
                    algorithm=str(value.get("algorithm", "AES-256-GCM")),
                    created_at=datetime.fromisoformat(value["created_at"]),
                    status=str(value.get("status", "retired")),
                    retired_at=(
                        datetime.fromisoformat(value["retired_at"]) if value.get("retired_at") else None
                    ),
                )
            )
        return tuple(sorted(records, key=lambda item: item.created_at))

    def get_key(self, key_id: str | None = None) -> bytes:
        self._state = self._load()
        resolved = self._state.get("active_key_id") if key_id is None else key_id
        if resolved is None:
            resolved = self.ensure_active_key()
        entry = self._state.get("keys", {}).get(str(resolved))
        if not isinstance(entry, dict) or entry.get("algorithm") != "AES-256-GCM":
            raise VaultKeyError("vault key is not available")
        material = entry.get("material_b64")
        if not isinstance(material, str):
            raise VaultKeyError("vault key material is missing")
        try:
            key = base64.b64decode(material, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise VaultKeyError("vault key material is malformed") from exc
        if len(key) != 32:
            raise VaultKeyError("vault key material has an invalid length")
        return key

    def generate_key(self, *, key_id: str | None = None) -> str:
        with self._exclusive_key_lock():
            self._state = self._load()
            return self._generate_key_locked(key_id=key_id)

    def _generate_key_locked(self, *, key_id: str | None = None) -> str:
        new_key_id = key_id or f"pids-vault-{uuid.uuid4().hex}"
        if not isinstance(new_key_id, str) or not _KEY_ID.fullmatch(new_key_id):
            raise ValueError("vault key id has an invalid format")
        if new_key_id in self._state["keys"]:
            raise VaultKeyError("vault key id already exists")
        previous_state = json.loads(json.dumps(self._state))
        active_id = self._state.get("active_key_id")
        if active_id:
            active_entry = self._state["keys"].get(active_id)
            if isinstance(active_entry, dict):
                active_entry["status"] = "retired"
                active_entry["retired_at"] = self._now().isoformat()
        material = AESGCM.generate_key(bit_length=256)
        self._state["keys"][new_key_id] = {
            "key_id": new_key_id,
            "algorithm": "AES-256-GCM",
            "created_at": self._now().isoformat(),
            "status": "active",
            "material_b64": base64.b64encode(material).decode("ascii"),
        }
        self._state["active_key_id"] = new_key_id
        try:
            self._save()
        except VaultKeyError:
            self._state = previous_state
            raise
        return new_key_id

    def rotate(self, *, key_id: str | None = None) -> str:
        return self.generate_key(key_id=key_id)


class RawDataVault:
    def __init__(
        self,
        root: Path | str | None = None,
        *,
        key_manager: VaultKeyProvider | None = None,
        key_provider: VaultKeyProvider | None = None,
        production: bool = False,
    ):
        if key_manager is not None and key_provider is not None:
            raise ValueError("provide only one vault key provider")
        requested = Path(root) if root is not None else Path(
            os.getenv("PIDS_VAULT_DIR", str(ISOLATED_WORKSPACE / "raw-vault"))
        )
        if not requested.is_absolute():
            raise ValueError("vault root must be absolute")
        if ".." in requested.parts:
            raise ValueError("vault root must be inside pids-isolated-work")
        try:
            requested.absolute().relative_to(ISOLATED_WORKSPACE.absolute())
        except ValueError as exc:
            raise ValueError("vault root must be inside pids-isolated-work") from exc
        if requested.absolute() == ISOLATED_WORKSPACE.absolute():
            raise ValueError("vault root must be a dedicated subdirectory")
        self.root = requested.absolute()
        key_path = Path(os.getenv("PIDS_VAULT_KEY_PATH", str(ISOLATED_WORKSPACE / "vault-keys.json")))
        self.key_provider = key_provider or key_manager
        if self.key_provider is None:
            self.key_provider = (
                WindowsDPAPIVaultKeyManager(key_path)
                if os.name == "nt"
                else VaultKeyManager(key_path)
            )
        if production and not getattr(self.key_provider, "production_safe", False):
            raise VaultKeyError("production vault use requires a production-safe key provider")
        self.key_manager = self.key_provider
        self._assert_safe_path(self.root)


    @contextmanager
    def _exclusive_lock(self):
        self._ensure_directory(self.root)
        lock_path = self.root / ".pids-vault.lock"
        self._assert_safe_path(lock_path)
        # Serialize same-process lock-file initialization as well as payload
        # operations. The OS lock still coordinates separate processes.
        with _thread_lock_for(lock_path):
            with lock_path.open("a+b") as lock_file:
                if lock_file.seek(0, os.SEEK_END) == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if os.name == "nt":
                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _owner(owner_id: int) -> int:
        if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id <= 0:
            raise ValueError("owner_id must be a positive integer")
        return owner_id

    @staticmethod
    def _digest(digest: str) -> str:
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError("payload digest must be a lowercase SHA-256")
        return digest

    @staticmethod
    def _isoformat(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc).isoformat()

    def reference(self, owner_id: int, digest: str) -> str:
        owner_id = self._owner(owner_id)
        digest = self._digest(digest)
        return f"sha256/{owner_id}/{digest[:2]}/{digest}"

    def _path(self, owner_id: int, digest: str) -> Path:
        owner_id = self._owner(owner_id)
        digest = self._digest(digest)
        return self.root / "sha256" / str(owner_id) / digest[:2] / digest

    def _meta_path(self, owner_id: int, digest: str) -> Path:
        path = self._path(owner_id, digest)
        return path.with_name(f"{path.name}.meta.json")

    def _assert_safe_path(self, path: Path) -> None:
        workspace_path = Path(os.path.abspath(ISOLATED_WORKSPACE))
        absolute_path = Path(os.path.abspath(path))
        try:
            absolute_path.relative_to(workspace_path)
        except ValueError as exc:
            raise ValueError("vault path escapes pids-isolated-work") from exc

        # Inspect the caller's original components before resolve() can follow a
        # symlink or Windows junction and erase evidence of the unsafe path.
        components = list(reversed(absolute_path.parents)) + [absolute_path]
        for component in components:
            try:
                details = component.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(details.st_mode) or (
                getattr(details, "st_file_attributes", 0) & _REPARSE_POINT
            ):
                raise VaultIntegrityError("vault path contains a symbolic link or reparse point")
            if component != absolute_path and not stat.S_ISDIR(details.st_mode):
                raise VaultIntegrityError("vault path contains a non-directory component")

        def canonical(path: Path) -> Path:
            resolved = os.path.realpath(path)
            if os.name == "nt":
                # Python may return an extended-length path (\\?\D:\...) for
                # realpath on Windows. Strip that namespace prefix before
                # comparing it with the ordinary absolute workspace path.
                if resolved.startswith("\\\\?\\UNC\\"):
                    resolved = "\\\\" + resolved[8:]
                elif resolved.startswith("\\\\?\\"):
                    resolved = resolved[4:]
            return Path(os.path.normcase(os.path.normpath(resolved)))

        try:
            canonical(absolute_path).relative_to(canonical(workspace_path))
        except (OSError, ValueError) as exc:
            raise ValueError("vault path escapes pids-isolated-work") from exc

    def _ensure_directory(self, directory: Path) -> None:
        self._assert_safe_path(directory)
        missing: list[Path] = []
        current = directory
        while not current.exists():
            missing.append(current)
            current = current.parent
        for item in reversed(missing):
            try:
                item.mkdir()
            except FileExistsError:
                pass
            self._assert_safe_path(item)

    def _read_metadata(self, owner_id: int, digest: str) -> dict[str, Any]:
        meta_path = self._meta_path(owner_id, digest)
        self._assert_safe_path(meta_path)
        if not meta_path.exists():
            return {}
        try:
            with meta_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError) as exc:
            raise VaultIntegrityError("raw payload metadata is unreadable") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("owner_id") != owner_id
            or payload.get("digest") != digest
            or ("encrypted" in payload and not isinstance(payload["encrypted"], bool))
        ):
            raise VaultIntegrityError("raw payload metadata is malformed")
        expiry = payload.get("retention_expires_at")
        if expiry is not None:
            try:
                parsed_expiry = datetime.fromisoformat(expiry)
            except (TypeError, ValueError) as exc:
                raise VaultIntegrityError("raw payload retention metadata is malformed") from exc
            if parsed_expiry.tzinfo is None or parsed_expiry.utcoffset() is None:
                raise VaultIntegrityError("raw payload retention timestamp lacks a timezone")
        if payload.get("encrypted"):
            key_id = payload.get("key_id")
            nonce = payload.get("nonce")
            if (
                not isinstance(key_id, str)
                or not _KEY_ID.fullmatch(key_id)
                or not isinstance(nonce, str)
                or not re.fullmatch(r"[0-9a-f]{24}", nonce)
            ):
                raise VaultIntegrityError("encrypted raw payload metadata is incomplete")
        return payload

    def _write_metadata(
        self,
        owner_id: int,
        digest: str,
        *,
        retention_expires_at: datetime | None = None,
    ) -> None:
        meta_path = self._meta_path(owner_id, digest)
        self._assert_safe_path(meta_path)
        payload = {
            "owner_id": owner_id,
            "digest": digest,
            "retention_expires_at": self._isoformat(retention_expires_at),
            "updated_at": self._isoformat(datetime.now(timezone.utc)),
        }
        self._ensure_directory(meta_path.parent)
        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(prefix=".pids-meta-", dir=meta_path.parent)
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, meta_path)
        except OSError as exc:
            raise VaultError("raw payload metadata could not be saved") from exc
        finally:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def _encrypt(
        self, owner_id: int, digest: str, payload: bytes, key_id: str | None = None
    ) -> tuple[bytes, str]:
        resolved_key_id = key_id or self.key_provider.ensure_active_key()
        if not _KEY_ID.fullmatch(resolved_key_id):
            raise VaultKeyError("vault key id has an invalid format")
        key = self.key_provider.get_key(resolved_key_id)
        if len(key) != 32:
            raise VaultKeyError("vault key must contain 256 bits")
        nonce = os.urandom(12)
        header = json.dumps(
            {
                "digest": digest,
                "key_id": resolved_key_id,
                "nonce": nonce.hex(),
                "owner_id": owner_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        prefix = _ENVELOPE_MAGIC + len(header).to_bytes(_ENVELOPE_LENGTH_BYTES, "big") + header
        encrypted = prefix + AESGCM(key).encrypt(nonce, payload, prefix)
        return encrypted, resolved_key_id

    def _decrypt_if_needed(self, owner_id: int, digest: str, payload: bytes) -> bytes:
        metadata = self._read_metadata(owner_id, digest)
        if not metadata.get("encrypted") and hashlib.sha256(payload).hexdigest() == digest:
            return payload
        if payload.startswith(_ENVELOPE_MAGIC):
            header_start = len(_ENVELOPE_MAGIC)
            header_end = header_start + _ENVELOPE_LENGTH_BYTES
            if len(payload) < header_end:
                raise VaultIntegrityError("encrypted raw payload header is truncated")
            header_size = int.from_bytes(payload[header_start:header_end], "big")
            body_start = header_end + header_size
            if header_size <= 0 or header_size > 1024 or body_start >= len(payload):
                raise VaultIntegrityError("encrypted raw payload header is invalid")
            prefix = payload[:body_start]
            try:
                header = json.loads(payload[header_end:body_start].decode("ascii"))
                key_id = header["key_id"]
                nonce_hex = header["nonce"]
                if (
                    not isinstance(key_id, str)
                    or not _KEY_ID.fullmatch(key_id)
                    or not isinstance(nonce_hex, str)
                    or not re.fullmatch(r"[0-9a-f]{24}", nonce_hex)
                    or isinstance(header["owner_id"], bool)
                    or not isinstance(header["owner_id"], int)
                    or header["owner_id"] != owner_id
                    or header["digest"] != digest
                ):
                    raise ValueError("invalid encrypted payload header fields")
                nonce = bytes.fromhex(nonce_hex)
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise VaultIntegrityError("encrypted raw payload header is malformed") from exc
            key = self.key_provider.get_key(key_id)
            if len(key) != 32:
                raise VaultKeyError("vault key must contain 256 bits")
            try:
                return AESGCM(key).decrypt(nonce, payload[body_start:], prefix)
            except InvalidTag as exc:
                raise VaultIntegrityError("raw payload authentication failed") from exc

        if not metadata.get("encrypted"):
            return payload
        key_id = metadata["key_id"]
        nonce = bytes.fromhex(metadata["nonce"])
        key = self.key_provider.get_key(key_id)
        if len(key) != 32:
            raise VaultKeyError("vault key must contain 256 bits")
        try:
            return AESGCM(key).decrypt(nonce, payload, None)
        except InvalidTag as exc:
            raise VaultIntegrityError("encrypted raw payload integrity check failed") from exc

    def store(
        self,
        owner_id: int,
        payload: bytes,
        *,
        encrypt: bool = True,
        key_id: str | None = None,
        retention_expires_at: datetime | None = None,
    ) -> tuple[str, str, int]:
        owner_id = self._owner(owner_id)
        if not isinstance(payload, bytes):
            raise TypeError("raw payload must be bytes")
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise ValueError("raw payload exceeds the byte limit")

        digest = hashlib.sha256(payload).hexdigest()
        reference = self.reference(owner_id, digest)
        destination = self._path(owner_id, digest)
        self._ensure_directory(destination.parent)
        self._assert_safe_path(destination)
        with self._exclusive_lock():
            metadata = self._read_metadata(owner_id, digest) if destination.exists() else {}
            existing_blob: bytes | None = None
            if destination.exists():
                self._verify(destination, digest)
                existing_blob = destination.read_bytes()
                if not encrypt or existing_blob.startswith(_ENVELOPE_MAGIC):
                    if retention_expires_at is not None:
                        self._write_metadata(
                            owner_id, digest, retention_expires_at=retention_expires_at
                        )
                    return digest, reference, len(payload)
                blob, _ = self._encrypt(owner_id, digest, payload, key_id)
            else:
                blob = self._encrypt(owner_id, digest, payload, key_id)[0] if encrypt else payload

            old_metadata_expiry = metadata.get("retention_expires_at")
            expiry_to_keep = retention_expires_at
            if expiry_to_keep is None and old_metadata_expiry is not None:
                expiry_to_keep = datetime.fromisoformat(old_metadata_expiry)
            try:
                self._write_blob_atomic(destination, blob)
                self._verify(destination, digest)
                if expiry_to_keep is not None or metadata:
                    self._write_metadata(
                        owner_id, digest, retention_expires_at=expiry_to_keep
                    )
            except Exception:
                if existing_blob is None:
                    destination.unlink(missing_ok=True)
                    self._meta_path(owner_id, digest).unlink(missing_ok=True)
                else:
                    self._write_blob_atomic(destination, existing_blob)
                raise
            return digest, reference, len(payload)

    def _write_blob_atomic(self, destination: Path, blob: bytes) -> None:
        temp_path: Path | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(prefix=".pids-", dir=destination.parent)
            temp_path = Path(temp_name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(blob)
                stream.flush()
                os.fsync(stream.fileno())
            self._assert_safe_path(destination.parent)
            os.replace(temp_path, destination)
        except OSError as exc:
            raise VaultError("raw payload could not be stored") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def read(self, owner_id: int, digest: str) -> bytes:
        owner_id = self._owner(owner_id)
        digest = self._digest(digest)
        path = self._path(owner_id, digest)
        self._assert_safe_path(path)
        try:
            details = path.lstat()
            if not stat.S_ISREG(details.st_mode):
                raise VaultIntegrityError("raw payload is not a regular file")
            with path.open("rb") as stream:
                payload = stream.read(MAX_PAYLOAD_BYTES + 1024)
        except FileNotFoundError as exc:
            raise VaultIntegrityError("raw payload is missing") from exc
        except VaultIntegrityError:
            raise
        except OSError as exc:
            raise VaultError("raw payload could not be read") from exc

        plaintext = self._decrypt_if_needed(owner_id, digest, payload)
        if len(plaintext) > MAX_PAYLOAD_BYTES or hashlib.sha256(plaintext).hexdigest() != digest:
            raise VaultIntegrityError("raw payload integrity check failed")
        return plaintext

    def export(self, owner_id: int, digest: str, destination: Path | str | None = None) -> bytes | Path:
        payload = self.read(owner_id, digest)
        if destination is None:
            return payload
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination_path.write_bytes(payload)
        except OSError as exc:
            raise VaultError("raw payload export failed") from exc
        return destination_path

    def delete(self, owner_id: int, digest: str, *, dry_run: bool = False) -> bool:
        owner_id = self._owner(owner_id)
        digest = self._digest(digest)
        with self._exclusive_lock():
            return self._delete_locked(owner_id, digest, dry_run=dry_run)

    def _delete_locked(self, owner_id: int, digest: str, *, dry_run: bool) -> bool:
        path = self._path(owner_id, digest)
        meta_path = self._meta_path(owner_id, digest)
        if not path.exists() and not meta_path.exists():
            return False
        if dry_run:
            return True
        try:
            if path.exists():
                path.unlink()
            if meta_path.exists():
                meta_path.unlink()
        except OSError as exc:
            raise VaultError("raw payload cleanup failed") from exc
        return True

    def retention_scan(
        self, *, now: datetime | None = None, owner_id: int | None = None
    ) -> tuple[str, ...]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("retention scan time must include a timezone")
        current = current.astimezone(timezone.utc)
        resolved_owner = self._owner(owner_id) if owner_id is not None else None
        refs: list[str] = []
        for current_path, _, filenames in os.walk(self.root, followlinks=False):
            current_dir = Path(current_path)
            self._assert_safe_path(current_dir)
            for name in filenames:
                if name.endswith(".meta.json") or name.startswith(".pids-"):
                    continue
                path = current_dir / name
                self._assert_safe_path(path)
                if len(path.parts) < 4:
                    continue
                owner_text = str(path.parts[-3])
                digest = path.name
                if not owner_text.isdigit() or not _SHA256.fullmatch(digest):
                    continue
                payload_owner = int(owner_text)
                if resolved_owner is not None and payload_owner != resolved_owner:
                    continue
                metadata = self._read_metadata(payload_owner, digest)
                expires = metadata.get("retention_expires_at")
                if expires is None:
                    continue
                expiry = datetime.fromisoformat(expires).astimezone(timezone.utc)
                if expiry <= current:
                    refs.append(self.reference(payload_owner, digest))
        return tuple(sorted(refs))

    def enforce_retention(
        self,
        referenced: set[tuple[int, str]],
        *,
        now: datetime | None = None,
        dry_run: bool = True,
        minimum_age: timedelta = _DEFAULT_CLEANUP_GRACE,
    ) -> VaultReport:
        if minimum_age < timedelta(0):
            raise ValueError("minimum_age must not be negative")
        referenced_refs = {
            self.reference(owner_id, digest) for owner_id, digest in referenced
        }
        current = now or datetime.now(timezone.utc)
        cutoff = current.timestamp() - minimum_age.total_seconds()
        removed: list[str] = []
        protected: list[str] = []
        eligible: list[str] = []
        with self._exclusive_lock():
            expired = self.retention_scan(now=current)
            for reference in expired:
                parts = reference.split("/")
                owner_id = int(parts[1])
                digest = parts[3]
                path = self._path(owner_id, digest)
                if reference in referenced_refs or path.stat().st_mtime > cutoff:
                    protected.append(reference)
                    continue
                eligible.append(reference)
                if not dry_run and self._delete_locked(owner_id, digest, dry_run=False):
                    removed.append(reference)
        return VaultReport(
            orphaned_refs=tuple(eligible),
            removed_refs=tuple(removed),
            protected_refs=tuple(protected),
        )

    def reencrypt(self, owner_id: int, digest: str, *, key_id: str | None = None) -> str:
        owner_id = self._owner(owner_id)
        digest = self._digest(digest)
        path = self._path(owner_id, digest)
        with self._exclusive_lock():
            plaintext = self.read(owner_id, digest)
            metadata = self._read_metadata(owner_id, digest)
            ciphertext, active_key_id = self._encrypt(owner_id, digest, plaintext, key_id)
            previous_blob = path.read_bytes()
            self._write_blob_atomic(path, ciphertext)
            try:
                self._verify(path, digest)
                if metadata:
                    expiry = metadata.get("retention_expires_at")
                    self._write_metadata(
                        owner_id,
                        digest,
                        retention_expires_at=(
                            datetime.fromisoformat(expiry) if expiry is not None else None
                        ),
                    )
            except Exception:
                self._write_blob_atomic(path, previous_blob)
                raise
            return active_key_id

    def reconcile(
        self, referenced: set[tuple[int, str]]
    ) -> VaultReport:
        self._ensure_directory(self.root)
        referenced_refs = {
            self.reference(owner_id, digest) for owner_id, digest in referenced
        }
        found: set[str] = set()
        orphaned: list[str] = []
        temporary_files: list[str] = []
        for current, directories, filenames in os.walk(self.root, followlinks=False):
            current_path = Path(current)
            self._assert_safe_path(current_path)
            safe_directories = []
            for name in directories:
                path = current_path / name
                self._assert_safe_path(path)
                safe_directories.append(name)
            directories[:] = safe_directories
            for name in filenames:
                path = current_path / name
                self._assert_safe_path(path)
                relative = path.relative_to(self.root).as_posix()
                if name == ".pids-vault.lock" or name.endswith(".meta.json"):
                    continue
                if name.startswith(".pids-"):
                    temporary_files.append(relative)
                    continue
                parts = relative.split("/")
                if (
                    len(parts) != 4
                    or parts[0] != "sha256"
                    or not parts[1].isdigit()
                    or not _SHA256.fullmatch(parts[3])
                    or parts[2] != parts[3][:2]
                ):
                    continue
                found.add(relative)
                if relative not in referenced_refs:
                    orphaned.append(relative)
        missing = tuple(sorted(referenced_refs - found))
        return VaultReport(
            orphaned_refs=tuple(sorted(orphaned)),
            missing_refs=missing,
            temporary_files=tuple(sorted(temporary_files)),
        )

    def cleanup_orphans(
        self,
        referenced: set[tuple[int, str]],
        *,
        dry_run: bool = True,
        minimum_age: timedelta = _DEFAULT_CLEANUP_GRACE,
    ) -> VaultReport:
        if minimum_age < timedelta(0):
            raise ValueError("minimum_age must not be negative")
        with self._exclusive_lock():
            report = self.reconcile(referenced)
            if dry_run:
                return report
            cutoff = datetime.now().timestamp() - minimum_age.total_seconds()
            removed: list[str] = []
            removed_temporary: list[str] = []
            for reference in report.orphaned_refs:
                parts = reference.split("/")
                if len(parts) != 4:
                    continue
                owner_id = int(parts[1])
                digest = parts[3]
                path = self._path(owner_id, digest)
                meta_path = self._meta_path(owner_id, digest)
                self._assert_safe_path(path)
                try:
                    if path.stat().st_mtime > cutoff:
                        continue
                    path.unlink()
                    if meta_path.exists():
                        meta_path.unlink()
                    removed.append(reference)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise VaultError("orphan blob cleanup failed") from exc
            for relative in report.temporary_files:
                path = self.root / relative
                self._assert_safe_path(path)
                try:
                    if path.stat().st_mtime > cutoff:
                        continue
                    path.unlink()
                    removed_temporary.append(relative)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise VaultError("temporary raw payload cleanup failed") from exc
            return VaultReport(
                orphaned_refs=report.orphaned_refs,
                missing_refs=report.missing_refs,
                removed_refs=tuple(removed),
                temporary_files=tuple(removed_temporary),
            )

    def _verify(self, path: Path, expected_digest: str) -> None:
        self._assert_safe_path(path)
        try:
            details = path.lstat()
            if not stat.S_ISREG(details.st_mode):
                raise VaultIntegrityError("raw payload is not a regular file")
            if details.st_size > MAX_PAYLOAD_BYTES + 1024:
                raise VaultIntegrityError("raw payload exceeds the byte limit")
            with path.open("rb") as stream:
                payload = stream.read(MAX_PAYLOAD_BYTES + 2048)
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError("raw payload integrity check failed") from exc

        owner_id = int(str(path.parent.parent.name))
        if owner_id <= 0:
            raise VaultIntegrityError("raw payload owner metadata is invalid")
        plaintext = self._decrypt_if_needed(owner_id, expected_digest, payload)
        if hashlib.sha256(plaintext).hexdigest() != expected_digest:
            raise VaultIntegrityError("raw payload integrity check failed")

    def require_safe_migration(self, database_url: str) -> str:
        """Allow isolated PIDS migrations only when the database remains inside pids-isolated-work."""
        return validate_isolated_database_url(database_url)


class WindowsDPAPIVaultKeyManager(VaultKeyManager):
    """Protect the local vault key ring with the current Windows user's DPAPI."""

    production_safe = True
    _magic = b"NEXUS-DPAPI-KEYS-V1\0"

    @staticmethod
    def _crypt(data: bytes, *, protect: bool) -> bytes:
        if os.name != "nt":
            raise VaultKeyError("Windows DPAPI is available only on Windows")
        import ctypes
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        buffer = ctypes.create_string_buffer(data)
        input_blob = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        output_blob = DataBlob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if protect:
            function = crypt32.CryptProtectData
            function.argtypes = [ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.POINTER(DataBlob),
                                 wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(DataBlob)]
            function.restype = wintypes.BOOL
            succeeded = function(ctypes.byref(input_blob), "Nexus AI local PIDS key ring", None, None, None,
                                 0x1, ctypes.byref(output_blob))
        else:
            function = crypt32.CryptUnprotectData
            function.argtypes = [ctypes.POINTER(DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(DataBlob),
                                 wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(DataBlob)]
            function.restype = wintypes.BOOL
            succeeded = function(ctypes.byref(input_blob), None, None, None, None, 0x1, ctypes.byref(output_blob))
        if not succeeded:
            raise VaultKeyError("Windows could not protect or unlock the local vault keys")
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
            kernel32.LocalFree.restype = wintypes.HLOCAL
            kernel32.LocalFree(output_blob.pbData)

    def __init__(self, path: Path | str | None = None):
        super().__init__(path)
        if self.path.exists() and not self.path.read_bytes().startswith(self._magic):
            # Upgrade the legacy local JSON key ring without changing its key IDs/material.
            with self._exclusive_key_lock():
                self._state = self._load()
                self._save()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"keys": {}, "active_key_id": None}
        try:
            stored = self.path.read_bytes()
            if stored.startswith(self._magic):
                payload = json.loads(self._crypt(stored[len(self._magic):], protect=False).decode("utf-8"))
            else:
                # Read the prior local format once so the current vault key stays usable.
                payload = json.loads(stored.decode("utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise VaultKeyError("vault key metadata is unreadable") from exc
        return self._validate_state(payload)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            encoded = json.dumps(self._state, indent=2, sort_keys=True).encode("utf-8")
            protected = self._magic + self._crypt(encoded, protect=True)
            descriptor, temp_name = tempfile.mkstemp(prefix=".pids-keys-", dir=self.path.parent)
            temp_path = Path(temp_name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(protected)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError as exc:
            raise VaultKeyError("vault key metadata could not be saved") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
