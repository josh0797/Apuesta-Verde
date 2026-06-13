/**
 * Phase F85.5 — PublicXGEnrichmentButton
 *
 * Thin alias of :mod:`PublicXGPanel` so the spec's two-component naming
 * is honoured. They are the same React component — the panel manages
 * its own CTA. This wrapper exists purely so callers can import
 * ``PublicXGEnrichmentButton`` when that name reads better at the call
 * site (e.g. when placed next to ``CornersEnrichmentButton``).
 */
export { PublicXGPanel as PublicXGEnrichmentButton } from './PublicXGPanel';
export { default } from './PublicXGPanel';
