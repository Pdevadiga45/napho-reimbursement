import contextlib
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from word_total import extract_image, main, select_word_total


def line(y, *entries, page=0):
    words = []
    for entry in entries:
        x, text, *score = entry
        right = x + max(8, 8 * len(text))
        words.append(dict(text=text, confidence=score[0] if score else 0.99,
                          bbox=[[x, y], [right, y], [right, y + 12], [x, y + 12]]))
    left, right = words[0]['bbox'][0][0], words[-1]['bbox'][1][0]
    return dict(text=' '.join(word['text'] for word in words), confidence=0.99,
                bbox=[[left, y], [right, y], [right, y + 12], [left, y + 12]],
                page=page, words=words)


def rotate(line_data, turns):
    def quad(points):
        for _ in range(turns):
            points = [[600 - y, x] for x, y in points]
        return points

    return [dict(row, bbox=quad(row['bbox']),
                 words=[dict(word, bbox=quad(word['bbox'])) for word in row['words']])
            for row in line_data]


def slope(line_data, gradient):
    def quad(points):
        return [[x, y + gradient * x] for x, y in points]

    return [dict(row, bbox=quad(row['bbox']),
                 words=[dict(word, bbox=quad(word['bbox'])) for word in row['words']])
            for row in line_data]


class WordTotalTests(unittest.TestCase):
    def assert_unverified(self, result, reason=None):
        self.assertEqual(result['status'], 'Total not verified')
        self.assertIsNone(result['amount_paise'])
        self.assertNotIn('evidence', result)
        if reason:
            self.assertEqual(result['reason'], reason)

    def test_rotated_merged_line_preserves_original_word_polygons(self):
        merged = line(82, (10, 'Subtotal'), (95, '100.00'), (190, 'Grand'),
                      (237, 'Total:'), (330, '₹'), (340, '118.50'),
                      (415, 'Change'), (480, '0.50'))
        for turns in range(4):
            with self.subTest(turns=turns):
                row = rotate([merged], turns)[0]
                result = select_word_total([row])
                self.assertEqual(result['amount_paise'], 11850)
                self.assertEqual(result['evidence']['label_quads'],
                                 [word['bbox'] for word in row['words'][2:4]])
                self.assertEqual(result['evidence']['amount_quads'],
                                 [word['bbox'] for word in row['words'][4:6]])
                self.assertNotIn('Grand', json.dumps(result))

        for gradient in (-0.11, 0.11):
            for turns in (0, 1, 3):
                with self.subTest(gradient=gradient, turns=turns):
                    rows = rotate(slope([
                        line(20, (10, 'Item'), (128, 'Qty'), (310, 'Amount')),
                        line(45, (10, 'Lamp'), (128, '1'), (322, '118.50')),
                        line(65, (10, 'Payment'), (119, 'details')),
                        line(100, (10, 'Total (Incl. Tax):')),
                        line(100, (322, '118.50')),
                        line(128, (10, 'GST'), (330, '18.50')),
                    ], gradient), turns)
                    outlier = rotate(slope([line(155, (10, 'Bank'), (225, 'details'))], -gradient), turns)
                    rows.extend(outlier)
                    result = select_word_total(rows)
                    self.assertEqual(result['amount_paise'], 11850)
                    self.assertEqual(result['evidence']['label_quads'], [rows[3]['words'][0]['bbox']])
                    self.assertEqual(result['evidence']['amount_quads'], [rows[4]['words'][0]['bbox']])
                    self.assert_unverified(select_word_total(rows[:3] + rows[4:]))

    def test_separate_amount_uses_word_row_over_merged_detection_box(self):
        header = line(20, (10, 'Item'), (122, 'Qty'), (224, 'Amount'))
        subtotal = line(90, (10, 'Item'), (54, 'Subtotal'), (224, '2,990.00'))
        label = line(125, (10, 'Total'), (60, 'Payable'))
        label['bbox'] = [[0, 16], [200, 16], [200, 165], [0, 165]]
        amount = line(128, (242, '345.67'))
        impostor = line(111, (155, '500.00'))
        result = select_word_total([impostor, header, subtotal, label, amount])
        self.assertEqual(result['amount_paise'], 34567)
        self.assertEqual(result['evidence']['amount_quads'], [amount['words'][0]['bbox']])

    def test_explicit_net_labels_match_the_full_phrase(self):
        for name in ('Net Receivable Amount', 'Net Receivable Amt', 'Net Payable Amt'):
            with self.subTest(name=name):
                row = line(70, *zip((10, 45, 146), name.split()), (220, '345.67'))
                result = select_word_total([row])
                self.assertEqual(result['amount_paise'], 34567)
                self.assertEqual(result['evidence']['label_quads'],
                                 [word['bbox'] for word in row['words'][:-1]])
        for name in ('Amount', 'Amt'):
            with self.subTest(name=name):
                self.assert_unverified(select_word_total([
                    line(70, (10, 'Net'), (45, name), (220, '345.67'))]), 'no_final_evidence')

        for name in ('Grand Total(Round):', 'Total Bill (rounded off)', 'Total (Incl. Tax)',
                     'Total Amount after Tax', 'Total Invoice Value'):
            with self.subTest(full_label=name):
                entries = [(10, name), (235, '345.67'), (318, 'Tax Amount'), (425, '18.00')]
                split = []
                x = 10
                for word in name.replace('(', ' ( ').replace(')', ' ) ').split():
                    split.append((x, word))
                    x += 8 * len(word) + 3
                for fields in (entries, split + entries[1:]):
                    row = line(70, *fields)
                    result = select_word_total([row])
                    self.assertEqual(result['amount_paise'], 34567)
                    self.assertEqual(result['evidence']['label_quads'],
                                     [word['bbox'] for word in row['words'][:-3]])
                self.assert_unverified(select_word_total([
                    line(70, (10, 'See'), (75, name), (320, '345.67'))]))
                self.assert_unverified(select_word_total([
                    line(70, (10, 'See')), line(70, (75, name), (320, '345.67'))]))
                separate = [line(70, (10, name)), line(70, (240, '345.67'))]
                self.assertEqual(select_word_total(separate)['amount_paise'], 34567)
                self.assertEqual(select_word_total([
                    *separate, line(70, (318, 'Bank Account')), line(70, (425, '123456'))
                ])['amount_paise'], 34567)
                self.assert_unverified(select_word_total([
                    line(70, (10, name)), line(70, (318, 'Tax Amount')), line(70, (425, '18.00'))]))
                self.assert_unverified(select_word_total([
                    line(70, (10, name), (318, 'Bank Account'), (425, '123456'))]))

        for name in ('Grand Total Before Tax', 'Total Amount Before Tax', 'Total Tax Amount',
                     'Total Quantity', 'Total Qty', 'Total Invoice Value Before Tax'):
            with self.subTest(nonfinal=name):
                self.assert_unverified(select_word_total([line(70, (10, name), (260, '345.67'))]))
        self.assert_unverified(select_word_total([
            line(70, (10, 'Description'), (170, 'Grand'), (225, 'Total:'), (320, '345.67'))]))
        self.assert_unverified(select_word_total([
            line(70, (10, 'Item'), (170, 'Qty'), (225, 'Total Invoice Value')),
            line(94, (10, 'Lamp'), (170, '1'), (340, '345.67'))]))

    def test_left_amount_requires_standalone_unowned_unambiguous_evidence(self):
        label = line(70, (200, 'Grand'), (250, 'Total'))
        left = line(77, (65, '345.67'))
        for turns in (0, 1):
            with self.subTest(turns=turns):
                final, amount = rotate([label, left], turns)
                result = select_word_total([final, amount])
                self.assertEqual(result['amount_paise'], 34567)
                self.assertEqual(result['evidence']['amount_quads'], [amount['words'][0]['bbox']])

        for case, rows in (
            ('ambiguous', [label, left, line(70, (125, '456.78'))]),
            ('owned by tax', [label, left, line(77, (10, 'GST'))]),
            ('intervening field', [label, left, line(70, (135, 'Cash'))]),
            ('amount in other text', [label, line(77, (10, 'Tea'), (65, '345.67'))]),
            ('label in other text', [line(70, (125, 'Payment'), (200, 'Grand'), (250, 'Total')), left]),
            ('off row', [label, line(78, (65, '345.67'))]),
            ('too far', [line(70, (402, 'Grand'), (452, 'Total')), left]),
        ):
            with self.subTest(case=case):
                self.assert_unverified(select_word_total(rows), 'no_final_evidence')

        right = line(70, (310, '456.78'))
        result = select_word_total([label, left, right])
        self.assertEqual(result['amount_paise'], 45678)
        self.assertEqual(result['evidence']['amount_quads'], [right['words'][0]['bbox']])

    def test_equal_explicit_and_arithmetic_backed_conflicts_abstain(self):
        grand = line(114, (10, 'Grand'), (59, 'Total'), (235, '120.00'))
        bill = line(140, (10, 'Bill'), (59, 'Total'), (235, '121.00'))
        for score in (0.99, 0.72):
            bill['words'][-1]['confidence'] = score
            self.assert_unverified(select_word_total([grand, bill]), 'conflicting_totals')
        subtotal = line(30, (10, 'Subtotal'), (235, '100.00'))
        tax = line(55, (10, 'GST'), (243, '18.00'))
        generic = line(80, (10, 'Total'), (235, '118.00'))
        self.assertEqual(select_word_total([subtotal, tax, generic])['amount_paise'], 11800)
        self.assert_unverified(select_word_total([subtotal, tax, generic, grand]),
                               'conflicting_totals')

    def test_terminal_total_after_tax_requires_a_clean_footer(self):
        item = line(30, (10, 'Lamp'), (140, '2'), (220, '90.00'), (315, '180.00'))
        tax = line(55, (10, 'CGST'), (323, '9.00'))
        total = line(80, (10, 'Total'), (315, '198.00'))
        cash = line(105, (10, 'Tendered'), (315, '200.00'))
        for turns in (0, 3):
            with self.subTest(turns=turns):
                rows = rotate([cash, total, item, tax], turns)
                result = select_word_total(rows)
                self.assertEqual(result['amount_paise'], 19800)
                self.assertEqual(result['evidence']['support'][0]['amount_quads'],
                                 [rows[-1]['words'][-1]['bbox']])
        for later in (line(105, (10, 'SGST'), (323, '9.00')),
                      line(105, (10, 'Tape'), (315, '12.00')),
                      line(105, (10, 'Net Amount'), (315, '201.00')),
                      line(105, (10, 'Item Total'), (315, '201.00')),
                      line(105, (10, 'Round Off'), (315, '1.00')),
                      line(105, (10, 'SGST')),
                      line(105, (10, 'Brought Forward')),
                      line(105, (10, 'Continued On Next Page'))):
            with self.subTest(later=later['text']):
                self.assert_unverified(select_word_total([item, tax, total, later]))
        self.assert_unverified(select_word_total([item, total, cash]))
        subtotal = line(40, (10, 'Subtotal'), (315, '180.00'))
        self.assert_unverified(select_word_total([subtotal, tax, total]))
        tax['words'][-1]['confidence'] = 0.899
        self.assert_unverified(select_word_total([item, tax, total]), 'below_threshold')

    def test_subtotal_and_tax_arithmetic_survives_a_printed_rate(self):
        rows = [line(30, (10, 'Taxable Value'), (315, '180.00')),
                line(55, (10, 'CGST'), (60, '@'), (80, '5%'), (323, '9.00')),
                line(80, (10, 'SGST 5%'), (323, '9.00')),
                line(105, (10, 'Total'), (315, '198.00'))]
        result = select_word_total(rows)
        self.assertEqual(result['amount_paise'], 19800)
        self.assertEqual(len(result['evidence']['support']), 3)
        self.assert_unverified(select_word_total([*rows, line(130, (10, 'Lamp'), (315, '198.00'))]))

        split = [line(y, field) for y, label, value in (
            (30, 'Subtotal', '180.00'), (55, 'GST', '18.00'), (80, 'Total', '198.00'))
                 for field in ((10, label), (315, value))]
        self.assertEqual(select_word_total(split)['amount_paise'], 19800)

        for source, tax, adjustment in (('180.104', '17.90', '-0.004'),
                                        ('180.10', '17.896', '+0.004'),
                                        ('180.10', '17.896', '₹+0.004')):
            with self.subTest(signed_rounding=adjustment):
                rows = [line(30, (10, 'Subtotal'), (355 - 8 * len(source), source)),
                        line(55, (10, 'Tax Amount'), (355 - 8 * len(tax), tax)),
                        line(80, (10, 'Round Off'), (355 - 8 * len(adjustment), adjustment)),
                        line(105, (10, 'Total'), (307, '198.00'))]
                result = select_word_total(rows)
                self.assertEqual(result['amount_paise'], 19800)
                self.assertEqual(len(result['evidence']['support']), 3)
                self.assert_unverified(select_word_total(rows[:-1]))
                self.assert_unverified(select_word_total([*rows, line(130, (10, 'Lamp'), (307, '198.00'))]))
                self.assert_unverified(select_word_total([*rows[:-1], line(105, (10, 'Total'), (299, '198.000'))]))
        for tax, adjustment in (('17.896', '0.004'), ('17.896', '±0.004'),
                                ('18.501', '-0.601'), ('17.299', '+0.601')):
            with self.subTest(rejected_rounding=adjustment):
                self.assert_unverified(select_word_total([
                    line(30, (10, 'Subtotal'), (307, '180.10')),
                    line(55, (10, 'Tax Amount'), (307, tax)),
                    line(80, (10, 'Round Off'), (299, adjustment)),
                    line(105, (10, 'Total'), (307, '198.00'))]))

    def test_bare_total_needs_exact_words_or_final_payment(self):
        body = line(20, (10, 'Lamp'), (135, '2'), (220, '86.40'), (310, '172.80'))
        total = line(50, (10, 'Total'), (310, '172.80'))
        words = line(75, (10, 'Amount in Words:'), (150, 'One Hundred Seventy Two'),
                     (338, 'Rupees and Eighty Paise Only'))
        paid = line(75, (10, 'Amount Paid'), (310, '172.80'))
        for support in (words, paid):
            with self.subTest(support=support['text']):
                result = select_word_total([body, total, support])
                self.assertEqual(result['amount_paise'], 17280)
                self.assertEqual(len(result['evidence']['support']), 1)
                self.assertNotIn(support['text'], json.dumps(result))
        for support in (line(75, (10, 'Amount in Words'), (145, 'One Hundred Seventy Two Only')),
                        line(75, (10, 'Amount Paid'), (310, '173.00')),
                        line(75, (10, 'Cash Tendered'), (310, '172.80')),
                        line(75, (10, 'Change'), (310, '172.80')),
                        line(75, (10, 'Balance'), (310, '172.80'))):
            with self.subTest(support=support['text']):
                self.assert_unverified(select_word_total([body, total, support]))
        self.assert_unverified(select_word_total([body, total, paid,
                                                 line(100, (10, 'Lamp'), (310, '11.00'))]))
        self.assert_unverified(select_word_total([
            line(20, (10, 'Invoice No.'), (310, '123456')), total, paid]))
        rupees = line(75, (10, 'Rupees'), (76, 'One Hundred Seventy Two and Eighty Paise Only'))
        self.assertEqual(select_word_total([body, total, rupees])['amount_paise'], 17280)

        tail = [line(120, (10, 'Taxable Value'), (150, 'Tax Amount')),
                line(143, (18, '150.00'), (175, '22.80')),
                line(165, (10, 'Bank Account'), (190, '123456789'))]
        spelled = line(96, (10, 'One Hundred Seventy Two Rupees and Eighty Paise Only'))
        for name in ('Amount Chargeable (in words):', 'Total Invoice Amount (in words)'):
            with self.subTest(payable_words=name):
                heading = line(73, (10, name))
                for support in ([heading, spelled, line(96, (490, '123456'))],
                                [line(75, (10, name), (290, spelled['text']), (740, '123456'))]):
                    result = select_word_total([body, total, *support, *tail])
                    self.assertEqual(result['amount_paise'], 17280)
                    self.assertEqual(result['evidence']['amount_quads'], [total['words'][-1]['bbox']])
                    self.assertEqual(len(result['evidence']['support'][0]['amount_quads']), 1)
                    grand = line(189, (10, 'Grand Total'), (310, '173.00'))
                    self.assert_unverified(select_word_total([body, total, *support, *tail, grand]),
                                           'conflicting_totals')
                support = [heading, spelled]
                for competing in (line(50, (410, '172.80')), line(68, (10, 'Total'), (310, '172.80'))):
                    self.assert_unverified(select_word_total([body, total, competing, *support, *tail]))
                unmarked = line(50, (10, 'Total'), (135, '2'), (310, '172.80'))
                self.assertEqual(select_word_total([body, unmarked, *support, *tail])['amount_paise'], 17280)
                self.assert_unverified(select_word_total([body, unmarked, *tail]))
                self.assert_unverified(select_word_total([body, total, line(96, (490, '172.80')), *tail]))
        for name in ('Tax Amount (in words)', 'Total Tax (in words)', 'CGST Amount in Words'):
            self.assert_unverified(select_word_total([
                body, total, line(75, (10, name), (230, spelled['text'])), *tail]))
        payable = line(75, (10, 'Total Invoice Amount (in words)'), (290, spelled['text']))
        self.assert_unverified(select_word_total([body, total, payable, line(102, (10, 'Tape'), (310, '11.00'))]))
        tax_heading = line(0, (10, 'Taxable Value'), (294, 'Tax Amount'))
        self.assert_unverified(select_word_total([tax_heading, body, total, payable]))
        bad_words = line(75, (10, 'Total Invoice Amount (in words)'), (290, spelled['text']), (710, '172.80'))
        self.assert_unverified(select_word_total([body, total, bad_words, *tail]))

        tax = line(30, (10, 'CGST'), (318, '22.80'))
        for annotation in (((135, '2'), (154, 'Nos')), ((130, 'Qty:'), (174, '2'))):
            with self.subTest(quantity=annotation):
                annotated = line(50, (10, 'Total'), *annotation, (310, '172.80'))
                self.assertEqual(select_word_total([tax, annotated])['amount_paise'], 17280)
                self.assert_unverified(select_word_total([tax, annotated, line(50, (390, '173.80'))]))

    def test_named_document_amount_accepts_only_a_standalone_unique_field(self):
        for name in ('Invoice Value', 'Bill Amount'):
            with self.subTest(name=name):
                label = line(70, (155, name))
                left = line(72, (72, 'INR339.7'))
                merged = line(70, (72, 'INR339.7'), (155, name))
                right = line(70, (155, name), (300, '₹339.7'))
                for rows in ([label, left], [merged], [right]):
                    result = select_word_total(rows)
                    self.assertEqual(result['amount_paise'], 33970)
                self.assert_unverified(select_word_total([merged, line(70, (300, '420.00'))]))
                for prefix in ('Subtotal', 'Pre-tax', 'Tax', 'Item', 'Previous Balance', 'Invoice No.'):
                    self.assert_unverified(select_word_total([
                        line(70, (10, prefix), (155, name), (300, '339.70'))]))
                    self.assert_unverified(select_word_total([right, line(73, (10, prefix))]))
                self.assert_unverified(select_word_total([right, line(48, (155, 'Previous Balance'))]))
                for bad_left in ('24/09/26', '2026-09-24', 'INV339', '20260924', '₹339.70x'):
                    self.assert_unverified(select_word_total([line(70, (55, bad_left), (155, name))]))

    def test_stacked_payable_needs_alignment_and_no_line_items(self):
        heading = line(70, (218, 'Amount Payable'))
        amount = line(94, (268, '₹436.20'))
        for turns in (0, 1, 2, 3):
            with self.subTest(turns=turns):
                # This row provides native word order for the rotated column.
                rows = rotate([line(22, (10, 'Thank'), (52, 'you')), heading, amount], turns)
                self.assertEqual(select_word_total(rows)['amount_paise'], 43620)
        for rows in ([heading, line(94, (95, '₹436.20'))],
                     [heading, line(128, (268, '₹436.20'))],
                     [heading, amount, line(94, (208, '80.00'))],
                     [heading, amount, line(94, (10, 'Lamp'))],
                     [heading, amount, line(118, (268, '436.21'))],
                     [heading, amount, line(118, (10, 'Continued On Next Page'))]):
            self.assert_unverified(select_word_total([line(22, (10, 'Thank'), (52, 'you')), *rows]))

    def test_payable_column_can_share_a_footer_with_aligned_tax_and_subtotal(self):
        headings = line(70, (22, 'Subtotal'), (160, 'GST'), (260, 'Bill Amount'))
        values = line(94, (38, '400.00'), (144, '72.00'), (300, '472.00'))
        result = select_word_total([values, headings])
        self.assertEqual(result['amount_paise'], 47200)
        self.assertEqual(len(result['evidence']['support']), 2)
        item_headings = line(70, (22, 'Description'), (160, 'Qty'), (260, 'Bill Amount'))
        self.assert_unverified(select_word_total([item_headings, values]))
        continuing = line(118, (22, 'Tape'), (160, '2'), (300, '472.00'))
        self.assert_unverified(select_word_total([headings, values, continuing]))

    def test_roundoff_to_is_a_target_with_a_neighboring_literal_pre_round_value(self):
        before = line(50, (10, 'Total'), (260, 'Rs.146.5'))
        for target in (line(75, (10, 'Roundoff To'), (276, '₹147')),
                       line(75, (276, '147'), (330, 'Round Off To')),
                       line(50, (10, 'Total'), (130, '146.5'), (195, 'Roundoff To'), (326, '₹147'))):
            with self.subTest(target=target['text']):
                rows = [before, target] if target['bbox'][0][1] > 50 else [target]
                self.assertEqual(select_word_total(rows)['amount_paise'], 14700)
        for target in (line(75, (10, 'Round Off'), (276, '147')),
                       line(75, (10, 'Roundoff To'), (276, '146')),
                       line(75, (10, 'Roundoff To'), (170, '147')),
                       line(115, (10, 'Roundoff To'), (276, '147')),
                       line(75, (10, 'Roundoff To'), (276, '₹147x'))):
            self.assert_unverified(select_word_total([before, target]))
        target = line(75, (10, 'Roundoff To'), (276, '147'))
        self.assert_unverified(select_word_total([target]))
        self.assert_unverified(select_word_total([line(50, (10, 'GST'), (260, 'Rs.146.5')), target]))
        self.assert_unverified(select_word_total([before, target, line(100, (10, 'GST'), (276, '5.00'))]))
        amount_only = line(50, (270, '146.5'))
        result = select_word_total([amount_only, target])
        self.assertEqual(result['amount_paise'], 14700)
        self.assertEqual(result['evidence']['support'], [{'amount_quads': [amount_only['words'][0]['bbox']]}])
        before['words'][-1]['confidence'] = 0.89
        self.assert_unverified(select_word_total([before, target]), 'below_threshold')

    def test_literal_single_fraction_digit_and_currency_marker(self):
        for literal in ('10.3', '₹10.3', 'INR10.3', 'Rs.10.3', '10.3INR'):
            with self.subTest(literal=literal):
                result = select_word_total([line(70, (10, 'Amount Due'), (160, literal))])
                self.assertEqual(result['amount_paise'], 1030)
        for literal in ('INR10.33x', 'ID₹10.3', '₹10.333', 'Rs.10.3/0', '₹10.3₹'):
            with self.subTest(literal=literal):
                self.assert_unverified(select_word_total([line(70, (10, 'Amount Due'), (160, literal))]))

        for literal in ('1,23,456=00', '₹1,23,456—00', 'Rs.123456=00'):
            with self.subTest(legacy=literal):
                result = select_word_total([line(70, (10, 'Amount Due'), (160, literal))])
                self.assertEqual(result['amount_paise'], 12345600)
                self.assertEqual(result['threshold'], 0.90)
        for literal in ('1,234,567=00', '123,456=00', '12,34=00', '123—0', '123=7O', '123—000',
                        '123-00', '123.00=00'):
            with self.subTest(ambiguous=literal):
                self.assert_unverified(select_word_total([line(70, (10, 'Amount Due'), (160, literal))]))
        for tail in ('=00', '—00'):
            self.assert_unverified(select_word_total([line(70, (10, 'Amount Due'), (160, '123'), (187, tail))]))

    def test_continued_table_and_lone_net_amount_abstain(self):
        rows = [line(20, (12, 'Item'), (96, 'Qty'), (171, 'Rate'), (260, 'Amount')),
                line(50, (12, 'Widgets'), (105, '2'), (171, '38.00'), (268, '76.00')),
                line(70, (12, 'Pens'), (105, '1'), (171, '22.00'), (268, '22.00')),
                line(92, (12, 'Total'), (105, '3'), (268, '98.00')),
                line(117, (12, 'Net'), (52, 'Amt'), (268, '98.00')),
                line(141, (12, 'Continued'), (98, 'on'), (125, 'next'), (170, 'page'))]
        self.assert_unverified(select_word_total(rows))
        self.assert_unverified(select_word_total(rows[-2:-1]))
        final = line(124, (12, 'Grand'), (60, 'Total'), (268, '98.00'))
        self.assert_unverified(select_word_total(rows + [final]))

    def test_unsupported_currency_and_word_confidence_abstain(self):
        final = line(70, (10, 'Grand'), (60, 'Total'), (150, '₹530.00'))
        foreign = line(40, (10, 'Cash'), (150, 'USD'), (182, '600.00'))
        self.assert_unverified(select_word_total([foreign, final]), 'unsupported_currency')
        for weak_index in (0, 2):
            words = [dict(word) for word in final['words']]
            words[weak_index]['confidence'] = 0.8999
            self.assert_unverified(select_word_total([dict(final, words=words)]),
                                   'below_threshold')

    def test_strict_money_and_exact_paise_claim_projection(self):
        for bad in ('12,34.50', '123.456', '8.00%', '-10.00', '10.00x', '10.'):
            with self.subTest(bad=bad):
                self.assert_unverified(select_word_total([
                    line(70, (10, 'Amount'), (72, 'Due'), (160, bad))]))
        for mark in ('.5x', ',', '%'):
            with self.subTest(mark=mark):
                self.assert_unverified(select_word_total([
                    line(70, (10, 'Amount'), (72, 'Due'), (160, '10'), (193, mark))]))
        row = line(70, (10, 'Amount'), (72, 'Due'), (146, 'INR'), (174, '1,23,456.'),
                   (246, '07'))
        exact = select_word_total([row], claim_paise=12345607)
        self.assertEqual(exact['amount_paise'], 12345607)
        self.assertIsNone(exact['status'])
        over = select_word_total([row], claim_paise=12345608)
        self.assertEqual(over['amount_paise'], 12345607)
        self.assertEqual(over['currency'], 'INR')
        self.assertEqual(over['status'], 'Unusual amount')
        self.assertEqual(over['message'], 'Requested amount exceeds the receipt total.')

    def test_rounding_needs_both_literal_amounts_and_agreement(self):
        before = line(70, (10, 'Grand'), (60, 'Total'), (160, '189.50'))
        rounded = line(95, (10, 'Rounded'), (75, 'Total'), (160, '190.00'))
        self.assertEqual(select_word_total([before, rounded])['amount_paise'], 19000)
        self.assert_unverified(select_word_total([rounded]))
        wrong = line(95, (10, 'Rounded'), (75, 'Total'), (160, '189.00'))
        self.assert_unverified(select_word_total([before, wrong]), 'conflicting_totals')

    def test_adjustment_column_is_not_a_competing_total(self):
        headers = line(30, (10, 'Description'), (225, 'Amount'), (300, 'Adjustment'))
        final = line(90, (10, 'Grand'), (60, 'Total'), (225, '535.00'), (348, '1.00'))
        result = select_word_total([headers, final])
        self.assertEqual(result['amount_paise'], 53500)

    def test_contradictory_native_directions_abstain(self):
        final = line(70, (10, 'Grand'), (60, 'Total'), (160, '189.50'))
        other = rotate([line(105, (10, 'Payment'), (100, 'details'), (168, 'below'))], 2)[0]
        self.assert_unverified(select_word_total([final, other]), 'ambiguous_orientation')
        competing = rotate([line(105, (10, 'Bill'), (60, 'Total'), (160, '199.50'))], 2)[0]
        prose = [line(y, (10, 'Payment'), (100, 'details'), (168, 'below')) for y in (20, 45)]
        self.assert_unverified(select_word_total([final, competing, *prose]), 'ambiguous_orientation')

    def test_cli_adapter_requests_native_words(self):
        row = line(70, (10, 'Grand'), (60, 'Total'), (160, '189.50'))
        result = SimpleNamespace(txts=[row['text']], scores=[row['confidence']],
                                 boxes=[SimpleNamespace(tolist=lambda: row['bbox'])],
                                 word_results=[[(word['text'], word['confidence'], word['bbox'])
                                                for word in row['words']]])
        with patch('word_total._local_image', return_value=Path('test.png')):
            with patch('receipt_total.create_engine') as create:
                create.return_value.return_value = result
                extracted = extract_image('test.png')
                create.return_value.assert_called_once_with('test.png', return_word_box=True)
        self.assertEqual(extracted['amount_paise'], 18950)
        self.assertEqual(extracted['evidence']['amount_quads'], [row['words'][-1]['bbox']])

    def test_cli_packing_and_invalid_local_file(self):
        result = select_word_total([line(70, (10, 'Grand'), (60, 'Total'), (160, '189.50'))])
        out = io.StringIO()
        with patch('word_total._extract_supervised', return_value=result) as extract:
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(['test.png', '--claim-paise', '20000', '--threshold', '.95']), 0)
        extract.assert_called_once_with('test.png', 0.95, 20000, 60.0)
        self.assertEqual(json.loads(out.getvalue()), result)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(['missing-image.invalid']), 1)
        self.assert_unverified(json.loads(out.getvalue()), 'invalid_image')

    def test_cli_timeout_kills_and_reaps_a_running_worker(self):
        out, err = io.StringIO(), io.StringIO()
        processes = []
        popen = subprocess.Popen

        def start(*args, **kwargs):
            child = popen(*args, **kwargs)
            processes.append(child)
            return child

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            marker = Path(directory) / 'started'
            script = ('from pathlib import Path; import sys, time; '
                      'Path(sys.argv[1]).touch(); print("private log", file=sys.stderr, flush=True); '
                      'time.sleep(10)')
            command = [sys.executable, '-I', '-B', '-c', script, str(marker)]
            with patch('word_total._worker_command', return_value=command, create=True), \
                    patch('subprocess.Popen', side_effect=start), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                began = time.monotonic()
                try:
                    code = main(['private-path.png', '--timeout', '0.65'])
                except SystemExit as exc:
                    code = exc.code
                elapsed = time.monotonic() - began
            self.assertTrue(marker.exists(), 'worker never began running')
        self.assertEqual(code, 1)
        self.assertLess(elapsed, 3)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].returncode)
        self.assert_unverified(json.loads(out.getvalue()), 'timeout')
        self.assertEqual(err.getvalue(), '')

    def test_cli_rejects_failed_and_invalid_worker_results_without_logs(self):
        scripts = ('import sys; print("private log", file=sys.stderr); sys.exit(7)',
                   'print("private OCR text, invalid JSON")',
                   'print(\'{"reason":"verified","message":"private receipt text"}\')')
        for script in scripts:
            with self.subTest(script=script):
                command = [sys.executable, '-I', '-B', '-c', script]
                out, err = io.StringIO(), io.StringIO()
                with patch('word_total._worker_command', return_value=command, create=True), \
                        contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    self.assertEqual(main(['private-path.png']), 1)
                self.assert_unverified(json.loads(out.getvalue()), 'runtime_failure')
                self.assertEqual(err.getvalue(), '')

    def test_worker_result_pipe_excludes_python_and_native_logs(self):
        result = select_word_total([line(70, (10, 'Grand'), (60, 'Total'), (160, '189.50'))],
                                   claim_paise=20000)
        script = (
            'import json, os, sys; sys.path.insert(0, sys.argv[1]); import word_total\n'
            'def extract(*args):\n'
            '    print("private Python log", flush=True)\n'
            '    os.write(1, b"private native log")\n'
            '    print("private stderr log", file=sys.stderr, flush=True)\n'
            '    return json.loads(sys.argv[2])\n'
            'word_total.extract_image = extract\n'
            'word_total._worker_main("private-path.png", "0.90", "20000")\n'
        )
        command = [sys.executable, '-I', '-B', '-c', script, str(Path.cwd()), json.dumps(result)]
        out, err = io.StringIO(), io.StringIO()
        with patch('word_total._worker_command', return_value=command), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(['private-path.png', '--claim-paise', '20000']), 0)
        self.assertEqual(json.loads(out.getvalue()), result)
        self.assertEqual(err.getvalue(), '')

    def test_cli_invalid_timeout_is_a_sanitized_option_error(self):
        for timeout in ('0', '-1', 'nan', 'inf', 'private-option-text'):
            out, err = io.StringIO(), io.StringIO()
            with self.subTest(timeout=timeout), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as error:
                    main(['private-path.png', '--timeout', timeout])
                self.assertEqual(error.exception.code, 2)
                self.assert_unverified(json.loads(out.getvalue()), 'invalid_options')
                self.assertEqual(err.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
