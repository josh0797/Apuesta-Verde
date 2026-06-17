# Football DOUBLE CHANCE Backtest Report — World Cup 2022

Generated: 2026-06-17T12:41:57.781314Z

> ⚠️  Sprint-D3 · **observe_only + calibration_only**.

## Headline

| Variant | n_preds | n_fired | hit_rate | base_rate | brier | calibration |
|---|---:|---:|---:|---:|---:|---|
| DOUBLE_CHANCE_HD | 48 | 22 | 0.5455 | 0.6667 | 0.25852 | MISCALIBRATED |
| DOUBLE_CHANCE_AD | 48 | 31 | 0.4839 | 0.5417 | 0.24678 | ACCEPTABLE_CALIBRATION |
| DOUBLE_CHANCE_HA | 48 | 35 | 0.7714 | 0.7917 | 0.17508 | MISCALIBRATED |

---
## DOUBLE_CHANCE_HD

### Configuration
- `market` = `DOUBLE_CHANCE_HD`
- `min_pred_prob_pp` = `70.0`
- `use_calibration` = `True`

### DOUBLE_CHANCE_HD · Group Stage
- n_predictions: `32`
- n_hits: `17`
- base_rate: `0.5312`
- brier_score: `0.2729` *(lower is better)*
- log_loss: `0.74956`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 0 | None | None | None |
| [0.50,0.60) | 6 | 0.5238 | 0.3333 | 0.3333 |
| [0.60,0.70) | 4 | 0.6845 | 0.75 | 0.75 |
| [0.70,0.80) | 22 | 0.7496 | 0.5455 | 0.5455 |
| [0.80,0.90) | 0 | None | None | None |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_HD · Knockout
- n_predictions: `16`
- n_hits: `15`
- base_rate: `0.9375`
- brier_score: `0.22974` *(lower is better)*
- log_loss: `0.6526`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 0 | None | None | None |
| [0.50,0.60) | 16 | 0.5238 | 0.9375 | 0.9375 |
| [0.60,0.70) | 0 | None | None | None |
| [0.70,0.80) | 0 | None | None | None |
| [0.80,0.90) | 0 | None | None | None |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_HD · Combined
- n_predictions: `48`
- n_hits: `32`
- base_rate: `0.6667`
- brier_score: `0.25852` *(lower is better)*
- log_loss: `0.71724`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 0 | None | None | None |
| [0.50,0.60) | 22 | 0.5238 | 0.7727 | 0.7727 |
| [0.60,0.70) | 4 | 0.6845 | 0.75 | 0.75 |
| [0.70,0.80) | 22 | 0.7496 | 0.5455 | 0.5455 |
| [0.80,0.90) | 0 | None | None | None |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_HD · Label hit-rate (Combined)
| Label | n | won | hit_rate |
|---|---|---|---|
| STRONG_VALUE | 13 | 6 | 0.4615 |
| VALUE_CANDIDATE | 9 | 6 | 0.6667 |

### DOUBLE_CHANCE_HD · False positives (top 10 by confidence, did NOT hit)
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2022-11-29 | Ecuador vs Senegal | 0.7795 | STRONG_VALUE | GROUP | 1-2 | True |
| 2022-11-30 | Poland vs Argentina | 0.771 | STRONG_VALUE | GROUP | 0-2 | True |
| 2022-11-25 | Qatar vs Senegal | 0.7625 | STRONG_VALUE | GROUP | 1-3 | True |
| 2022-11-30 | Saudi Arabia vs Mexico | 0.7625 | STRONG_VALUE | GROUP | 1-2 | True |
| 2022-11-25 | Wales vs Iran | 0.7583 | STRONG_VALUE | GROUP | 0-2 | True |
| 2022-11-26 | Tunisia vs Australia | 0.7583 | STRONG_VALUE | GROUP | 0-1 | True |
| 2022-11-27 | Belgium vs Morocco | 0.7583 | STRONG_VALUE | GROUP | 0-2 | True |
| 2022-11-27 | Japan vs Costa Rica | 0.7324 | VALUE_CANDIDATE | GROUP | 0-1 | True |
| 2022-11-28 | South Korea vs Ghana | 0.7198 | VALUE_CANDIDATE | GROUP | 2-3 | True |
| 2022-11-29 | Iran vs USA | 0.7117 | VALUE_CANDIDATE | GROUP | 0-1 | True |

