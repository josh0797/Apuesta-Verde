/**
 * Tests — lib/appMetadata (Fase 2 — Audit Drift Producción).
 *
 * Estos tests usan jest.resetModules() para poder forzar variables de
 * entorno antes de cada `require`.  Esto es necesario porque el módulo lee
 * `process.env.*` en evaluación, no en runtime.
 */

describe("lib/appMetadata", () => {
  const ORIGINAL_ENV = process.env;

  beforeEach(() => {
    jest.resetModules();
    process.env = { ...ORIGINAL_ENV };
  });

  afterAll(() => {
    process.env = ORIGINAL_ENV;
  });

  test("returns 'unknown' when no env vars provided", () => {
    delete process.env.REACT_APP_APP_VERSION;
    delete process.env.REACT_APP_COMMIT_SHA;
    delete process.env.REACT_APP_BUILD_TIME;

    // eslint-disable-next-line global-require
    const meta = require("../appMetadata");

    expect(meta.APP_VERSION).toBe("unknown");
    expect(meta.COMMIT_SHA).toBe("unknown");
    expect(meta.COMMIT_SHA_SHORT).toBe("unknown");
    expect(meta.BUILD_TIME).toBe("unknown");
  });

  test("reads env vars when provided", () => {
    process.env.REACT_APP_APP_VERSION = "1.2.3";
    process.env.REACT_APP_COMMIT_SHA = "abcdef1234567890";
    process.env.REACT_APP_BUILD_TIME = "2026-06-22T10:00:00Z";

    // eslint-disable-next-line global-require
    const meta = require("../appMetadata");

    expect(meta.APP_VERSION).toBe("1.2.3");
    expect(meta.COMMIT_SHA).toBe("abcdef1234567890");
    expect(meta.COMMIT_SHA_SHORT).toBe("abcdef1");
    expect(meta.BUILD_TIME).toBe("2026-06-22T10:00:00Z");
  });

  test("APP_METADATA is frozen and contains all keys", () => {
    process.env.REACT_APP_COMMIT_SHA = "deadbeef";
    process.env.REACT_APP_BUILD_TIME = "2026-01-01T00:00:00Z";
    process.env.REACT_APP_APP_VERSION = "9.9.9";

    // eslint-disable-next-line global-require
    const { APP_METADATA } = require("../appMetadata");

    expect(Object.isFrozen(APP_METADATA)).toBe(true);
    expect(APP_METADATA).toEqual(
      expect.objectContaining({
        appVersion: "9.9.9",
        commitSha: "deadbeef",
        commitShaShort: "deadbee",
        buildTime: "2026-01-01T00:00:00Z",
        auditPhase: "F99-P0-PRODUCTION-DRIFT-AUDIT",
      }),
    );
  });

  test("logAppMetadataOnce only logs once", () => {
    // eslint-disable-next-line global-require
    const { logAppMetadataOnce } = require("../appMetadata");

    const spy = jest.spyOn(console, "info").mockImplementation(() => {});
    logAppMetadataOnce();
    logAppMetadataOnce();
    logAppMetadataOnce();

    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toContain("[app-version]");
    spy.mockRestore();
  });
});
