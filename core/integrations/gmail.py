"""Bulk review and safe, bounded attachment previews."""
import base64
import json
from core import mail_service as mail
from . import common


def bulk(action, message_ids):
    selected = common.ids(message_ids)
    reader = mail.service().users().messages()
    rows = [mail.summarize(reader.get(userId="me", id=i, format="metadata").execute()) for i in selected]
    common.publish("gmail", {"success": True, "status": "review_required", "selected_messages": rows})
    detail = "\n".join(f"{r['subject']} | {r['from']} | {r['date']} | ID {r['id']}" for r in rows)
    from core import confirm
    def perform():
        writer = mail.service(write=True).users().messages()
        results = []
        for row in rows:
            try:
                if action == "bulk_trash":
                    writer.trash(userId="me", id=row["id"]).execute()
                else:
                    writer.modify(userId="me", id=row["id"], body={"removeLabelIds":["INBOX"]}).execute()
                results.append({"id": row["id"], "success": True})
            except Exception:
                results.append({"id": row["id"], "success": False})
        common.publish("gmail", {"success": True, "status": "bulk_completed", "changes": results})
        return f"Mailbox changes: {sum(r['success'] for r in results)}/{len(results)}. No permanent deletion."
    return confirm.request("gmail_bulk", "Review selected email " + ("trash" if action == "bulk_trash" else "archive"), detail, perform)


def attachment(p):
    api = mail.service().users().messages()
    mid, aid = common.text(p.get("message_id"), "Message ID"), common.text(p.get("attachment_id"), "Attachment ID", 2000)
    message = api.get(userId="me", id=mid, format="full").execute()
    meta = next((a for a in mail.attachment_metadata(message.get("payload", {})) if a["attachment_id"] == aid), None)
    if not meta: raise ValueError("Attachment does not belong to this message.")
    if meta["size"] > 1_000_000: raise ValueError("Preview limit is 1 MB. Open larger attachments in Gmail.")
    if meta["mime_type"] not in {"text/plain", "image/png", "image/jpeg", "application/pdf"}:
        return {"success": True, "attachment": meta, "preview": "Preview unsupported; open in Gmail."}
    blob = api.attachments().get(userId="me", messageId=mid, id=aid).execute()
    encoded = blob.get("data", "")
    raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    if len(raw) > 1_000_000: raise ValueError("Preview exceeds 1 MB.")
    result = {"success": True, "attachment": meta}
    if meta["mime_type"] == "text/plain":
        result["preview"] = raw.decode("utf-8", errors="replace")[:20000]
    elif meta["mime_type"] == "application/pdf":
        result["preview"] = "PDF attachment available. Open it in Gmail's sandboxed viewer."
    else:
        result["_image_base64"] = base64.b64encode(raw).decode()
    return result
