"""Read-only access for the explorer notebook. Reads Reports; never writes anything."""

from . import schema, sheets


def monthly_actuals(session, folder_id):
    """Monthly spend by category from the latest engine run, as a DataFrame."""
    reports = session.sheets.open(sheets.REPORTS_TITLE, folder_id=folder_id)
    df = sheets.read_table(reports, schema.table(schema.REPORTS, "monthly_actuals"))
    if not df.empty:
        df["spend"] = df["spend"].astype(float)
    return df
