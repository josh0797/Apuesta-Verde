/* Sprint-D7-G · Calibration Diagnostics standalone page. */
import { CalibrationDiagnosticsPanel } from "@/components/CalibrationDiagnosticsPanel";

export default function CalibrationDiagnosticsPage() {
  return (
    <div className="container mx-auto px-4 py-6 max-w-6xl">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight" data-testid="calibration-diagnostics-title">
          Calibration Diagnostics
        </h1>
        <p className="text-sm text-muted-foreground mt-1.5 max-w-3xl">
          Diagnóstico riguroso de calidad del modelo para los mercados DRAW, OVER_2_5 y UNDER_2_5.
          Incluye reliability curve, calibration intercept/slope, Brier y log-loss vs mercado de-vig,
          AUC, sharpness, realized edge por bucket y CLV apertura vs cierre. Reportes pre-computados
          desde football-data.co.uk · observe_only.
        </p>
      </header>
      <CalibrationDiagnosticsPanel />
    </div>
  );
}