### DOUBLE_CHANCE_HD · False negatives (top 10 lowest confidence, DID hit)
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2022-12-01 | Croatia vs Belgium | 0.5238 | NO_VALUE | GROUP | 0-0 | False |
| 2022-12-02 | Cameroon vs Brazil | 0.5238 | NO_VALUE | GROUP | 1-0 | False |
| 2022-12-03 | Netherlands vs USA | 0.5238 | NO_VALUE | KNOCKOUT | 3-1 | False |
| 2022-12-03 | Argentina vs Australia | 0.5238 | NO_VALUE | KNOCKOUT | 2-1 | False |
| 2022-12-04 | France vs Poland | 0.5238 | NO_VALUE | KNOCKOUT | 3-1 | False |
| 2022-12-04 | England vs Senegal | 0.5238 | NO_VALUE | KNOCKOUT | 3-0 | False |
| 2022-12-05 | Japan vs Croatia | 0.5238 | NO_VALUE | KNOCKOUT | 1-1 | False |
| 2022-12-05 | Brazil vs South Korea | 0.5238 | NO_VALUE | KNOCKOUT | 4-1 | False |
| 2022-12-06 | Morocco vs Spain | 0.5238 | NO_VALUE | KNOCKOUT | 0-0 | False |
| 2022-12-06 | Portugal vs Switzerland | 0.5238 | NO_VALUE | KNOCKOUT | 6-1 | False |

---
## DOUBLE_CHANCE_AD

### Configuration
- `market` = `DOUBLE_CHANCE_AD`
- `min_pred_prob_pp` = `55.0`
- `use_calibration` = `True`

### DOUBLE_CHANCE_AD · Group Stage
- n_predictions: `32`
- n_hits: `20`
- base_rate: `0.625`
- brier_score: `0.2408` *(lower is better)*
- log_loss: `0.67465`
- calibration_label: `ACCEPTABLE_CALIBRATION`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 2 | 0.4926 | 1.0 | 1.0 |
| [0.50,0.60) | 30 | 0.5481 | 0.6 | 0.6 |
| [0.60,0.70) | 0 | None | None | None |
| [0.70,0.80) | 0 | None | None | None |
| [0.80,0.90) | 0 | None | None | None |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_AD · Knockout
- n_predictions: `16`
- n_hits: `6`
- base_rate: `0.375`
- brier_score: `0.25874` *(lower is better)*
- log_loss: `0.69885`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 0 | None | None | None |
| [0.50,0.60) | 15 | 0.5652 | 0.3333 | 0.3333 |
| [0.60,0.70) | 0 | None | None | None |
| [0.70,0.80) | 0 | None | None | None |
| [0.80,0.90) | 0 | None | None | None |
| [0.90,1.00) | 1 | 1.0 | 1.0 | 1.0 |

### DOUBLE_CHANCE_AD · Combined
- n_predictions: `48`
- n_hits: `26`
- base_rate: `0.5417`
- brier_score: `0.24678` *(lower is better)*
- log_loss: `0.68271`
- calibration_label: `ACCEPTABLE_CALIBRATION`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 2 | 0.4926 | 1.0 | 1.0 |
| [0.50,0.60) | 45 | 0.5538 | 0.5111 | 0.5111 |
| [0.60,0.70) | 0 | None | None | None |
| [0.70,0.80) | 0 | None | None | None |
| [0.80,0.90) | 0 | None | None | None |
| [0.90,1.00) | 1 | 1.0 | 1.0 | 1.0 |

