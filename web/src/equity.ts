// The Equity page's data (compute's equity.py tables): each grant, its vesting month by month over its life, and
// per symbol the price, the shares held and the shares still to vest. The page moves the price by the
// equity_price_steps setting and works the gains and values out from these, the same way equity.py does
// (sharePrice, grantKey and equitySteps are in budgetview.ts, which the page shares).

interface EquityGrant {
  grant_id: string;
  symbol: string;
  award_type: string; // RSU, PSU, RS, ESPP, or an option type (OPTION, ISO, NSO, NQ)
  award_date: string;
  grant_price: number | null; // a share's price at grant, or an option's exercise price; null when neither says
  price_from: string; // setting (grant_price:<grant id>), award page, or "" when there is none
  granted: number;
  vested: number;
  unvested: number;
  next_vest: string;
  last_vest: string;
}

interface EquityMonth {
  month: string;
  grant_id: string;
  symbol: string;
  shares: number;
  price: number;
  value: number;
  basis: string; // vested (its vest's fair market value) or latest (the latest price)
}

interface EquitySymbol {
  symbol: string;
  price: number | null; // the price everything is valued at, or null with no price yet
  price_date: string;
  held: number; // shares held (open lots)
  basis: number;
  value: number | null;
  gain: number | null;
  unvested: number;
  unvested_value: number | null;
}

interface EquityPage {
  symbols: EquitySymbol[];
  grants: EquityGrant[];
  months: EquityMonth[];
  steps: number[]; // dollars a share the page moves the price by, up and down (equity_price_steps)
  stepsSet: string | null; // the setting as stored, or null when it is the default
}

function equityPage(tabs: Record<string, unknown[][]>): EquityPage {
  const rows = (name: string): Record<string, string>[] => {
    const values = tabs[name] || [];
    if (!values.length) return [];
    const head = values[0].map((h) => String(h).trim());
    return values.slice(1).map((v) => {
      const r: Record<string, string> = {};
      head.forEach((h, i) => (r[h] = String(v[i] ?? "").trim()));
      return r;
    });
  };
  const num = (s: string): number => Number(s) || 0;
  const maybe = (s: string): number | null => (s === "" || !isFinite(Number(s)) ? null : Number(s));
  const grants = rows("equity_grants")
    .filter((r) => r.grant_id)
    .map((r) => ({
      grant_id: r.grant_id,
      symbol: r.symbol,
      award_type: r.award_type,
      award_date: r.award_date.slice(0, 10),
      grant_price: maybe(r.grant_price),
      price_from: r.price_from,
      granted: num(r.granted),
      vested: num(r.vested),
      unvested: num(r.unvested),
      next_vest: r.next_vest.slice(0, 10),
      last_vest: r.last_vest.slice(0, 10),
    }));
  const months = rows("equity_vesting")
    .filter((r) => /^\d{4}-\d{2}/.test(r.month))
    .map((r) => ({
      month: r.month.slice(0, 7),
      grant_id: r.grant_id,
      symbol: r.symbol,
      shares: num(r.shares),
      price: num(r.price),
      value: num(r.value),
      basis: r.basis,
    }));
  const symbols = rows("equity_summary")
    .filter((r) => r.symbol)
    .map((r) => ({
      symbol: r.symbol,
      price: maybe(r.price),
      price_date: r.price_date.slice(0, 10),
      held: num(r.held),
      basis: num(r.basis),
      value: maybe(r.value),
      gain: maybe(r.gain),
      unvested: num(r.unvested),
      unvested_value: maybe(r.unvested_value),
    }));
  const set = rows("editor_parameters").filter((r) => r.key === "equity_price_steps" && r.source !== "not set")[0];
  return { symbols, grants, months, steps: equitySteps(set ? set.value : null), stepsSet: set ? set.value : null };
}
