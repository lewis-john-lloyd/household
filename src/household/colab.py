"""Sign-in for notebooks running in Google Colab.

The notebook user signs in with their own Google account. No credential is
stored in this package or in the notebook.
"""

from dataclasses import dataclass


@dataclass
class Session:
    sheets: object  # gspread client
    credentials: object

    def drive(self):
        from googleapiclient.discovery import build

        return build("drive", "v3", credentials=self.credentials, cache_discovery=False)


def sign_in():
    """Prompt for Google sign-in in Colab and return a Session (Sheets and Drive)."""
    from google.colab import auth  # Only available inside Colab.
    import google.auth
    import gspread

    auth.authenticate_user()
    creds, _ = google.auth.default()
    client = gspread.authorize(creds, http_client=gspread.http_client.BackOffHTTPClient)
    return Session(sheets=client, credentials=creds)


def authenticate():
    """Return just the gspread client (kept for the v0.0.1 scratch notebook)."""
    return sign_in().sheets
