from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from f_server.auth.admin import oidc_client, oidc_enabled, require_admin
from f_server.auth.api_keys import create_api_key, hash_secret
from f_server.config import get_settings
from f_server.db import get_session
from f_server.fdroid.sign import SigningConfigurationError
from f_server.models import AllowedSigningKey, ApiKey, App, AuditLog, RegistrySettings, Version
from f_server.services.rebuild import rebuild_repo
from f_server.storage import get_storage

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="f_server/templates")
templates.env.globals["repo_name"] = get_settings().repo.name


@router.get("", response_class=HTMLResponse)
def dashboard(
    request: Request,
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(request, "admin/index.html", _dashboard_context(session))


@router.get("/keys", response_class=HTMLResponse)
def api_keys(
    request: Request,
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(request, "admin/keys.html", _api_keys_context(session))


@router.get("/apps/{package_name}", response_class=HTMLResponse)
def app_detail(
    package_name: str,
    request: Request,
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    app = session.scalar(
        select(App)
        .where(App.package_name == package_name)
        .options(selectinload(App.versions), selectinload(App.signing_keys), selectinload(App.assets))
    )
    if app is None:
        raise HTTPException(status_code=404, detail="app not found")
    return templates.TemplateResponse(request, "admin/app.html", {"app": app})


@router.post("/apps/{package_name}/metadata")
def update_app_metadata(
    package_name: str,
    name: str = Form(""),
    summary: str = Form(""),
    description: str = Form(""),
    categories: str = Form(""),
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    app = session.get(App, package_name)
    if app is None:
        raise HTTPException(status_code=404, detail="app not found")
    app.name = name or None
    app.summary = summary or None
    app.description = description or None
    app.categories = [item.strip() for item in categories.split(",") if item.strip()]
    session.commit()
    return RedirectResponse(f"/admin/apps/{package_name}", status_code=303)


@router.post("/apps/{package_name}/signing-keys")
def add_signing_key(
    package_name: str,
    fingerprint: str = Form(...),
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    app = session.get(App, package_name)
    if app is None:
        raise HTTPException(status_code=404, detail="app not found")
    session.add(
        AllowedSigningKey(
            package_name=package_name,
            sha256_fingerprint=fingerprint.lower().replace(":", ""),
            added_by=admin,
        )
    )
    session.commit()
    return RedirectResponse(f"/admin/apps/{package_name}", status_code=303)


@router.post("/versions/{version_id}/delete")
def delete_version(
    version_id: int,
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    version = session.get(Version, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    package_name = version.package_name
    _delete_storage_keys([version.storage_key])
    session.delete(version)
    session.commit()
    rebuild_repo(session)
    session.commit()
    return RedirectResponse(f"/admin/apps/{package_name}", status_code=303)


@router.post("/apps/{package_name}/delete")
def delete_app(
    package_name: str,
    confirm_package_name: str = Form(""),
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if confirm_package_name != package_name:
        raise HTTPException(status_code=400, detail="package name confirmation did not match")
    app = session.scalar(
        select(App)
        .where(App.package_name == package_name)
        .options(selectinload(App.versions), selectinload(App.assets))
    )
    if app is None:
        raise HTTPException(status_code=404, detail="app not found")
    _delete_storage_keys([version.storage_key for version in app.versions])
    _delete_storage_keys([asset.storage_key for asset in app.assets])
    session.delete(app)
    session.commit()
    rebuild_repo(session)
    session.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/keys")
def create_key(
    request: Request,
    label: str = Form(...),
    scope: str = Form(...),
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    scopes = [item.strip() for item in scope.split(",") if item.strip()]
    created = create_api_key(session, label, scopes, created_by=admin)
    return templates.TemplateResponse(
        request,
        "admin/keys.html",
        {**_api_keys_context(session), "new_secret": created.secret},
    )


@router.post("/keys/{key_id}/revoke")
def revoke_key(
    key_id: int,
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    api_key = session.get(ApiKey, key_id)
    if api_key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    api_key.revoked = True
    session.commit()
    return RedirectResponse("/admin/keys", status_code=303)


@router.post("/rebuild")
def rebuild(
    request: Request,
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    try:
        rebuild_repo(session)
        session.commit()
    except SigningConfigurationError as exc:
        return templates.TemplateResponse(
            request,
            "admin/index.html",
            {**_dashboard_context(session), "error": str(exc)},
            status_code=400,
        )
    return RedirectResponse("/admin", status_code=303)


@router.get("/registry", response_class=HTMLResponse)
def registry_access(
    request: Request,
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(request, "admin/registry.html", _registry_context(session))


@router.post("/registry/lock")
def lock_registry(
    request: Request,
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    registry_settings = session.get(RegistrySettings, 1)
    if not registry_settings or not registry_settings.hashed_password:
        return templates.TemplateResponse(
            request,
            "admin/registry.html",
            {
                **_registry_context(session),
                "error": "Registry password is not configured.",
                "error_title": "Registry lock failed",
            },
            status_code=400,
        )
    _set_registry_locked(session, True, admin)
    return RedirectResponse("/admin/registry", status_code=303)


@router.post("/registry/unlock")
def unlock_registry(
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    _set_registry_locked(session, False, admin)
    return RedirectResponse("/admin/registry", status_code=303)


@router.post("/registry/credentials")
def update_registry_credentials(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if not password:
        return templates.TemplateResponse(
            request,
            "admin/registry.html",
            {
                **_registry_context(session),
                "error": "Registry password cannot be empty.",
                "error_title": "Registry password failed",
            },
            status_code=400,
        )
    registry_settings = _registry_settings(session)
    registry_settings.username = username.strip() or "fdroid"
    registry_settings.hashed_password = hash_secret(password)
    session.add(
        AuditLog(
            actor=admin,
            action="registry.credentials",
            result="ok",
        )
    )
    session.commit()
    return RedirectResponse("/admin/registry", status_code=303)


@router.get("/audit", response_class=HTMLResponse)
def audit(
    request: Request,
    admin: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    rows = session.scalars(select(AuditLog).order_by(AuditLog.ts.desc()).limit(200)).all()
    return templates.TemplateResponse(request, "admin/audit.html", {"rows": rows})


@router.get("/login")
async def login(request: Request):
    if not oidc_enabled():
        return RedirectResponse("/admin", status_code=303)
    client = oidc_client()
    redirect_uri = get_settings().admin_auth.redirect_url or str(request.url_for("oidc_callback"))
    return await client.authorize_redirect(request, redirect_uri, code_challenge_method="S256")


@router.get("/callback", name="oidc_callback")
async def oidc_callback(request: Request):
    client = oidc_client()
    token = await client.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = await client.userinfo(token=token)
    subject = userinfo.get("sub") or userinfo.get("email")
    if not subject:
        raise HTTPException(status_code=401, detail="OIDC subject missing")
    request.session["admin_user"] = subject
    return RedirectResponse("/admin", status_code=303)


@router.post("/logout")
def logout(request: Request, admin: str = Depends(require_admin)):
    request.session.clear()
    return RedirectResponse("/admin", status_code=303)


def _dashboard_context(session: Session) -> dict:
    apps = session.scalars(
        select(App).options(selectinload(App.versions), selectinload(App.signing_keys)).order_by(App.package_name)
    ).all()
    return {
        "apps": apps,
    }


def _api_keys_context(session: Session) -> dict:
    keys = session.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
    return {
        "keys": keys,
    }


def _registry_context(session: Session) -> dict:
    registry_settings = session.get(RegistrySettings, 1)
    return {
        "registry_locked": bool(registry_settings and registry_settings.downloads_locked),
        "registry_auth_configured": bool(registry_settings and registry_settings.hashed_password),
        "registry_username": (registry_settings.username if registry_settings else None) or "fdroid",
    }


def _set_registry_locked(session: Session, locked: bool, admin: str) -> None:
    registry_settings = _registry_settings(session)
    registry_settings.downloads_locked = locked
    session.add(
        AuditLog(
            actor=admin,
            action="registry.lock" if locked else "registry.unlock",
            result="ok",
        )
    )
    session.commit()


def _registry_settings(session: Session) -> RegistrySettings:
    registry_settings = session.get(RegistrySettings, 1)
    if registry_settings is None:
        registry_settings = RegistrySettings(id=1, username="fdroid")
        session.add(registry_settings)
    return registry_settings


def _delete_storage_keys(keys: list[str]) -> None:
    storage = get_storage()
    for key in sorted(set(keys)):
        storage.delete(key)
