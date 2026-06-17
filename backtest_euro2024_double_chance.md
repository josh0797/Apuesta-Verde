# Football DOUBLE CHANCE Backtest Report — Euro 2024

Generated: 2026-06-17T12:41:57.786636Z

> ⚠️  Sprint-D3 · **observe_only + calibration_only**.

## Headline

| Variant | n_preds | n_fired | hit_rate | base_rate | brier | calibration |
|---|---:|---:|---:|---:|---:|---|
| DOUBLE_CHANCE_HD | 39 | 22 | 0.6818 | 0.7436 | 0.19689 | MISCALIBRATED |
| DOUBLE_CHANCE_AD | 39 | 30 | 0.7 | 0.6667 | 0.24768 | MISCALIBRATED |
| DOUBLE_CHANCE_HA | 39 | 5 | 0.6 | 0.5897 | 0.27663 | MISCALIBRATED |

---
## DOUBLE_CHANCE_HD

### Configuration
- `market` = `DOUBLE_CHANCE_HD`
- `min_pred_prob_pp` = `70.0`
- `use_calibration` = `True`

### DOUBLE_CHANCE_HD · Group Stage
- n_predictions: `24`
- n_hits: `17`
- base_rate: `0.7083`
- brier_score: `0.21311` *(lower is better)*
- log_loss: `0.61941`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 0 | None | None | None |
| [0.50,0.60) | 0 | None | None | None |
| [0.60,0.70) | 3 | 0.6717 | 1.0 | 1.0 |
| [0.70,0.80) | 21 | 0.7558 | 0.6667 | 0.6667 |
| [0.80,0.90) | 0 | None | None | None |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_HD · Knockout
- n_predictions: `15`
- n_hits: `12`
- base_rate: `0.8`
- brier_score: `0.17095` *(lower is better)*
- log_loss: `0.52789`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 0 | None | None | None |
| [0.50,0.60) | 0 | None | None | None |
| [0.60,0.70) | 14 | 0.6818 | 0.7857 | 0.7857 |
| [0.70,0.80) | 1 | 0.7635 | 1.0 | 1.0 |
| [0.80,0.90) | 0 | None | None | None |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_HD · Combined
- n_predictions: `39`
- n_hits: `29`
- base_rate: `0.7436`
- brier_score: `0.19689` *(lower is better)*
- log_loss: `0.58421`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 0 | None | None | None |
| [0.50,0.60) | 0 | None | None | None |
| [0.60,0.70) | 17 | 0.68 | 0.8235 | 0.8235 |
| [0.70,0.80) | 22 | 0.7561 | 0.6818 | 0.6818 |
| [0.80,0.90) | 0 | None | None | None |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_HD · Label hit-rate (Combined)
| Label | n | won | hit_rate |
|---|---|---|---|
| VALUE_CANDIDATE | 11 | 7 | 0.6364 |
| STRONG_VALUE | 11 | 8 | 0.7273 |

### DOUBLE_CHANCE_HD · False positives (top 10 by confidence, did NOT hit)
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2024-06-21 | Slovakia vs Ukraine | 0.7709 | STRONG_VALUE | GROUP | 1-2 | True |
| 2024-06-23 | Scotland vs Hungary | 0.7668 | STRONG_VALUE | GROUP | 0-1 | True |
| 2024-06-25 | Netherlands vs Austria | 0.7668 | STRONG_VALUE | GROUP | 2-3 | True |
| 2024-06-26 | Czech Republic vs Turkey | 0.7496 | VALUE_CANDIDATE | GROUP | 1-2 | True |
| 2024-06-21 | Poland vs Austria | 0.746 | VALUE_CANDIDATE | GROUP | 1-3 | True |
| 2024-06-22 | Turkey vs Portugal | 0.746 | VALUE_CANDIDATE | GROUP | 0-3 | True |
| 2024-06-24 | Albania vs Spain | 0.7189 | VALUE_CANDIDATE | GROUP | 0-1 | True |

