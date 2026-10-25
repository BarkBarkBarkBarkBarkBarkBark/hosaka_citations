"""Deliberate operator setup tools; never invoked by discovery or installation."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import tempfile
from uuid import UUID
import zipfile

from citations.config import load_config
from citations.database import Database, encoded
from citations.models import initial_state
from citations.supabase import Supabase, SyncError
from citations.validation import DomainError

EXTENSION_ID = "halloween-citations"


class OperatorContext:
    """Explicit standalone operator context; not a sandbox or host approval."""
    def __init__(self, root: Path, network=False):
        self.root = root.resolve()
        if self.root.is_relative_to(Path(__file__).resolve().parents[1]):
            raise DomainError("Runtime data must be outside extension source; use the canonical Hosaka data resolver.")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.network = network

    def data_path(self, relative: str) -> str:
        path = Path(relative)
        target = self.root / path
        if not relative or path.is_absolute() or "\\" in relative or any(part in {"", ".", ".."} for part in relative.split("/")):
            raise DomainError("Use a relative extension data path.")
        if not target.resolve().is_relative_to(self.root):
            raise DomainError("Data path escapes this extension.")
        current = target
        while current != self.root:
            if current.is_symlink():
                raise DomainError("Symlinked data paths are not permitted.")
            current = current.parent
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return str(target)

    def read_data(self, name):
        path = Path(self.data_path(name))
        return path.read_text(encoding="utf-8") if path.exists() else None

    def write_data(self, name, text):
        target = Path(self.data_path(name))
        fd, temporary = tempfile.mkstemp(prefix=".config-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def require_network(self):
        if not self.network:
            raise DomainError("The standalone login command requires --allow-network.")

    def delete_data(self, name):
        Path(self.data_path(name)).unlink(missing_ok=True)

    def log(self, event, metadata):
        pass  # The standalone command reports its result to the operator, not host logs.


def resolve_data(override: Path | None) -> Path:
    if override:
        return override.resolve()
    result = subprocess.run(["hosaka", "paths", "--json"], capture_output=True, text=True, check=True, timeout=15)
    return Path(json.loads(result.stdout)["paths"]["extension_data"]) / EXTENSION_ID


def psql(sql: str, variables: dict | None = None, *, atomic=True) -> str:
    command = ["psql", "-X", "-q", "-A", "-t", "--set", "ON_ERROR_STOP=1"]
    if atomic:
        command.append("--single-transaction")
    for key, value in (variables or {}).items():
        command.extend(["--set", f"{key}={value}"])
    result = subprocess.run(command, input=sql, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise DomainError(f"PostgreSQL setup failed (psql exit {result.returncode}); transaction rolled back. Inspect the named SQL migration with psql directly.")
    return result.stdout.strip()


def remote_migrate(workspace: str, member: str, name: str):
    workspace, member = str(UUID(workspace)), str(UUID(member))
    migrations = Path(__file__).parent / "migrations"
    try:
        psql((migrations / "postgres_bootstrap.sql").read_text(encoding="utf-8"))
    except DomainError as exc:
        raise DomainError(f"Schema/role bootstrap: {exc}") from exc
    applied = {int(line) for line in psql("SELECT version FROM hosaka_citations.hc_schema_migrations ORDER BY version;").splitlines() if line}
    for migration in sorted((migrations / "postgres").glob("*.sql")):
        version = int(migration.name.split("_")[0])
        if version not in applied:
            try:
                psql(migration.read_text(encoding="utf-8") + f"\nINSERT INTO hosaka_citations.hc_schema_migrations VALUES({version});")
            except DomainError as exc:
                raise DomainError(f"Migration {migration.name}: {exc}") from exc
    # Membership has no foreign key into Supabase Auth (the owner role cannot reference it).
    if psql("SELECT count(*) FROM auth.users WHERE id=:'member'::uuid;", {"member": member}) != "1":
        raise DomainError("The member must be an existing Supabase Auth user UUID.")
    psql(
        "INSERT INTO hosaka_citations.hc_workspaces VALUES(:'workspace'::uuid,:'name') ON CONFLICT DO NOTHING;\n"
        "INSERT INTO hosaka_citations.hc_members VALUES(:'workspace'::uuid,:'member'::uuid) ON CONFLICT DO NOTHING;",
        {"workspace": workspace, "member": member, "name": name},
    )
    psql("NOTIFY pgrst, 'reload schema';")


def backup(context, destination: Path):
    if destination.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise DomainError("Backups belong outside extension source.")
    database = Database(context)
    try:
        with tempfile.TemporaryDirectory() as directory:
            import sqlite3
            target = Path(directory) / "database.sqlite3"
            connection = sqlite3.connect(target)
            try:
                database.connection.backup(connection)
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise DomainError("Backup database integrity check failed.")
            finally:
                connection.close()
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as stream, zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(target, "database.sqlite3")
                archive.writestr("citations-config.json", encoded(load_config(context)))
                for attachment in database.rows("attachments"):
                    path = Path(context.data_path("files/" + attachment["storage_key"]))
                    if path.exists():
                        archive.write(path, "files/" + path.name)
    finally:
        database.close()


def main():
    parser = argparse.ArgumentParser(description="Operator-owned citation setup; inspect source before execution.")
    parser.add_argument("--data-dir", type=Path, help="Explicit data root for tests/restore; default comes from hosaka paths")
    parser.add_argument("--allow-network", action="store_true", help="Explicit standalone user-authentication consent")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    seed = commands.add_parser("seed")
    seed.add_argument("--demo", action="store_true", help="Explicitly add fictional demonstration data")
    configure = commands.add_parser("configure")
    configure.add_argument("--project-url")
    configure.add_argument("--publishable-key")
    configure.add_argument("--workspace")
    configure.add_argument("--interval", type=int)
    configure.add_argument("--database")
    enabled = configure.add_mutually_exclusive_group()
    enabled.add_argument("--enable-sync", dest="sync_enabled", action="store_const", const=True, default=None)
    enabled.add_argument("--disable-sync", dest="sync_enabled", action="store_const", const=False)
    login = commands.add_parser("login")
    login.add_argument("--email", required=True)
    remote = commands.add_parser("remote-migrate")
    remote.add_argument("--workspace", required=True)
    remote.add_argument("--member", required=True, help="Existing Supabase Auth user UUID")
    remote.add_argument("--name", default="Halloween")
    seed_remote = commands.add_parser("remote-seed")
    seed_remote.add_argument("--workspace", required=True)
    save = commands.add_parser("backup")
    save.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "remote-migrate":
            remote_migrate(args.workspace, args.member, args.name)
        elif args.command == "remote-seed":
            rows = [{**row, "workspace_id": str(UUID(args.workspace)), "sync_revision": 0, "deleted_at": None}
                    for row in initial_state()["violation_types"]]
            psql((Path(__file__).parent / "seeds/postgres.sql").read_text(), {"defaults": encoded(rows)})
        else:
            context = OperatorContext(resolve_data(args.data_dir), args.allow_network)
            if args.command == "configure":
                config = load_config(context, validate_remote=False)
                changes = {"database": args.database, "supabase_url": args.project_url,
                           "publishable_key": args.publishable_key, "workspace_id": args.workspace,
                           "sync_interval_seconds": args.interval, "sync_enabled": args.sync_enabled}
                config.update({key: value for key, value in changes.items() if value is not None})
                location = context.read_data("database-location.json")
                if location is not None and json.loads(location) != config["database"]:
                    raise DomainError("Changing the SQLite path requires an explicit verified database move.")
                previous = context.read_data("citations-config.json")
                context.write_data("citations-config.json", encoded(config))
                try:
                    load_config(context)
                except DomainError:
                    if previous is not None:
                        context.write_data("citations-config.json", previous)
                    else:
                        Path(context.data_path("citations-config.json")).unlink()
                    raise
            elif args.command == "login":
                Supabase(context, load_config(context)).login(args.email, getpass.getpass("Supabase user password: "))
            elif args.command == "backup":
                backup(context, args.destination)
            elif args.command == "seed" and args.demo:
                from citations.repository import Repository
                from citations.service import Service
                repository = Repository(context)
                try:
                    if repository.database.meta("demo_seeded") is None:
                        Service(repository).issue_for_attendee({"display_name": "Demo Count Byte", "violation": "no-costume"})
                        repository.commit(metadata={"demo_seeded": "1"})
                finally:
                    repository.close()
            else:
                database = Database(context)
                if args.command == "seed":
                    from citations.seeds import seed_defaults
                    with database.transaction():
                        seed_defaults(database)
                database.close()
        print("Completed. Application data, credentials and SQLite remain outside source.")
    except sqlite3.Error:
        parser.exit(1, "SQLite setup failed; inspect the migration/database and protected backups. No reset was performed.\n")
    except (DomainError, SyncError, OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
