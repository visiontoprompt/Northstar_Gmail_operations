"""Curated Gmail add-on operation catalog.

Only listing URLs from the Google Workspace Marketplace are kept here. The
catalog is deliberately data-only: installation remains in the profile-backed
observer, which accepts the Marketplace installation confirmation but pauses
for publisher OAuth, billing, or subscription consent.
"""

from __future__ import annotations

import secrets


GMAIL_ADDON_CATALOG: tuple[dict[str, str], ...] = (
    {
        "name": "Any.do for Gmail",
        "url": "https://workspace.google.com/marketplace/app/anydo_for_gmail/38800197956",
    },
    {
        "name": "Recurring Emails for Gmail",
        "url": "https://workspace.google.com/marketplace/app/recurring_emails_for_gmail/471382602177",
    },
    {
        "name": "GPT Workspace for Gmail",
        "url": "https://workspace.google.com/marketplace/app/gpt_workspace_for_gmail/802100925247?flow_type=48",
    },
    {
        "name": "Email Studio for Gmail",
        "url": "https://workspace.google.com/marketplace/app/email_studio_for_gmail/60106804857",
    },
    {
        "name": "Mailtrack for Gmail",
        "url": "https://workspace.google.com/marketplace/app/mailtrack_for_gmail_email_tracker/224054973946",
    },
    {
        "name": "Mail Tracker for Gmail",
        "url": "https://workspace.google.com/marketplace/app/mail_tracker_for_gmail/888949007475?flow_type=63",
    },
    {
        "name": "ContactsFlow for Gmail",
        "url": "https://workspace.google.com/marketplace/app/contactsflow_for_gmail/1033860418004",
    },
    {
        "name": "Greenhouse for Gmail",
        "url": "https://workspace.google.com/marketplace/app/greenhouse_for_gmail/250187532019",
    },
    {
        "name": "Group Merge for Gmail",
        "url": "https://workspace.google.com/marketplace/app/group_merge_mail_merge_for_gmail/586770229603",
    },
    {
        "name": "Relay for Gmail",
        "url": "https://workspace.google.com/marketplace/app/relay_for_gmail/713369689425",
    },
)


def select_addon_candidates(count: int = 3) -> list[dict[str, str]]:
    """Return a fresh, bounded selection without exposing account secrets."""
    if count < 1:
        return []
    # Use a cryptographically strong shuffle so each account's run is
    # independent, while preserving the hard maximum requested by the product
    # contract.
    candidates = [dict(item) for item in GMAIL_ADDON_CATALOG]
    for index in range(len(candidates) - 1, 0, -1):
        swap_index = secrets.randbelow(index + 1)
        candidates[index], candidates[swap_index] = candidates[swap_index], candidates[index]
    return candidates[: min(count, len(candidates))]