### DOUBLE_CHANCE_AD · Label hit-rate (Combined)
| Label | n | won | hit_rate |
|---|---|---|---|
| VALUE_CANDIDATE | 30 | 14 | 0.4667 |
| STRONG_VALUE | 1 | 1 | 1.0 |

### DOUBLE_CHANCE_AD · False positives (top 10 by confidence, did NOT hit)
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2022-11-26 | Argentina vs Mexico | 0.5735 | VALUE_CANDIDATE | GROUP | 2-0 | True |
| 2022-12-02 | Cameroon vs Brazil | 0.5652 | VALUE_CANDIDATE | GROUP | 1-0 | True |
| 2022-12-02 | South Korea vs Portugal | 0.5652 | VALUE_CANDIDATE | GROUP | 2-1 | True |
| 2022-12-03 | Netherlands vs USA | 0.5652 | VALUE_CANDIDATE | KNOCKOUT | 3-1 | True |
| 2022-12-03 | Argentina vs Australia | 0.5652 | VALUE_CANDIDATE | KNOCKOUT | 2-1 | True |
| 2022-12-04 | France vs Poland | 0.5652 | VALUE_CANDIDATE | KNOCKOUT | 3-1 | True |
| 2022-12-04 | England vs Senegal | 0.5652 | VALUE_CANDIDATE | KNOCKOUT | 3-0 | True |
| 2022-12-05 | Brazil vs South Korea | 0.5652 | VALUE_CANDIDATE | KNOCKOUT | 4-1 | True |
| 2022-12-06 | Portugal vs Switzerland | 0.5652 | VALUE_CANDIDATE | KNOCKOUT | 6-1 | True |
| 2022-12-10 | Morocco vs Portugal | 0.5652 | VALUE_CANDIDATE | KNOCKOUT | 1-0 | True |

### DOUBLE_CHANCE_AD · False negatives (top 10 lowest confidence, DID hit)
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2022-11-27 | Japan vs Costa Rica | 0.4926 | NO_VALUE | GROUP | 0-1 | False |
| 2022-11-27 | Spain vs Germany | 0.4926 | NO_VALUE | GROUP | 1-1 | False |
| 2022-11-25 | England vs USA | 0.5072 | FAIR_NO_EDGE | GROUP | 0-0 | False |
| 2022-11-28 | South Korea vs Ghana | 0.5072 | FAIR_NO_EDGE | GROUP | 2-3 | False |
| 2022-11-29 | Iran vs USA | 0.5173 | FAIR_NO_EDGE | GROUP | 0-1 | False |
| 2022-11-30 | Saudi Arabia vs Mexico | 0.5405 | FAIR_NO_EDGE | GROUP | 1-2 | False |
| 2022-11-29 | Wales vs England | 0.5438 | FAIR_NO_EDGE | GROUP | 0-3 | False |
| 2022-11-25 | Wales vs Iran | 0.5457 | FAIR_NO_EDGE | GROUP | 0-2 | False |
| 2022-11-26 | Tunisia vs Australia | 0.5457 | FAIR_NO_EDGE | GROUP | 0-1 | False |
| 2022-11-27 | Belgium vs Morocco | 0.5457 | FAIR_NO_EDGE | GROUP | 0-2 | False |

---
## DOUBLE_CHANCE_HA

### Configuration
- `market` = `DOUBLE_CHANCE_HA`
- `min_pred_prob_pp` = `70.0`
- `use_calibration` = `True`

