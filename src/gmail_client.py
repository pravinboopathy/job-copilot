"""Gmail API client for fetching LinkedIn job alert emails."""

import base64
import logging
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


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
        query: str = "from:jobs-noreply@linkedin.com newer_than:1d",
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