### DOUBLE_CHANCE_HD · False negatives (top 10 lowest confidence, DID hit)
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2024-06-26 | Georgia vs Portugal | 0.6612 | FAIR_NO_EDGE | GROUP | 2-0 | False |
| 2024-06-19 | Scotland vs Switzerland | 0.6769 | FAIR_NO_EDGE | GROUP | 1-1 | False |
| 2024-06-22 | Belgium vs Romania | 0.6769 | FAIR_NO_EDGE | GROUP | 2-0 | False |
| 2024-06-29 | Germany vs Denmark | 0.6818 | FAIR_NO_EDGE | KNOCKOUT | 2-0 | False |
| 2024-06-30 | England vs Slovakia | 0.6818 | FAIR_NO_EDGE | KNOCKOUT | 2-1 | False |
| 2024-06-30 | Spain vs Georgia | 0.6818 | FAIR_NO_EDGE | KNOCKOUT | 4-1 | False |
| 2024-07-01 | France vs Belgium | 0.6818 | FAIR_NO_EDGE | KNOCKOUT | 1-0 | False |
| 2024-07-01 | Portugal vs Slovenia | 0.6818 | FAIR_NO_EDGE | KNOCKOUT | 0-0 | False |
| 2024-07-05 | Spain vs Germany | 0.6818 | FAIR_NO_EDGE | KNOCKOUT | 2-1 | False |
| 2024-07-05 | Portugal vs France | 0.6818 | FAIR_NO_EDGE | KNOCKOUT | 0-0 | False |

---
## DOUBLE_CHANCE_AD

### Configuration
- `market` = `DOUBLE_CHANCE_AD`
- `min_pred_prob_pp` = `55.0`
- `use_calibration` = `True`

### DOUBLE_CHANCE_AD · Group Stage
- n_predictions: `24`
- n_hits: `20`
- base_rate: `0.8333`
- brier_score: `0.20926` *(lower is better)*
- log_loss: `0.61112`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 1 | 0.4926 | 0.0 | 0.0 |
| [0.50,0.60) | 23 | 0.5611 | 0.8696 | 0.8696 |
| [0.60,0.70) | 0 | None | None | None |
| [0.70,0.80) | 0 | None | None | None |
| [0.80,0.90) | 0 | None | None | None |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_AD · Knockout
- n_predictions: `15`
- n_hits: `6`
- base_rate: `0.4`
- brier_score: `0.30915` *(lower is better)*
- log_loss: `0.84302`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 2 | 0.0 | 0.0 | 0.0 |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 0 | None | None | None |
| [0.50,0.60) | 1 | 0.5515 | 0.0 | 0.0 |
| [0.60,0.70) | 0 | None | None | None |
| [0.70,0.80) | 0 | None | None | None |
| [0.80,0.90) | 12 | 0.8333 | 0.5 | 0.5 |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_AD · Combined
- n_predictions: `39`
- n_hits: `26`
- base_rate: `0.6667`
- brier_score: `0.24768` *(lower is better)*
- log_loss: `0.70031`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 2 | 0.0 | 0.0 | 0.0 |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 1 | 0.4926 | 0.0 | 0.0 |
| [0.50,0.60) | 24 | 0.5607 | 0.8333 | 0.8333 |
| [0.60,0.70) | 0 | None | None | None |
| [0.70,0.80) | 0 | None | None | None |
| [0.80,0.90) | 12 | 0.8333 | 0.5 | 0.5 |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_AD · Label hit-rate (Combined)
| Label | n | won | hit_rate |
|---|---|---|---|
| VALUE_CANDIDATE | 18 | 15 | 0.8333 |
| STRONG_VALUE | 12 | 6 | 0.5 |

### DOUBLE_CHANCE_AD · False positives (top 10 by confidence, did NOT hit)
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2024-06-30 | England vs Slovakia | 0.8333 | STRONG_VALUE | KNOCKOUT | 2-1 | True |
| 2024-06-30 | Spain vs Georgia | 0.8333 | STRONG_VALUE | KNOCKOUT | 4-1 | True |
| 2024-07-01 | France vs Belgium | 0.8333 | STRONG_VALUE | KNOCKOUT | 1-0 | True |
| 2024-07-05 | Spain vs Germany | 0.8333 | STRONG_VALUE | KNOCKOUT | 2-1 | True |
| 2024-07-06 | Netherlands vs Turkey | 0.8333 | STRONG_VALUE | KNOCKOUT | 2-1 | True |
| 2024-07-14 | Spain vs England | 0.8333 | STRONG_VALUE | KNOCKOUT | 2-1 | True |
| 2024-06-20 | Spain vs Italy | 0.561 | VALUE_CANDIDATE | GROUP | 1-0 | True |
| 2024-06-26 | Georgia vs Portugal | 0.5608 | VALUE_CANDIDATE | GROUP | 2-0 | True |
| 2024-06-29 | Switzerland vs Italy | 0.5515 | VALUE_CANDIDATE | KNOCKOUT | 2-0 | True |

