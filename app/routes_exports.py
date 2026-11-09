"""Downloads (CSV / JSON of every list) and full backup / restore (owner only)."""
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import audit, backup, exports

OWNER_ROLES = ("owner", "admin")


def require_owner(request: Request) -> None:
    """Backups, restores and the suppression list are for the owner only.

    Today there is one admin login, already checked by the login middleware, so every request that reaches here passes.
    When users and roles arrive, the auth layer sets `request.state.role` (or `request.state.user` with a `role`) and
    this one check starts refusing everyone but the owner / admin.
    """
    role = getattr(request.state, "role", None)
    user = getattr(request.state, "user", None)
    if role is None and user is not None:
        role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
    if role is not None and str(role).lower() not in OWNER_ROLES:
        raise HTTPException(403, "Only the owner can do this.")


def require_super_admin(request: Request, what: str) -> None:
    """A backup with secrets holds every key and password hash, and a restore replaces all data: super admin only
    (OUTREACH_SUPER_ADMIN, or the server admin password; with no super admin set, any owner, as auth.is_super_admin)."""
    from . import auth
    user = getattr(request.state, "user", None)
    if user is not None and not auth.is_super_admin(user):
        raise HTTPException(403, f"Only the super admin can {what}.")


def _attachment(name: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"}


def make_router(store, present) -> APIRouter:
    router = APIRouter()

    def rows(dataset: str, params: dict, request: Request, kind: str = "csv") -> tuple:
        if dataset in exports.OWNER_ONLY:
            require_owner(request)
        try:
            columns, found = exports.build(store, dataset, params, present)
        except exports.ExportError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        # Every download of personal data is written to the Audit tab: who, which list, how many rows, which filters.
        filters = ", ".join(f"{k}={str(v)[:40]}" for k, v in params.items() if v and k != "ids")
        picked = f"{len(params['ids'])} picked" if isinstance(params.get("ids"), list) else ""
        audit.record(store, getattr(request.state, "user", None), "export.download", f"{dataset}.{kind}",
                     "; ".join(x for x in (f"{len(found)} rows", picked, filters) if x))
        return columns, found

    def as_csv(dataset: str, params: dict, request: Request) -> StreamingResponse:
        columns, found = rows(dataset, params, request)
        return StreamingResponse(exports.csv_chunks(columns, found), media_type="text/csv; charset=utf-8",
                                 headers=_attachment(exports.filename(dataset, params, "csv")))

    @router.get("/api/export")
    def catalogue():
        return exports.catalogue()

    @router.get("/api/export/{dataset}.csv")
    def export_csv(dataset: str, request: Request):
        return as_csv(dataset, dict(request.query_params), request)

    @router.post("/api/export/{dataset}.csv")
    def export_csv_post(dataset: str, request: Request, body: Optional[dict] = Body(default=None)):
        """Same as the GET, for filters too long for an address (e.g. hundreds of picked lead ids)."""
        return as_csv(dataset, {**dict(request.query_params), **(body or {})}, request)

    @router.get("/api/export/{dataset}.json")
    def export_json(dataset: str, request: Request):
        params = dict(request.query_params)
        columns, found = rows(dataset, params, request, "json")
        content = {"dataset": dataset, "generated_at": exports.now_utc().isoformat(timespec="seconds"),
                   "filters": {k: v for k, v in params.items() if v}, "columns": columns, "count": len(found), "rows": found}
        return JSONResponse(content, headers=_attachment(exports.filename(dataset, params, "json")))

    @router.get("/api/backup")
    def download_backup(request: Request, include_secrets: bool = False):
        require_owner(request)
        if include_secrets:
            require_super_admin(request, "download a backup with keys and secrets")
        now = backup.now_utc()
        audit.record(store, getattr(request.state, "user", None), "backup.download", "Full backup",
                     "with connection keys and secrets" if include_secrets else "without connection keys")
        payload = backup.build_backup(store, include_secrets, now)
        return Response(backup.to_bytes(payload), media_type="application/gzip",
                        headers=_attachment(backup.backup_filename(include_secrets, now)))

    @router.post("/api/backup/restore")
    async def restore_backup(request: Request, dry_run: Optional[bool] = None, confirm: bool = False, keep_secrets: bool = False):
        """Upload a backup (raw body, or multipart field 'file'). Dry run by default; ?confirm=1 replaces everything."""
        require_owner(request)
        require_super_admin(request, "restore a backup")
        declared = request.headers.get("content-length") or ""
        if declared.isdigit() and int(declared) > backup.MAX_UPLOAD_BYTES:
            raise HTTPException(413, "The file is too large to be an Outreach OS backup.")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > backup.MAX_UPLOAD_BYTES:
                raise HTTPException(413, "The file is too large to be an Outreach OS backup.")
            chunks.append(chunk)
        raw = b"".join(chunks)
        content_type = request.headers.get("content-type") or ""
        try:
            if content_type.lower().startswith("multipart/form-data"):
                raw = backup.file_from_multipart(raw, content_type)
            payload = await run_in_threadpool(backup.read_payload, raw)
            if confirm and dry_run is not True:
                result = await run_in_threadpool(backup.restore, store, payload, keep_secrets)
                audit.record(store, getattr(request.state, "user", None), "backup.restore", "Everything replaced from a backup",
                             f"backup made {payload.get('created_at_ist') or payload.get('created_at') or '?'}")     # after the restore, so it survives it
                return result
            return await run_in_threadpool(backup.summary, store, payload, keep_secrets)
        except backup.BackupError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    return router
