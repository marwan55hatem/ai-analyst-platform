/** RFM grid: recency score (rows, 5 = most recent at the top) by frequency score (columns). */

export interface RfmCell {
  r_score: number;
  f_score: number;
  customers: number;
  monetary: number;
}

export interface GridCell {
  row: number;
  col: number;
  customers: number;
  /** Share of all customers in percent. */
  share: number;
  /** Sequential step 0-4 for the fill (0 = no customers). */
  level: number;
}

export const SCORES = [1, 2, 3, 4, 5] as const;

/** A full 5 x 5 grid (missing cells are empty) with a fill level per cell. */
export function rfmGrid(cells: readonly RfmCell[]): GridCell[][] {
  const total = cells.reduce((sum, c) => sum + c.customers, 0);
  const max = Math.max(0, ...cells.map((c) => c.customers));
  const lookup = new Map(cells.map((c) => [`${c.r_score}:${c.f_score}`, c]));
  return [...SCORES].reverse().map((r) =>
    SCORES.map((f) => {
      const found = lookup.get(`${r}:${f}`);
      const customers = found?.customers ?? 0;
      return {
        row: r,
        col: f,
        customers,
        share: total ? (100 * customers) / total : 0,
        level: customers && max ? Math.min(4, Math.ceil((4 * customers) / max)) : 0,
      };
    }),
  );
}