### DOUBLE_CHANCE_AD · False negatives (top 10 lowest confidence, DID hit)
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2024-06-25 | France vs Poland | 0.5145 | FAIR_NO_EDGE | GROUP | 1-1 | False |
| 2024-06-21 | Slovakia vs Ukraine | 0.5311 | FAIR_NO_EDGE | GROUP | 1-2 | False |
| 2024-06-23 | Switzerland vs Germany | 0.5394 | FAIR_NO_EDGE | GROUP | 1-1 | False |
| 2024-06-20 | Slovenia vs Serbia | 0.5457 | FAIR_NO_EDGE | GROUP | 1-1 | False |
| 2024-06-19 | Scotland vs Switzerland | 0.5481 | FAIR_NO_EDGE | GROUP | 1-1 | False |

---
## DOUBLE_CHANCE_HA

### Configuration
- `market` = `DOUBLE_CHANCE_HA`
- `min_pred_prob_pp` = `70.0`
- `use_calibration` = `True`

### DOUBLE_CHANCE_HA · Group Stage
- n_predictions: `24`
- n_hits: `11`
- base_rate: `0.4583`
- brier_score: `0.2933` *(lower is better)*
- log_loss: `0.78553`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 0 | None | None | None |
| [0.50,0.60) | 0 | None | None | None |
| [0.60,0.70) | 19 | 0.6773 | 0.4211 | 0.4211 |
| [0.70,0.80) | 5 | 0.7696 | 0.6 | 0.6 |
| [0.80,0.90) | 0 | None | None | None |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_HA · Knockout
- n_predictions: `15`
- n_hits: `12`
- base_rate: `0.8`
- brier_score: `0.24995` *(lower is better)*
- log_loss: `0.692`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 12 | 0.45 | 0.75 | 0.75 |
| [0.50,0.60) | 0 | None | None | None |
| [0.60,0.70) | 3 | 0.6283 | 1.0 | 1.0 |
| [0.70,0.80) | 0 | None | None | None |
| [0.80,0.90) | 0 | None | None | None |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_HA · Combined
- n_predictions: `39`
- n_hits: `23`
- base_rate: `0.5897`
- brier_score: `0.27663` *(lower is better)*
- log_loss: `0.74956`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 12 | 0.45 | 0.75 | 0.75 |
| [0.50,0.60) | 0 | None | None | None |
| [0.60,0.70) | 22 | 0.6706 | 0.5 | 0.5 |
| [0.70,0.80) | 5 | 0.7696 | 0.6 | 0.6 |
| [0.80,0.90) | 0 | None | None | None |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_HA · Label hit-rate (Combined)
| Label | n | won | hit_rate |
|---|---|---|---|
| STRONG_VALUE | 4 | 3 | 0.75 |
| VALUE_CANDIDATE | 1 | 0 | 0.0 |

### DOUBLE_CHANCE_HA · False positives (top 10 by confidence, did NOT hit)
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2024-06-19 | Scotland vs Switzerland | 0.775 | STRONG_VALUE | GROUP | 1-1 | True |
| 2024-06-23 | Switzerland vs Germany | 0.745 | VALUE_CANDIDATE | GROUP | 1-1 | True |

### DOUBLE_CHANCE_HA · False negatives (top 10 lowest confidence, DID hit)
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2024-06-30 | England vs Slovakia | 0.45 | NO_VALUE | KNOCKOUT | 2-1 | False |
| 2024-06-30 | Spain vs Georgia | 0.45 | NO_VALUE | KNOCKOUT | 4-1 | False |
| 2024-07-01 | France vs Belgium | 0.45 | NO_VALUE | KNOCKOUT | 1-0 | False |
| 2024-07-02 | Romania vs Netherlands | 0.45 | NO_VALUE | KNOCKOUT | 0-3 | False |
| 2024-07-02 | Austria vs Turkey | 0.45 | NO_VALUE | KNOCKOUT | 1-2 | False |
| 2024-07-05 | Spain vs Germany | 0.45 | NO_VALUE | KNOCKOUT | 2-1 | False |
| 2024-07-06 | Netherlands vs Turkey | 0.45 | NO_VALUE | KNOCKOUT | 2-1 | False |
| 2024-07-10 | Netherlands vs England | 0.45 | NO_VALUE | KNOCKOUT | 1-2 | False |
| 2024-07-14 | Spain vs England | 0.45 | NO_VALUE | KNOCKOUT | 2-1 | False |
| 2024-06-29 | Germany vs Denmark | 0.6 | NO_VALUE | KNOCKOUT | 2-0 | False |

