# RapidOCR evaluation: exact INR receipt totals

[Per-case v2 results](evaluation.json) and the [package rerun](evaluation-package-rerun.json) record all cases, including failures to verify and both controls. These were end-to-end CPU runs, not replays of OCR tokens. The JSON files are redacted: the original receipts, transcriptions, and expected amounts are not in the repository, so external readers cannot independently rescore those cases.

| Run | Complete receipts | Exact final total | Wrong total accepted | Unverified | Missing-final-page controls unverified |
| --- | ---: | ---: | ---: | ---: | ---: |
| v1 before tuning on this set | 10 | 3 | 0 | 7 | 2/2 |
| **v2 after tuning on the same set** | **10** | **5** | **0** | **5** | **2/2** |
| Packaged v2, clean dependency install and fresh model download, same inputs | 10 | 5 | 0 | 5 | 2/2 |

The complete receipts comprised six scans of printed bills and four phone photos of paper receipts. Expected final totals were read visually before OCR. Each CLI invocation processed an image and its result was compared in integer paise against its expected final payable amount. Merely reading the right numeral elsewhere on the receipt did not count. A missing amount counted as unverified. The confidence cutoff was 0.90, the per-image timeout was 90 seconds, and the median v2 wall time on complete receipts was 15.53 seconds. The two incomplete inputs were scored separately as controls.

V2 reached five exact matches but did **not** meet the 9-of-10, zero-wrong gate. V2 was tuned on this same cohort, so it is development evidence, not an unseen holdout or a reliability estimate. Neither broad coverage on 100–150+ diverse digital and physical receipts nor throughput under load was established. A possible follow-up is to train an XGBoost ranker on 200+ labeled receipts to choose among candidates read by RapidOCR, then validate it on a separate merchant/layout split. No ranker was trained in this project.

## Reproduce the bundled example

The [redacted receipt example](examples/demo-receipt.png) is separate from the 10-receipt evaluation. The original photo is not distributed. Run it through the same CLI without the app or any cached result:

```sh
python word_total.py examples/demo-receipt.png --timeout 90
```

Use the Python executable from the [setup instructions](README.md). The visible ground truth is ₹777.00. The packaged RapidOCR CLI returned `reason: verified`, `currency: INR`, and `amount_paise: 77700` on that exact PNG. Approvals shows this recorded result for valid requests with the same receipt SHA-256 and displays **Recorded OCR result · no live OCR**. The benchmark originals are not included because redistribution permission was not confirmed for all source images.
