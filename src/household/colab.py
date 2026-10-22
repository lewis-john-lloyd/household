"""Sign-in for notebooks running in Google Colab.

The notebook user signs in with their own Google account. No credential is
stored in this package or in the notebook.
"""


def authenticate():
    """Prompt for Google sign-in in Colab and return an authorized gspread client."""
    from google.colab import auth  # Only available inside Colab.
    import google.auth
    import gspread

    auth.authenticate_user()
    creds, _ = google.auth.default()
    return gspread.authorize(creds)
