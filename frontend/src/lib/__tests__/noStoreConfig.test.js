/**
 * Tests — lib/api · noStoreConfig (Fase 7 — Audit Drift Producción).
 */

import { noStoreConfig } from "@/lib/api";

describe("noStoreConfig", () => {
  test("returns headers with no-store directives", () => {
    const cfg = noStoreConfig();
    expect(cfg.headers["Cache-Control"]).toMatch(/no-store/);
    expect(cfg.headers["Cache-Control"]).toMatch(/no-cache/);
    expect(cfg.headers["Pragma"]).toBe("no-cache");
  });

  test("merges with extra config without losing other headers", () => {
    const cfg = noStoreConfig({ headers: { "X-Custom": "v1" }, timeout: 5000 });

    expect(cfg.headers["X-Custom"]).toBe("v1");
    expect(cfg.headers["Cache-Control"]).toMatch(/no-store/);
    expect(cfg.timeout).toBe(5000);
  });

  test("works with no arguments", () => {
    const cfg = noStoreConfig();
    expect(cfg).toBeTruthy();
    expect(cfg.headers).toBeTruthy();
  });

  test("does not pollute global object", () => {
    const a = noStoreConfig({ headers: { Foo: "1" } });
    const b = noStoreConfig({ headers: { Bar: "2" } });
    expect(a.headers.Foo).toBe("1");
    expect(a.headers.Bar).toBeUndefined();
    expect(b.headers.Bar).toBe("2");
    expect(b.headers.Foo).toBeUndefined();
  });
});
