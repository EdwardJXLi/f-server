from __future__ import annotations

import subprocess
from pathlib import Path

import typer
from sqlalchemy import select

from f_server.auth.api_keys import create_api_key
from f_server.db import SessionLocal, create_all
from f_server.models import ApiKey
from f_server.services.rebuild import rebuild_repo

app = typer.Typer(help="Manage f-server")
keys_app = typer.Typer(help="Manage upload API keys")
db_app = typer.Typer(help="Manage the database")
app.add_typer(keys_app, name="keys")
app.add_typer(db_app, name="db")


@keys_app.command("create")
def keys_create(
    label: str = typer.Option(..., "--label", "-l"),
    scope: list[str] = typer.Option(..., "--scope", "-s"),
) -> None:
    create_all()
    with SessionLocal() as session:
        created = create_api_key(session, label=label, scopes=list(scope), created_by="cli")
        typer.echo(created.secret)


@keys_app.command("list")
def keys_list() -> None:
    create_all()
    with SessionLocal() as session:
        rows = session.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
        for row in rows:
            status = "revoked" if row.revoked else "active"
            typer.echo(f"{row.id}\t{row.label}\t{status}\t{','.join(row.allowed_package_globs)}")


@keys_app.command("revoke")
def keys_revoke(key_id: int) -> None:
    create_all()
    with SessionLocal() as session:
        row = session.get(ApiKey, key_id)
        if row is None:
            raise typer.BadParameter("API key not found")
        row.revoked = True
        session.commit()
        typer.echo(f"revoked {key_id}")


@app.command("rebuild")
def rebuild() -> None:
    create_all()
    with SessionLocal() as session:
        for key in rebuild_repo(session):
            typer.echo(key)
        session.commit()


@app.command("init")
def init(
    keystore: Path = typer.Option(Path("./repo-keystore.p12"), "--keystore"),
    alias: str = typer.Option("f-server", "--alias"),
    password: str | None = typer.Option(None, "--password", help="Keystore and key password"),
) -> None:
    create_all()
    if keystore.exists():
        typer.echo(f"{keystore} already exists")
        _print_fingerprint(keystore, alias, password)
        return
    if password is None:
        password = typer.prompt("Keystore password", hide_input=True, confirmation_prompt=True)
    keystore.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "keytool",
        "-genkeypair",
        "-storetype",
        "PKCS12",
        "-keystore",
        str(keystore),
        "-storepass",
        password,
        "-keypass",
        password,
        "-alias",
        alias,
        "-keyalg",
        "RSA",
        "-keysize",
        "4096",
        "-validity",
        "10000",
        "-dname",
        "CN=f-server,O=f-server",
    ]
    subprocess.run(cmd, check=True)
    typer.echo(f"created {keystore}")
    _print_fingerprint(keystore, alias, password)
    typer.echo(f"FS_REPO__KEYSTORE_PATH={keystore}")
    typer.echo(f"FS_REPO__KEY_ALIAS={alias}")


@db_app.command("upgrade")
def db_upgrade() -> None:
    subprocess.run(["alembic", "upgrade", "head"], check=True)


def _print_fingerprint(keystore: Path, alias: str, password: str | None) -> None:
    if password is None:
        return
    result = subprocess.run(
        [
            "keytool",
            "-list",
            "-v",
            "-keystore",
            str(keystore),
            "-storepass",
            password,
            "-alias",
            alias,
        ],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    for line in result.stdout.splitlines():
        if "SHA256:" in line:
            typer.echo(line.strip())
            break
