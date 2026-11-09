// What the page uses of Google's browser libraries: Apps Script's google.script, which calls the server's
// functions (typed by the functions themselves, in Code.ts), and Google Charts.

/** The server functions the page calls, by name. */
interface ServerCalls {
  getPeriods: typeof getPeriods;
  getFlows: typeof getFlows;
  getTransactions: typeof getTransactions;
  getBudgetBook: typeof getBudgetBook;
  getStatus: typeof getStatus;
  getEditor: typeof getEditor;
  saveChanges: typeof saveChanges;
  findSave: typeof findSave;
  getWaiting: typeof getWaiting;
  discardWaiting: typeof discardWaiting;
  getAppliedAnyway: typeof getAppliedAnyway;
  forgetDiscarded: typeof forgetDiscarded;
  getTransaction: typeof getTransaction;
  getReviewLines: typeof getReviewLines;
  getTaxes: typeof getTaxes;
  getForm8949: typeof getForm8949;
  getInvestments: typeof getInvestments;
  getHome: typeof getHome;
  getRetirement: typeof getRetirement;
}

/** How the page was opened: its query parameters. */
interface PageLocation {
  parameter: Record<string, string>;
}

declare namespace google {
  namespace script {
    /** A call to the server: each function by name, with what to do on success or failure. */
    type Runner = { [K in keyof ServerCalls]: (...args: Parameters<ServerCalls[K]>) => void } & {
      withSuccessHandler(done: (result: never) => void): Runner;
      withFailureHandler(failed: (error: Error) => void): Runner;
    };
    const run: Runner;
    const url: { getLocation(then: (location: PageLocation) => void): void } | undefined;
  }
  namespace charts {
    function load(version: string, settings: { packages: string[] }): void;
    function setOnLoadCallback(then: () => void): void;
  }
  namespace visualization {
    class DataTable {
      addColumn(type: string | { type: string; role: string }, label?: string): number;
      addRows(rows: unknown[][]): number;
    }
    class Sankey {
      constructor(container: Element);
      draw(data: DataTable, options: object): void;
    }
  }
}
