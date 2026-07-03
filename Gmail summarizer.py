"""
Gmail filter + summarize with Claude Anuradha test branch 
-------------------------------------
1. Authenticates with Gmail (OAuth, runs once, then caches token.json)
2. Finds all emails from a specific sender address
3. Extracts the plain-text body of each
4. Sends them to Claude to produce a summary

Setup:
    pip install google-auth-oauthlib google-api-python-client anthropic
    export ANTHROPIC_API_KEY="your-key-here"
    Place your Google OAuth "credentials.json" in the same folder as this script.
"""

import os
import base64
import pickle
from pathlib import Path

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import anthropic

# ---- CONFIG ----
TARGET_EMAIL = "lachlan.parkar@deeca.vic.gov.au "   # <-- change this to the address you want to filter
MAX_EMAILS = 50                        # cap how many emails to pull, to control cost
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CLAUDE_MODEL = "claude-sonnet-5"


def get_gmail_service():
    """Handles OAuth login and returns an authenticated Gmail API client."""
    creds = None
    token_path = Path("token.pickle")

    if token_path.exists():
        with open(token_path, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "wb") as f:
            pickle.dump(creds, f)

    return build("gmail", "v1", credentials=creds)


def get_message_body(payload):
    """Recursively extracts plain-text body from a Gmail message payload."""
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        data = payload["body"]["data"]
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    if "parts" in payload:
        for part in payload["parts"]:
            text = get_message_body(part)
            if text:
                return text
    return ""


def fetch_emails_from_sender(service, sender_email, max_results=50):
    """Returns a list of {subject, date, body} dicts for emails from sender_email."""
    query = f"from:{sender_email}"
    results = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()

    messages = results.get("messages", [])
    emails = []

    for msg_meta in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_meta["id"], format="full"
        ).execute()

        headers = msg["payload"].get("headers", [])
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "(no subject)")
        date = next((h["value"] for h in headers if h["name"] == "Date"), "")

        body = get_message_body(msg["payload"])
        emails.append({"subject": subject, "date": date, "body": body})

    return emails


def summarize_with_claude(emails, sender_email):
    """Sends all email bodies to Claude and returns a summary."""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    combined = "\n\n---\n\n".join(
        f"Subject: {e['subject']}\nDate: {e['date']}\n\n{e['body']}" for e in emails
    )

    # Truncate to keep well within context limits for very large mailboxes
    combined = combined[:150_000]

    prompt = (
        f"Here are {len(emails)} emails from {sender_email}. "
        "Summarize the key themes, any action items, and anything time-sensitive. "
        "Group related emails together rather than going one by one.\n\n"
        f"{combined}"
    )

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text


def main():
    print(f"Authenticating with Gmail...")
    service = get_gmail_service()

    print(f"Fetching emails from {TARGET_EMAIL}...")
    emails = fetch_emails_from_sender(service, TARGET_EMAIL, MAX_EMAILS)
    print(f"Found {len(emails)} emails.")

    if not emails:
        print("No emails found for that sender.")
        return

    print("Summarizing with Claude...")
    summary = summarize_with_claude(emails, TARGET_EMAIL)

    print("\n===== SUMMARY =====\n")
    print(summary)


if __name__ == "__main__":
    main()