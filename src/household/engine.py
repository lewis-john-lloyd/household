"""Engine: the stages the engine notebook (runner.run) calls, in the order they run.

  stage_legacy   the old workbooks into the ledger, once each (legacy budget, investments, equity, net worth)
  stage_ingest   every file in the inbox into the ledger, then review answers and prices (the only ledger writer)
  stage_compute  Reports rebuilt from the ledger
  books          the ledger's transactions as both stages reason about them
  ledgerio       opening the ledger and Reports, and the ledger as compute reads it
  runlog         what each stage prints, kept for the views' Status page

Stores are found by name inside the household folder, so a notebook needs only the folder ID (and, for
migration, the legacy workbook ID). This module gathers what the notebook and the runner call.
"""

from .books import (  # noqa: F401
    categorize_books,
    read_books,
    with_item_categories,
)
from .ledgerio import (  # noqa: F401
    ACCOUNT_COLUMNS,
    _ACTIVITY_FIXES,
    _LEGACY_MOVED,
    _RENAMED,
    activity_fixes,
    bootstrap,
    legacy_moves,
    new_run_id,
    read_ledger,
    renamed_accounts,
    versions,
)
from .runlog import (  # noqa: F401
    STAGES,
    record_stage,
    recording,
    say,
)
from .stage_legacy import (  # noqa: F401
    ingest_legacy_equity,
    ingest_legacy_investments,
    ingest_legacy_net_worth,
    ingest_legacy_year,
)
from .stage_ingest import (  # noqa: F401
    CHANGES_WRITTEN_DAYS,
    PRICE_POLL_EVERY,
    PRICE_POLL_SECONDS,
    _not_yet,
    _price_requests,
    _review,
    changes_written,
    fetch_prices,
    ingest_inbox,
    review,
)
from .stage_compute import (  # noqa: F401
    RETIREMENT_TABLES,
    _investments_and_homes,
    _lots,
    _retirement,
    compute_all,
)
