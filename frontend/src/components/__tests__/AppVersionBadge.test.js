/**
 * Tests — Fase 2 (Identidad del frontend) — Auditoría Drift Producción.
 *
 * Valida que ``AppVersionBadge`` se monte en el DOM con
 * ``data-testid="app-version-badge"`` y exponga los metadatos de bundle
 * a tests automatizados y a inspección manual del usuario.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { AppVersionBadge } from "@/components/AppVersionBadge";

describe("AppVersionBadge (Fase 2 — Audit Drift)", () => {
  test("renders the badge with data-testid in the DOM", () => {
    render(<AppVersionBadge />);
    const badge = screen.getByTestId("app-version-badge");
    expect(badge).toBeInTheDocument();
  });

  test("exposes commit_sha and build_time via data-* attributes", () => {
    render(<AppVersionBadge />);
    const badge = screen.getByTestId("app-version-badge");

    expect(badge).toHaveAttribute("data-commit-sha");
    expect(badge).toHaveAttribute("data-commit-sha-short");
    expect(badge).toHaveAttribute("data-build-time");
    expect(badge).toHaveAttribute("data-app-version");
    expect(badge).toHaveAttribute(
      "data-audit-phase",
      "F99-P0-PRODUCTION-DRIFT-AUDIT",
    );
  });

  test("badge is visually hidden but reachable for tests", () => {
    render(<AppVersionBadge />);
    const badge = screen.getByTestId("app-version-badge");

    // Debe estar marcado como aria-hidden para lectores de pantalla, pero
    // seguir existiendo en el DOM (sr-only).
    expect(badge).toHaveAttribute("aria-hidden", "true");
    expect(badge.className).toContain("sr-only");
  });

  test("textContent includes commit_sha label", () => {
    render(<AppVersionBadge />);
    const badge = screen.getByTestId("app-version-badge");

    expect(badge.textContent).toMatch(/commit_sha=/);
    expect(badge.textContent).toMatch(/build_time=/);
    expect(badge.textContent).toMatch(/app_version=/);
  });
});
