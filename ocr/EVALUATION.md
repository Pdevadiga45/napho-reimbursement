# Receipt OCR proof of concept

RapidOCR reads the text and word positions in a receipt image on the CPU. A rule-based Python selector parses amounts and uses their position on the page to match them to labels like “Grand Total” or “Amount Payable,” rather than picking a subtotal, tax, or change. If the evidence is ambiguous or confidence is too low, it leaves the total unverified.

The prototype correctly extracted the final INR total in the [five examples shown here](evaluation.json). These are selected examples, not an accuracy estimate. The prototype has not been validated on 100–150+ diverse digital and physical receipts or load-tested for scale, so it is not ready for production use.

To try it, follow the [local setup instructions](README.md). From `ocr/`, run this with the virtual environment’s Python. The [bundled redacted receipt](examples/demo-receipt.png) should return `amount_paise: 77700` (₹777.00).

```sh
python word_total.py examples/demo-receipt.png
```
