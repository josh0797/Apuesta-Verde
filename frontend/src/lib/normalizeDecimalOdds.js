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
 */
export function normalizeDecimalOdds(value) {
  if (value === null || value === undefined) return null;
  const raw = String(value).replace(',', '.').trim();
  if (!raw) return null;
  const n = Number(raw);
  if (!Number.isFinite(n)) return null;
  return n;
}

/**
 * isValidBookieOdds — quick gate to confirm the user typed something usable.
 * Bookmakers can't price an even-money bet at <= 1.01, so we use that as
 * the floor.
 */
export function isValidBookieOdds(value) {
  const n = normalizeDecimalOdds(value);
  return n !== null && n > 1.01;
}

export default normalizeDecimalOdds;
