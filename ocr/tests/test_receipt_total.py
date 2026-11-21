import unittest
from copy import deepcopy
import receipt_total
from receipt_total import select_total


def token(text, x, y, score=0.99, width=None):
    w = width or max(20, len(text)*8)
    return {'text':text, 'bbox':[[x,y],[x+w,y],[x+w,y+12],[x,y+12]], 'confidence':score, 'page':0}


def rotated(rows, turn):
    def rotate(x, y):
        return (600 - y, x) if turn == 90 else (y, 600 - x)

    result = []
    for row in rows:
        quad = [list(rotate(x, y)) for x, y in row['bbox']]
        # Native boxes can start with a short horizontal edge on sideways text.
        start = min(range(len(quad)), key=lambda i: (quad[i][1], quad[i][0]))
        result.append({**row, 'bbox': quad[start:] + quad[:start]})
    return result


def gst_invoice(units):
    return [
        token('GST TAX INVOICE', 0, 0), token('Invoice Number', 0, 24),
        token('Invoice Date', 0, 48),
        token('Description', 0, 85), token('Qty', 160, 85), token('Amount', 300, 85),
        token('Packaging materials', 0, 108), token(str(units // 100), 160, 108),
        token(str(units - 20), 300, 108),
        token('Total', 0, 131), token(str(units - 20), 300, 131),
        token('CGST', 145, 176), token(str(units // 30), 300, 176),
        token('SGST', 145, 200), token(str(units // 30), 300, 200),
        token('Total Bill Amount', 145, 224), token(str(units), 300, 224),
        token('Tendered', 145, 248), token(str(units * 2), 300, 248),
    ]


class TotalTests(unittest.TestCase):
    def test_legacy_file_and_cli_entry_points_are_absent(self):
        for name in ('extract_file', 'main'):
            with self.subTest(name=name):
                self.assertFalse(hasattr(receipt_total, name))

    def check(self, rows, expected, **kwargs):
        r=select_total(rows, **kwargs)
        self.assertEqual(r['amount_paise'], expected, r)
        return r
    def test_final_over_subtotal_tax_and_tender(self):
        rows=[token('SUB TOTAL',0,0),token('1,000.00',150,0),token('TAX TOTAL',0,20),token('100.00',150,20),token('Grand Total',0,40),token('1,100.00',150,40),token('Cash',0,60),token('2,000.00',150,60),token('Change',0,80),token('900.00',150,80),token('Qty 99',0,100)]
        self.check(rows,110000)
    def test_split_joined_and_stacked(self):
        self.check([token('Amount',0,0),token('Payable',60,0),token('Rs.',150,0),token('250.00',180,0)],25000)
        self.check([token('GRAND TOTAL',0,0),token('INR 250.00',0,18)],25000)
        self.check([token('Total: Rs. 250.00',0,0)],25000)
    def test_duplicates_and_conflicts(self):
        self.check([token('Total',0,0),token('100.00',120,0),token('Amount Due',0,20),token('100.00',120,20)],10000)
        r=self.check([token('Total',0,0),token('100.00',120,0),token('Amount Due',0,20),token('200.00',120,20)],None)
        self.assertEqual(r['reason'],'conflicting_totals')
    def test_money_and_zero(self):
        self.check([token('Total',0,0),token('12,34,567.89',120,0)],123456789)
        self.check([token('Total',0,0),token('0.00',120,0)],0)
        self.check([token('Total',0,0),token('1,23,45.00',120,0)],None)
    def test_foreign_currency(self):
        for currency in ('BDT','PKR','USD','$'):
            with self.subTest(currency=currency):
                self.assertEqual(self.check([token('Total',0,0),token(currency+' 100.00',120,0)],None)['reason'],'unsupported_currency')
    def test_threshold_and_claim(self):
        rows=[token('Total',0,0,.92),token('100.00',120,0,.92)]
        self.assertEqual(self.check(rows,None,threshold=.93)['status'],'Total not verified')
        self.assertEqual(self.check(rows,10000,threshold=.90,claim_paise=10001)['status'],'Unusual amount')
        self.assertIsNone(self.check(rows,10000,threshold=.90,claim_paise=10000)['status'])
    def test_bare_header_does_not_abort(self):
        self.check([token('Total',0,0),token('items',100,0),token('Grand Total',0,40),token('99.00',120,40)],9900)
    def test_small_box_overlap_and_negative(self):
        self.check([token('Total',0,0,width=60),token('INR 50.00',58,0)],5000)
        self.check([token('Total',0,0),token('-50.00',120,0)],None)

    def assert_selection(self, rows, reason, expected=None, **kwargs):
        result = select_total(rows, **kwargs)
        self.assertEqual(result['reason'], reason)
        self.assertTrue(result['amount_paise'] == expected, 'unexpected selected amount')
        return result

    def test_payable_with_table_totals_and_other_roles_on_same_line(self):
        units = 7 ** 3
        rows = [
            token('Page Total', 0, 0), token(str(units // 2), 180, 0),
            token('Total Qty', 0, 20), token(str(units % 10), 180, 20),
            token('Net Payable Amt', 0, 40), token('INR', 145, 40),
            token(str(units), 180, 40), token('GST', 255, 40),
            token(str(units // 5), 295, 40), token('Previous Balance', 350, 40),
            token(str(units * 2), 495, 40),
            token('Total', 0, 60), token(str(units // 4), 110, 60),
            token(str(units // 3), 180, 60),
        ]
        self.assert_selection(rows, 'verified', units * 100)

    def test_distinct_values_for_equally_authoritative_totals_abstain(self):
        units = 7 ** 3
        rows = [
            token('Net Receivable Amount', 0, 0), token(str(units), 200, 0),
            token('Grand Total', 0, 20), token(str(units + 1), 200, 20),
        ]
        self.assert_selection(rows, 'conflicting_totals')
        rows[-1] = token(str(units), 200, 20)
        self.assert_selection(rows, 'verified', units * 100)

    def test_continuation_and_threshold_preserve_abstention(self):
        units = 7 ** 3
        rows = [token('Total', 0, 0), token(str(units), 180, 0), token('Continue...', 0, 25)]
        self.assert_selection(rows, 'no_final_evidence')
        rows[0] = token('Grand Total', 0, 0, .89)
        result = self.assert_selection(rows, 'below_threshold', claim_paise=(units + 1) * 100)
        self.assertEqual(result['status'], 'Total not verified')
        rows[0] = token('Grand Total', 0, 0)
        self.assert_selection(rows, 'verified', units * 100)

    def test_compound_and_offset_labels(self):
        units = 7 ** 3
        for label in ('Total Bill Amount', 'Invoice Value', 'Total (Invoice Value)',
                      'Amount Charged', 'Amount Paid', 'NetReceivableAmt', 'Party Total Payable'):
            with self.subTest(label=label):
                rows = [token(label, 0, 0), token('INR', 175, 15), token(str(units), 210, 15)]
                self.assert_selection(rows, 'verified', units * 100)
        self.assert_selection([token(f'CGST {units // 7} Total Bill Amount (INR){units}', 0, 0)],
                              'verified', units * 100)
        self.assert_selection([token(str(units), 0, 0), token('Invoice Value', 90, 0)],
                              'verified', units * 100)

    def test_rounding_target_requires_local_support(self):
        units = 7 ** 3
        cents = 4 ** 2
        before = f'{units}.{cents:02}'
        rows = [token('Grand Total', 0, 0), token(before, 180, 0),
                token('Round Off', 0, 20), token(f'.{cents:02}', 180, 20)]
        self.assert_selection(rows, 'verified', units * 100 + cents)
        rows[2:] = [token('Roundoff to', 0, 20), token(str(units), 180, 20)]
        self.assert_selection(rows, 'verified', units * 100)
        self.assert_selection(rows[2:], 'no_final_evidence')
        rows[-1] = token(str(units + 2), 180, 20)
        self.assert_selection(rows, 'no_final_evidence')
        self.assert_selection(rows[:-1], 'no_final_evidence')

    def test_balance_quantity_and_tax_totals_cannot_be_final(self):
        units = 7 ** 3
        for label in ('Party Total', 'Running Balance Total', 'Outstanding Balance Due',
                      'Total GST', 'Total CGST', 'SGST Total', 'Table Total', 'Page Sub Total'):
            with self.subTest(label=label):
                rows = [token(label, 0, 0), token(str(units), 240, 0)]
                self.assert_selection(rows, 'no_final_evidence')

    def test_explicit_amount_after_plain_table_total(self):
        units = 7 ** 3
        self.assert_selection(gst_invoice(units), 'verified', units * 100)

    def test_sideways_gst_invoice_and_native_tokens(self):
        units = 7 ** 3
        upright = gst_invoice(units)
        for turn in (90, 270):
            with self.subTest(turn=turn):
                rows = rotated(upright, turn)
                original = deepcopy(rows)
                self.assert_selection(rows, 'verified', units * 100)
                self.assertTrue(rows == original, 'native OCR tokens changed')
                # A complete token should interpolate over the normalized text axis.
                upright_joined = upright[:-4] + [
                    token(f'SGST {units // 30} Grand Total INR {units}.07', 0, 224),
                ]
                self.assert_selection(rotated(upright_joined, turn), 'verified', units * 100 + 7)
                continued = upright[:-4] + [token('Continued...', 0, 224)]
                self.assert_selection(rotated(continued, turn), 'no_final_evidence')

    def test_sideways_page_direction_requires_evidence(self):
        units = 7 ** 3
        body = [token(text, 0, y) for text, y in (
            ('Packaging materials', 20), ('Shipping supplies', 40), ('Office supplies', 60),
        )]
        label = token('Amount Payable', 140, 100)
        left = token(str(units + 1), 40, 100)
        bare = token(str(units), 300, 100)
        currency = token(f'INR {units}', 300, 100)
        for turn in (90, 270):
            with self.subTest(turn=turn):
                self.assert_selection(rotated(body + [left, label, bare], turn), 'ambiguous_orientation')
                self.assert_selection(rotated(body + [left, label, currency], turn), 'verified', units * 100)
                self.assert_selection(rotated(body + [label, bare], turn), 'verified', units * 100)

    def test_orientation_is_per_page_and_ignores_isolated_vertical_text(self):
        units = 7 ** 3
        upright = [token('Invoice Number', 0, 0), token('Shipping supplies', 0, 25),
                   token('Grand Total', 0, 60), token(str(units), 120, 60)]
        logo = rotated([token('Vertical letterhead', 0, 0)], 90)
        self.assert_selection(upright + logo, 'verified', units * 100)
        other_page = [{**row, 'page': 1} for row in rotated(gst_invoice(units), 90)]
        self.assert_selection(upright + other_page, 'verified', units * 100)
        other_page = [{**row, 'page': 1} for row in rotated(gst_invoice(units + 1), 270)]
        self.assert_selection(upright + other_page, 'conflicting_totals')

    def test_complete_explicit_token_owns_its_amount(self):
        units = 7 ** 3
        rows = [token(f'Total Bill Amount INR {units}', 0, 0),
                token(f'INR {units + 1}', 245, 0)]
        self.assert_selection(rows, 'verified', units * 100)
        rows.append(token(f'Amount Paid INR {units + 1}', 0, 25))
        self.assert_selection(rows, 'conflicting_totals')

if __name__ == '__main__': unittest.main()
