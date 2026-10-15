"""Drive metadata, honest quota accounting and individually reviewed trash."""
from collections import defaultdict
import time
from . import common, google_auth

FIELDS = "id,name,mimeType,size,quotaBytesUsed,modifiedTime,viewedByMeTime,owners(displayName,emailAddress,me),parents,webViewLink,md5Checksum,sha256Checksum,capabilities(canTrash),trashed"


def analyze(p):
    api = google_auth.service("drive")
    quota = api.about().get(fields="storageQuota,user(displayName)").execute()
    files, token = [], None
    deadline = time.monotonic() + 40
    for _ in range(10):
        result = api.files().list(q="trashed = false", pageSize=100, pageToken=token,
                                 fields="nextPageToken,files(" + FIELDS + ")",
                                 orderBy="quotaBytesUsed desc").execute()
        files.extend(result.get("files", []))
        token = result.get("nextPageToken")
        if not token or time.monotonic() >= deadline:
            break
    groups, breakdown = defaultdict(list), defaultdict(int)
    for f in files:
        f["size_bytes"] = int(f.get("size", 0))
        f["quota_bytes"] = int(f.get("quotaBytesUsed", 0))
        f["owner"] = ", ".join(o.get("displayName", "") for o in f.get("owners", []))
        breakdown[f["mimeType"]] += f["quota_bytes"]
        digest = f.get("sha256Checksum") or f.get("md5Checksum")
        if digest and f["size_bytes"] > 0:
            groups[(digest, f["size_bytes"])].append(f["id"])
    duplicates = [group for group in groups.values() if len(group) > 1]
    q = {k: int(v) for k, v in quota.get("storageQuota", {}).items()}
    remaining = max(0, q["limit"] - q.get("usage", 0)) if q.get("limit") else None
    return {"success": True, "verified": True, "files": files, "quota": q, "remaining_bytes": remaining,
            "breakdown": dict(breakdown), "duplicate_candidates": duplicates, "partial": bool(token),
            "next_page_token": token, "fetched_at": common.now(),
            "note": "usage is shared Google account storage; usageInDrive includes Drive trash. Breakdown covers only the listed files and may include shared-owned files. Same-name files are not assumed duplicates."}


def trash_preview(file_ids):
    api = google_auth.service("drive")
    rows = [api.files().get(fileId=i, fields=FIELDS).execute() for i in common.ids(file_ids)]
    if any(not r.get("capabilities", {}).get("canTrash") for r in rows):
        raise PermissionError("One or more selected files cannot be moved to trash by this account.")
    detail = "\n".join(f"{r['name']} | {r.get('size', 'unknown')} bytes | {r['mimeType']} | ID {r['id']}" for r in rows)
    common.publish("drive", {"success": True, "status": "review_required", "selected_files": rows})
    from core import confirm
    def perform():
        # Permissions are checked again after the user approves. Never DELETE.
        writer = google_auth.service("drive", write=True)
        results = []
        for row in rows:
            try:
                changed = writer.files().update(fileId=row["id"], body={"trashed": True},
                                                 fields="id,trashed").execute()
                results.append({"id": row["id"], "trashed": changed.get("trashed") is True})
            except Exception:
                results.append({"id": row["id"], "trashed": False, "error": "Provider rejected this change"})
        refreshed = analyze({})
        refreshed["changes"] = results
        common.publish("drive", refreshed)
        return f"Trash review completed: {sum(r['trashed'] for r in results)}/{len(results)} files moved. No files permanently deleted."
    return confirm.request("drive_trash", "Move selected Drive files to trash?", detail, perform)


def handle(p):
    if p.get("action", "analyze") == "trash":
        return trash_preview(p.get("file_ids"))
    return analyze(p)
