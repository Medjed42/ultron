"""Shared Gmail service; sends always use Ultron desktop confirmation."""
from .google_auth import get_google_credentials
def get_gmail_service():
    from core.mail_service import service
    return service()
def send_email(to, subject, content):
    from plugins.ultron_mail import run
    return {"status":"review_required","message":run({"action":"send","to":to,"subject":subject,"body":content})}