### DOUBLE_CHANCE_HA · Group Stage
- n_predictions: `32`
- n_hits: `27`
- base_rate: `0.8438`
- brier_score: `0.14277` *(lower is better)*
- log_loss: `0.46281`
- calibration_label: `ACCEPTABLE_CALIBRATION`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 0 | None | None | None |
| [0.40,0.50) | 0 | None | None | None |
| [0.50,0.60) | 0 | None | None | None |
| [0.60,0.70) | 13 | 0.6799 | 0.8462 | 0.8462 |
| [0.70,0.80) | 12 | 0.7676 | 0.8333 | 0.8333 |
| [0.80,0.90) | 7 | 0.8407 | 0.8571 | 0.8571 |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_HA · Knockout
- n_predictions: `16`
- n_hits: `11`
- base_rate: `0.6875`
- brier_score: `0.23971` *(lower is better)*
- log_loss: `0.69865`
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
| [0.60,0.70) | 0 | None | None | None |
| [0.70,0.80) | 0 | None | None | None |
| [0.80,0.90) | 16 | 0.8422 | 0.6875 | 0.6875 |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_HA · Combined
- n_predictions: `48`
- n_hits: `38`
- base_rate: `0.7917`
- brier_score: `0.17508` *(lower is better)*
- log_loss: `0.54142`
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
| [0.60,0.70) | 13 | 0.6799 | 0.8462 | 0.8462 |
| [0.70,0.80) | 12 | 0.7676 | 0.8333 | 0.8333 |
| [0.80,0.90) | 23 | 0.8417 | 0.7391 | 0.7391 |
| [0.90,1.00) | 0 | None | None | None |

### DOUBLE_CHANCE_HA · Label hit-rate (Combined)
| Label | n | won | hit_rate |
|---|---|---|---|
| STRONG_VALUE | 34 | 26 | 0.7647 |
| VALUE_CANDIDATE | 1 | 1 | 1.0 |

### DOUBLE_CHANCE_HA · False positives (top 10 by confidence, did NOT hit)
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2022-12-05 | Japan vs Croatia | 0.8462 | STRONG_VALUE | KNOCKOUT | 1-1 | True |
| 2022-12-09 | Croatia vs Brazil | 0.8462 | STRONG_VALUE | KNOCKOUT | 0-0 | True |
| 2022-12-09 | Netherlands vs Argentina | 0.8462 | STRONG_VALUE | KNOCKOUT | 2-2 | True |
| 2022-12-18 | Argentina vs France | 0.8462 | STRONG_VALUE | KNOCKOUT | 2-2 | True |
| 2022-12-01 | Croatia vs Belgium | 0.8333 | STRONG_VALUE | GROUP | 0-0 | True |
| 2022-12-06 | Morocco vs Spain | 0.8333 | STRONG_VALUE | KNOCKOUT | 0-0 | True |
| 2022-11-27 | Spain vs Germany | 0.775 | STRONG_VALUE | GROUP | 1-1 | True |
| 2022-11-25 | England vs USA | 0.773 | STRONG_VALUE | GROUP | 0-0 | True |

### DOUBLE_CHANCE_HA · False negatives (top 10 lowest confidence, DID hit)
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2022-11-25 | Qatar vs Senegal | 0.66 | FAIR_NO_EDGE | GROUP | 1-3 | False |
| 2022-11-27 | Croatia vs Canada | 0.662 | FAIR_NO_EDGE | GROUP | 4-1 | False |
| 2022-11-29 | Ecuador vs Senegal | 0.663 | FAIR_NO_EDGE | GROUP | 1-2 | False |
| 2022-11-30 | Poland vs Argentina | 0.68 | FAIR_NO_EDGE | GROUP | 0-2 | False |
| 2022-11-30 | Australia vs Denmark | 0.68 | FAIR_NO_EDGE | GROUP | 1-0 | False |
| 2022-11-28 | Brazil vs Switzerland | 0.693 | FAIR_NO_EDGE | GROUP | 1-0 | False |
| 2022-11-25 | Wales vs Iran | 0.696 | FAIR_NO_EDGE | GROUP | 0-2 | False |
| 2022-11-26 | Argentina vs Mexico | 0.696 | FAIR_NO_EDGE | GROUP | 2-0 | False |
| 2022-11-26 | Tunisia vs Australia | 0.696 | FAIR_NO_EDGE | GROUP | 0-1 | False |
| 2022-11-27 | Belgium vs Morocco | 0.696 | FAIR_NO_EDGE | GROUP | 0-2 | False |

