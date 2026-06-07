/**
 * normalizeDecimalOdds — accept both comma and dot as decimal separator.
 *
 * Users on Spanish-locale phones get a comma when typing on a numeric
 * keyboard. The backend always expects a JS Number (dot decimals), so we
 * normalize the string here BEFORE sending the payload.
 *
 * Returns a finite Number, or `null` when the value is empty / invalid /
 * not parseable as a decimal.
 *
 * Examples:
 *   normalizeDecimalOdds("1.20")  -> 1.2
 *   normalizeDecimalOdds("1,20")  -> 1.2
 *   normalizeDecimalOdds("1,2")   -> 1.2
 *   normalizeDecimalOdds("1.2")   -> 1.2
 *   normalizeDecimalOdds(" 1,85 ") -> 1.85
 *   normalizeDecimalOdds("abc")   -> null
 *   normalizeDecimalOdds(null)    -> null
 *   normalizeDecimalOdds("")      -> null
 *   normalizeDecimalOdds(0)       -> null   // odds must be > 1.01 anyway
 *   normalizeDecimalOdds(-1.5)    -> null
 */
export function normalizeDecimalOdds(value) {
  if (value === null || value === undefined) return null;
  const raw = String(value).replace(',', '.').trim();
  if (!raw) return null;
  const n = Number(raw);
  if (!Number.isFinite(n)) return null;
  if (n <= 0) return null;        // reject zero / negative explicitly
  return n;
}

/**
 * normalizeManualOddsInput — alias the live-reeval flow uses.
 *
 * Same contract as normalizeDecimalOdds. Kept separate so future
 * tweaks to the manual-odds UX (e.g. clamping above 1.01, locale
 * heuristics) live in one place without touching the generic helper.
 */
export function normalizeManualOddsInput(value) {
  const n = normalizeDecimalOdds(value);
  // Manual odds must be > 1.01 to be meaningful (a bookmaker can't
  // price a market below "even money" plus the smallest tick).
  if (n === null || n <= 1.01) return null;
  return n;
}

/**
 * isValidBookieOdds — quick gate to confirm the user typed something usable.
 * Bookmakers can't price an even-money bet at <= 1.01, so we use that as
 * the floor.
 */
export function isValidBookieOdds(value) {
  return normalizeManualOddsInput(value) !== null;
}

export default normalizeDecimalOdds;
