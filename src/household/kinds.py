"""The types the stages hand each other, for annotations only: what a stage is given and what it returns.

A session is the engine notebook's handle on Google: .sheets opens spreadsheets by name or key, and .drive()
gives the Drive service. A log takes one line at a time (print, or a stage's recorder). The spreadsheets and
the Drive service are Google's own objects, typed as Any here.
"""

from typing import Any, Callable, Protocol

import pandas as pd

Log = Callable[..., Any]
Spreadsheet = Any  # a gspread Spreadsheet, or a test's stand-in
Frame = pd.DataFrame
StageResult = dict[str, Any]  # what a stage did, for the run's table of stages and the Status page


class Session(Protocol):
    sheets: Any

    def drive(self) -> Any: ...
