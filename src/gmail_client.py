"""Gmail API client for fetching and sending emails."""

import base64
import logging
from datetime import date
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


class GmailClient:
    """Fetch LinkedIn job alert emails via Gmail API."""

    def __init__(self, credentials_path: str, token_path: str) -> None:
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.service: Any = None

    def authenticate(self) -> None:
        """Authenticate with Gmail API.

        On first run, opens a browser for OAuth consent and saves the
        token for subsequent non-interactive use.
        """
        creds: Credentials | None = None

        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"Gmail credentials not found at {self.credentials_path}. "
                        "Download from Google Cloud Console → APIs & Services → Credentials."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                creds = flow.run_local_server(port=0)

            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(creds.to_json())

        self.service = build("gmail", "v1", credentials=creds)
        logger.info("Gmail authenticated successfully")

    def fetch_alert_emails(
        self,
        query: str = "{from:jobs-noreply@linkedin.com from:jobalerts-noreply@linkedin.com from:jobs-listings@linkedin.com} newer_than:1d",
        max_results: int = 20,
    ) -> list[dict[str, str]]:
        """Fetch emails matching query.

        Returns list of dicts with keys: id, subject, body_html.
        """
        if not self.service:
            self.authenticate()

        results = (
            self.service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )

        messages = results.get("messages", [])
        if not messages:
            logger.info("No emails found for query: %s", query)
            return []

        emails: list[dict[str, str]] = []
        for msg_ref in messages:
            msg = (
                self.service.users()
                .messages()
                .get(userId="me", id=msg_ref["id"], format="full")
                .execute()
            )

            subject = ""
            for header in msg.get("payload", {}).get("headers", []):
                if header["name"].lower() == "subject":
                    subject = header["value"]
                    break

            body_html = self._extract_html_body(msg.get("payload", {}))
            emails.append({
                "id": msg_ref["id"],
                "subject": subject,
                "body_html": body_html,
            })

        logger.info("Fetched %d emails", len(emails))
        return emails

    def _extract_html_body(self, payload: dict[str, Any]) -> str:
        """Extract HTML body from email payload, handling multipart messages."""
        mime_type = payload.get("mimeType", "")

        if mime_type == "text/html":
            data = payload.get("body", {}).get("data", "")
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        if mime_type.startswith("multipart/"):
            for part in payload.get("parts", []):
                html = self._extract_html_body(part)
                if html:
                    return html

        return ""

    def send_results_email(self, to: str, results: list[Any]) -> None:
        """Send an HTML email with tailoring results and attached PDFs.

        Args:
            to: Recipient email address
            results: List of TailorResult objects
        """
        if not self.service:
            self.authenticate()

        today = date.today().isoformat()
        subject = f"Job Tailor: {len(results)} resumes tailored — {today}"

        # Build HTML body
        rows = ""
        for r in results:
            rows += (
                f"<tr>"
                f"<td style='padding:8px;border:1px solid #ddd'>{r.job.title}</td>"
                f"<td style='padding:8px;border:1px solid #ddd'>{r.job.company}</td>"
                f"<td style='padding:8px;border:1px solid #ddd'>{r.job.location or 'N/A'}</td>"
                f"<td style='padding:8px;border:1px solid #ddd'>{r.pre_match:.0f}% → {r.post_match:.0f}%</td>"
                f"<td style='padding:8px;border:1px solid #ddd'>"
                f"<a href='{r.job.url}'>Apply</a></td>"
                f"</tr>"
            )

        html_body = f"""<html><body>
<h2>Job Tailor Results — {today}</h2>
<table style='border-collapse:collapse;width:100%'>
<tr style='background:#f2f2f2'>
<th style='padding:8px;border:1px solid #ddd;text-align:left'>Job</th>
<th style='padding:8px;border:1px solid #ddd;text-align:left'>Company</th>
<th style='padding:8px;border:1px solid #ddd;text-align:left'>Location</th>
<th style='padding:8px;border:1px solid #ddd;text-align:left'>Match</th>
<th style='padding:8px;border:1px solid #ddd;text-align:left'>Apply</th>
</tr>
{rows}
</table>
<p>PDFs attached. Click "Apply" links to open LinkedIn job pages.</p>
</body></html>"""

        # Build MIME message
        msg = MIMEMultipart()
        msg["to"] = to
        msg["subject"] = subject
        msg.attach(MIMEText(html_body, "html"))

        # Attach PDFs
        for r in results:
            if r.pdf_path:
                pdf_path = Path(r.pdf_path)
                if pdf_path.exists():
                    with open(pdf_path, "rb") as f:
                        attachment = MIMEApplication(f.read(), _subtype="pdf")
                    filename = f"{r.job.company}_{r.job.title}.pdf".replace(" ", "_")
                    attachment.add_header(
                        "Content-Disposition", "attachment", filename=filename
                    )
                    msg.attach(attachment)

        # Send
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        self.service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        logger.info("Results email sent to %s", to)
