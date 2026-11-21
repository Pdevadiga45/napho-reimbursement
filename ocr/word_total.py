"""Select a literal INR receipt total from native RapidOCR word boxes."""

import argparse
import json
import math
import os
import re
import stat
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from statistics import median


QUALIFIED_FINALS = (
    'grand total round', 'grand total rounded', 'grand total rounded off',
    'total bill round', 'total bill rounded', 'total bill rounded off',
    'total incl tax', 'total incl gst', 'total including tax', 'total including gst',
    'total inclusive of tax', 'total amount incl tax', 'total amount incl gst', 'total amount including tax',
    'total after tax', 'total amount after tax', 'total invoice value',
)
ROUNDING_NAMES = ('round off', 'rounding', 'roundoff', 'rounding adjustment', 'round off adjustment')
WEAK_WORD_LABELS = {'rupees', 'in words', 'rupees in words'}
BARE_TOTALS = {'total', 'total amount'}
ROLE_NAMES = {
    'final': ('grand total', 'grandtotal', 'bill total', 'total bill amount', 'final total',
              'amount payable', 'total payable', 'total amount payable', 'net payable',
              'net payable amt', 'net amount payable', 'net receivable amount', 'net receivable amt',
              'amount due', 'total due', 'total amount due', 'net due', *QUALIFIED_FINALS),
    'document': ('invoice value', 'bill amount'),
    'generic': ('total', 'total amount', 'invoice total', 'invoice amount',
                'net total', 'net amount', 'net amt'),
    'rounded': ('rounded total', 'rounded amount', 'rounded off total', 'total rounded',
                'total rounded off', 'rounded payable', 'net rounded amount'),
    'round_target': ('roundoff to', 'round off to'),
    'words': ('amount in words', 'total in words', 'rupees in words', 'in words', 'rupees',
              'amount chargeable in words', 'total invoice amount in words',
              'amount payable in words', 'total amount in words'),
    'subtotal': ('subtotal', 'sub total', 'item subtotal', 'items subtotal', 'taxable amount',
                 'taxable value', 'total taxable value', 'total before tax', 'pre tax total', 'pre tax',
                 'total amount before tax', 'grand total before tax', 'total invoice value before tax',
                 'total bill before tax', 'total excl tax', 'total excluding tax', 'total excl gst',
                 'total amount excl tax', 'total amount excluding tax', 'total before gst',
                 'total amount before gst', 'grand total before gst'),
    'tax': ('tax', 'total tax', 'tax total', 'tax amount', 'gst', 'total gst', 'gst amount',
            'vat', 'sales tax', 'total tax amount', 'total gst amount'),
    'tax_component': ('cgst', 'sgst', 'igst', 'utgst', 'cess', 'cgst amount', 'sgst amount',
                      'igst amount', 'utgst amount', 'cess amount'),
    'fee': ('service charge', 'delivery fee', 'shipping charge', 'shipping fee', 'packing charge'),
    'discount': ('discount', 'total discount', 'coupon discount'),
    'paid': ('amount paid', 'total paid', 'payment received'),
    'excluded': ('item total', 'items total', 'line total', 'total qty', 'total quantity',
                 'total qty nos', 'total quantity nos', 'total quantity in nos',
                 'total no of items', 'total items', 'total savings', 'previous balance', 'opening balance',
                 'balance brought forward', 'balance', 'cash', 'cash tendered', 'cash received',
                 'cash paid', 'tendered', 'change', 'change due', 'cashback', 'refund'),
    'adjustment': ('adjustment', 'adjustments', *ROUNDING_NAMES),
    'column': ('amount', 'qty', 'quantity', 'rate', 'unit price', 'price', 'description',
               'particulars', 'item', 'items'),
    'metadata': ('bank account', 'bank account no', 'bank account number', 'account no',
                 'account number', 'ifsc', 'ifsc code'),
}
ROLES = {name: role for role, names in ROLE_NAMES.items() for name in names}
ROLES.update({f'{name} in words': 'excluded'
              for role in ('tax', 'tax_component', 'subtotal') for name in ROLE_NAMES[role]})
MAX_LABEL_WORDS = max(len(name.split()) for name in ROLES)
LABEL_PUNCTUATION = str.maketrans('():.-–', '      ')
CONTINUED = re.compile(r'\b(?:continued (?:on|to)|to be continued|carried forward|carry forward|'
                       r'brought forward|carryover|carry over)\b')
FOREIGN = re.compile(
    r'[$€£¥₩₽₺₱฿₫₦₴₪₨৳₭₮₡₲₵₸]|(?<![A-Z])(?:USD|EUR|GBP|JPY|CAD|AUD|NZD|SGD|HKD|'
    r'CNY|RMB|CHF|AED|SAR|MYR|THB|PKR|NPR|LKR|BDT|KRW|RUB|ZAR|SEK|NOK|DKK)(?![A-Z])',
    re.IGNORECASE,
)
CURRENCY = r'(?:₹|INR\.?|RS\.)|RS(?![A-Z])'
MONEY = re.compile(
    rf'(?:(?P<prefix>{CURRENCY}))?'
    r'(?P<whole>[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,2}(?:,[0-9]{2})+,[0-9]{3})'
    rf'(?:\.(?P<fraction>[0-9]{{1,6}}))?(?:(?P<suffix>{CURRENCY})|(?P<rupees>/-))?',
    re.IGNORECASE,
)
LEGACY_MONEY = re.compile(
    rf'(?P<whole>(?:{CURRENCY})?(?:[0-9]+|[0-9]{{1,2}}(?:,[0-9]{{2}})*,[0-9]{{3}}))'
    rf'[=—]00(?P<suffix>{CURRENCY}|/-)?',
    re.IGNORECASE,
)
SIGN = r'[+\-−]'
SIGNED = re.compile(rf'(?P<currency>{CURRENCY})?(?P<sign>{SIGN})(?P<value>.+)', re.IGNORECASE)
CURRENCY_MARKER = re.compile(CURRENCY, re.IGNORECASE)
PREFIX = re.compile(rf'(?:{CURRENCY}|[+\-−–(])', re.IGNORECASE)
SUFFIX = re.compile(rf'(?:{CURRENCY}|[%)+\-−–]|/-)', re.IGNORECASE)
NUMBER_FRAGMENT = re.compile(r'[0-9,.]+')
NUMBER_TAIL = re.compile(r'^(?:[,.%)/]|[+\-−–—=](?:[0-9]|$))')
SEPARATORS = {':', '=', '|', '·'}
TAX_RATE = re.compile(r'@?\(?[0-9]{1,2}(?:\.[0-9]{1,2})?%\)?')
TAX_LABEL_WITH_RATE = re.compile(rf'(.+?)\s+{TAX_RATE.pattern}')
METADATA = re.compile(r'^(?:(?:invoice|bill|receipt|order)\s+(?:date|no\b|number|id\b)|'
                      r'(?:gstin|pan|phone|mobile|date|time|page)\b)')
BANK_DETAILS = re.compile(r'^(?:bank\b|(?:beneficiary|account)\s+(?:name|no\b|number)|ifsc\b)')
QUANTITY = re.compile(r'(?:[0-9]+ (?:nos?|pcs?|units?)|(?:qty|quantity) [0-9]+)')
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
MAX_IMAGE_SIDE = 12_000
DEFAULT_TIMEOUT_SECONDS = 60.0
TAX_ROLES = {'tax', 'tax_component'}
SETTLEMENT_NAMES = {*ROLE_NAMES['paid'], 'cash', 'cash tendered', 'cash received', 'cash paid',
                    'tendered', 'change', 'change due', 'cashback', 'refund', 'balance'}
SUMMARY_COMPONENTS = TAX_ROLES | {'subtotal', 'fee', 'discount'}
TOTAL_ROLES = {'final', 'generic', 'document'}


@dataclass(frozen=True)
class Word:
    text: str
    confidence: float
    quad: tuple


@dataclass(frozen=True)
class Span:
    row: int
    start: int
    end: int
    words: tuple
    bounds: tuple

    @property
    def center_y(self):
        return (self.bounds[1] + self.bounds[3]) / 2

    @property
    def height(self):
        return self.bounds[3] - self.bounds[1]


@dataclass(frozen=True)
class Label:
    span: Span
    name: str

    @property
    def role(self):
        return ROLES[self.name]


@dataclass(frozen=True)
class Amount:
    span: Span
    paise: int | Fraction
    literal: bool = True
    signed: bool = False


@dataclass(frozen=True)
class Link:
    label: Label | None
    amount: Amount


@dataclass(frozen=True)
class Total:
    link: Link
    rank: int
    support: tuple = ()

    @property
    def confidence(self):
        return min(word.confidence for link in (self.link, *self.support)
                   for span in (link.amount.span, *((link.label.span,) if link.label else ()))
                   for word in span.words)


def _unverified(threshold, reason):
    return dict(amount_paise=None, currency=None, confidence=0.0, threshold=threshold,
                status='Total not verified', reason=reason)


def _validate_options(threshold, claim_paise):
    if not 0 <= threshold <= 1:
        raise ValueError('threshold must be in [0, 1]')
    if claim_paise is not None and (type(claim_paise) is not int or claim_paise < 0):
        raise ValueError('claim_paise must be a nonnegative integer')


def _read_word(data):
    quad = tuple((float(x), float(y)) for x, y in data['bbox'])
    if len(quad) != 4 or not all(math.isfinite(value) for point in quad for value in point):
        raise ValueError('invalid word polygon')
    confidence = float(data['confidence'])
    if not 0 <= confidence <= 1:
        raise ValueError('invalid word confidence')
    return Word(data['text'].strip(), confidence, quad)


def _bounds(points):
    x, y = zip(*points)
    return min(x), min(y), max(x), max(y)


def _center(bounds):
    left, top, right, bottom = bounds
    return (left + right) / 2, (top + bottom) / 2


def _direction(rows):
    # The ordered endpoint centers vote only when their displacement is long and axial.
    votes = defaultdict(list)
    for words in rows:
        if len(words) < 2:
            continue
        first, last = (_bounds(word.quad) for word in (words[0], words[-1]))
        x0, y0 = _center(first)
        x1, y1 = _center(last)
        dx, dy = x1 - x0, y1 - y0
        axis = int(abs(dy) > abs(dx))
        along, across = (dy, dx) if axis else (dx, dy)
        thickness = max(first[3 - axis] - first[1 - axis], last[3 - axis] - last[1 - axis])
        if abs(along) >= max(8, 2.25 * thickness) and abs(across) <= 0.35 * abs(along):
            votes[axis, 1 if along > 0 else -1].append((-across if axis else across) / along)
    if not votes:
        # A fully split upright footer has no ordered word pairs to vote.
        names = {' '.join(_label_text(word.text) for word in words) for words in rows}
        footer_names = {*BARE_TOTALS, *ROLE_NAMES['document'], *ROLE_NAMES['round_target'],
                        *QUALIFIED_FINALS,
                        *(name for name in ROLE_NAMES['final'] if 'payable' in name)}
        if names & footer_names:
            boxes = [_bounds(word.quad) for words in rows for word in words]
            if (any(right - left >= 2 * (bottom - top) > 0 for left, top, right, bottom in boxes)
                    and not any(bottom - top >= 2 * (right - left) > 0 for left, top, right, bottom in boxes)):
                return 0, 1, 0
        return None
    direction = max(votes, key=lambda key: len(votes[key]))
    slopes = votes[direction]
    if len(slopes) < 3 * (sum(map(len, votes.values())) - len(slopes)):
        return None
    slope = median(slopes)
    inliers = [value for value in slopes if abs(value - slope) <= 0.025]
    if len(inliers) < 3 or len(inliers) < 3 * (len(slopes) - len(inliers)) or abs(slope) < 0.015:
        return *direction, 0
    return *direction, median(inliers)


def _project(words, direction):
    axis, sign, slope = direction

    def point(x, y):
        along = sign * (y if axis else x)
        return along, sign * (-x if axis else y) - slope * along

    boxes = tuple(_bounds(point(x, y) for x, y in word.quad) for word in words)
    if any(right <= left or bottom <= top for left, top, right, bottom in boxes):
        return ()
    centers = [_center(box) for box in boxes]
    if len(centers) > 1:
        dx, dy = (b - a for a, b in zip(centers[0], centers[-1]))
        if dx <= 0 or abs(dy) > dx or any(b[0] <= a[0] for a, b in zip(centers, centers[1:])):
            return ()
    return boxes


def _span(row, start, end, words, boxes):
    left, top, right, bottom = zip(*boxes[start:end])
    return Span(row, start, end, words[start:end], (min(left), min(top), max(right), max(bottom)))


def _label_text(text):
    return ' '.join(text.casefold().translate(LABEL_PUNCTUATION).split())


def _labels(row, words, boxes):
    names = [_label_text(word.text) for word in words]
    index = 0
    while index < len(names):
        if not names[index]:
            index += 1
            continue
        for end in range(min(len(names), index + 2 * MAX_LABEL_WORDS), index, -1):
            name = ' '.join(filter(None, names[index:end]))
            rate = TAX_LABEL_WITH_RATE.fullmatch(' '.join(word.text.casefold() for word in words[index:end]))
            if rate and ROLES.get(rate[1]) in TAX_ROLES:
                name = rate[1]
            if (name in {'total qty', 'total quantity'} and names[index] == 'total'
                    and not _adjacent(boxes[index], boxes[index + 1], gap=1.5)):
                continue
            if name in ROLES:
                yield Label(_span(row, index, end, words, boxes), name)
                index = end
                break
        else:
            index += 1


def _money_match(value):
    legacy = LEGACY_MONEY.fullmatch(value)
    if legacy:
        value = f"{legacy['whole']}.00{legacy['suffix'] or ''}"
    match = MONEY.fullmatch(value)
    if match is not None and not (match['prefix'] and match['suffix']):
        return match
    return None


def _parse_paise(value, precise=False):
    match = _money_match(value)
    if match is None:
        return None
    whole = match['whole'].replace(',', '')
    if len(whole) > 12:
        return None
    fraction = match['fraction'] or ''
    if len(fraction) <= 2:
        return int(whole) * 100 + int(fraction.ljust(2, '0'))
    if precise:
        return int(whole) * 100 + Fraction(int(fraction), 10 ** (len(fraction) - 2))


def _adjacent(left_box, right_box, gap=1):
    height = min(left_box[3] - left_box[1], right_box[3] - right_box[1])
    return (right_box[0] - left_box[2] <= gap * height
            and abs(_center(left_box)[1] - _center(right_box)[1]) <= height / 2)


def _money_join(left, right, left_box, right_box):
    if not left or not right or not _adjacent(left_box, right_box):
        return False
    if re.search(r'[0-9]', left) and SUFFIX.fullmatch(right):
        return True
    if PREFIX.fullmatch(left) and re.search(r'[0-9]', right):
        return True
    if (CURRENCY_MARKER.fullmatch(left) and re.fullmatch(SIGN, right)
            or re.fullmatch(SIGN, left) and CURRENCY_MARKER.fullmatch(right)):
        return True
    return ((left[-1] in ',.' or right[0] in ',.')
            and NUMBER_FRAGMENT.fullmatch(left) and NUMBER_FRAGMENT.fullmatch(right))


def _amounts(row, words, boxes):
    index = 0
    while index < len(words):
        end = index + 1
        value = words[index].text
        while end < len(words) and _money_join(words[end - 1].text, words[end].text,
                                               boxes[end - 1], boxes[end]):
            value += words[end].text
            end += 1
        cut_off = (end < len(words) and NUMBER_TAIL.match(words[end].text)
                   and _adjacent(boxes[end - 1], boxes[end], gap=2))
        if not cut_off:
            span = _span(row, index, end, words, boxes)
            paise = _parse_paise(value)
            if paise is not None:
                yield Amount(span, paise)
            else:
                signed = SIGNED.fullmatch(value)
                magnitude = (signed['currency'] or '') + signed['value'] if signed else value
                paise = _parse_paise(magnitude, precise=True)
                if paise is not None:
                    if signed and signed['sign'] != '+':
                        paise = -paise
                    yield Amount(span, paise, literal=False, signed=bool(signed))
        index = end


def _same_row(left, right, tolerance=0.45):
    return abs(left.center_y - right.center_y) <= tolerance * min(left.height, right.height)


def _lies_right(label, amount, tolerance):
    return (_same_row(label, amount, tolerance) and
            amount.bounds[0] >= label.bounds[2] - 0.2 * min(label.height, amount.height))


def _quantity_spans(row, words, boxes):
    for start, word in enumerate(words):
        if QUANTITY.fullmatch(_label_text(word.text)):
            yield _span(row, start, start + 1, words, boxes)
        elif start + 1 < len(words) and _adjacent(boxes[start], boxes[start + 1]):
            text = _label_text(f'{word.text} {words[start + 1].text}')
            if QUANTITY.fullmatch(text):
                yield _span(row, start, start + 2, words, boxes)


def _contains(field, span):
    return field.row == span.row and field.start <= span.start and span.end <= field.end


def _links(words_by_row, cells, labels, amounts, quantities):
    labels_by_row = defaultdict(list)
    amount_words = defaultdict(set)
    for label in labels:
        labels_by_row[label.span.row].append(label)
    for amount in amounts:
        span = amount.span
        amount_words[span.row].update(range(span.start, span.end))
    quantity_words = defaultdict(set)
    for span in quantities:
        quantity_words[span.row].update(range(span.start, span.end))

    def clear(row, start, end, role=None):
        remaining = [words_by_row[row][index].text for index in range(start, end)
                     if index not in amount_words[row] and words_by_row[row][index].text not in SEPARATORS
                     and (role != 'generic' or index not in quantity_words[row])]
        return not remaining or (role in TAX_ROLES and TAX_RATE.fullmatch(''.join(remaining)))

    def starts_field(label):
        span = label.span
        if span.start == 0 or all(word.text in SEPARATORS for word in words_by_row[span.row][:span.start]):
            return True
        return (span.start - 1 in amount_words[span.row]
                and any(first.span.start == 0 and first.span.end < span.start
                        and first.role not in {'column', 'excluded', 'words'}
                        and clear(span.row, first.span.end, span.start, first.role)
                        for first in labels_by_row[span.row]))

    for amount in amounts:
        span = amount.span
        preceding = [label for label in labels_by_row[span.row] if label.span.end <= span.start
                     and not any(_contains(field, label.span) for field in quantities)]
        if preceding:
            label = preceding[-1]
            tolerance = 0.8
        elif clear(span.row, 0, span.start):
            neighbors = [label for label in labels if label.span.row != span.row
                         and _lies_right(label.span, span, 0.45)
                         and all(word.text in SEPARATORS for word in words_by_row[label.span.row][label.span.end:])]
            if not neighbors:
                continue
            label = max(neighbors, key=lambda item: item.span.bounds[2])
            tolerance = 0.45
            # A separate native cell between the field and value can be an intervening column.
            if any(cell.row not in {label.span.row, span.row}
                   and _same_row(label.span, cell)
                   and label.span.bounds[2] <= cell.bounds[0] < span.bounds[0]
                   and cell.bounds[2] <= span.bounds[0]
                   and any(char.isalpha() for word in cell.words for char in word.text)
                   and cell not in quantities
                   for cell in cells):
                continue
        else:
            continue
        if (starts_field(label) and (label.span.row != span.row
                                     or clear(span.row, label.span.end, span.start, label.role))
                and _lies_right(label.span, span, tolerance)):
            yield Link(label, amount)


def _lies_left(label, amount):
    gap = label.bounds[0] - amount.bounds[2]
    return 0 <= gap <= 24 * min(label.height, amount.height) and _same_row(label, amount, 0.65)


def _left_links(words_by_row, labels, amounts, right_links):
    def standalone(span):
        return span.start == 0 and span.end == len(words_by_row[span.row])

    right_labels = {link.label for link in right_links}
    owned_amounts = {link.amount for link in right_links}
    standalone_amounts = [amount for amount in amounts if amount.literal and standalone(amount.span)]
    for label in labels:
        span = label.span
        if label.role != 'final' or label in right_labels or not standalone(span):
            continue
        row_labels = [other.span for other in labels if other is not label
                      and _same_row(span, other.span, 0.65)]
        candidates = [amount for amount in standalone_amounts
                      if amount not in owned_amounts and _lies_left(span, amount.span)
                      and not any(field.bounds[0] < span.bounds[0]
                                  and field.bounds[2] > amount.span.bounds[2]
                                  for field in row_labels)]
        if len(candidates) == 1:
            amount = candidates[0]
            owned_amounts.add(amount)
            yield Link(label, amount)


def _adjustment_headers(labels, links):
    used = {link.label for link in links}
    for adjustment in labels:
        if adjustment.role not in {'adjustment', 'discount'} or adjustment in used:
            continue
        for heading in labels:
            if heading.name not in {'amount', 'net amount', 'line total'} or heading in used:
                continue
            if _same_row(heading.span, adjustment.span) and heading.span.bounds[2] <= adjustment.span.bounds[0]:
                yield heading.span, adjustment.span


def _under_header(amount, heading):
    left, _, right, _ = heading.bounds
    return abs(amount.span.bounds[2] - right) <= max(1.5 * amount.span.height, (right - left) / 2)


def _aligned(first, second):
    return abs(first.amount.span.bounds[2] - second.amount.span.bounds[2]) <= max(
        first.amount.span.height, second.amount.span.height)


def _same_column(first, second):
    return min(abs(first.bounds[2] - second.bounds[2]),
               abs(_center(first.bounds)[0] - _center(second.bounds)[0])) <= 0.75 * min(first.height, second.height)


def _on_row(first, second):
    return first.row == second.row or _same_row(first, second, 0.65)


def _final_context(link, cells, labels, links):
    span = link.label.span
    for cell in cells:
        if (cell.row == span.row or not _on_row(cell, span) or cell.bounds[2] > span.bounds[0]
                or not any(char.isalpha() for word in cell.words for char in word.text)):
            continue
        heading = any(label.span.row == cell.row and label.role in SUMMARY_COMPONENTS | {'column', 'excluded'}
                      for label in labels)
        if (heading or span.bounds[0] - cell.bounds[2] <= 4 * span.height) and not any(
                other.label.span.row == cell.row and other.amount.span.bounds[2] <= span.bounds[0]
                for other in links):
            return False
    return True


def _clean_row(cells, anchor, *fields, tax_rate_between=None):
    covered = {(span.row, index) for span in fields for index in range(span.start, span.end)}
    extra = [(cell.row, index, word.text) for cell in cells if _on_row(cell, anchor)
             for index, word in enumerate(cell.words)
             if (cell.row, index) not in covered and word.text not in SEPARATORS]
    if not extra:
        return True
    if tax_rate_between:
        label, amount = tax_rate_between
        return (all(row == label.row and label.end <= index < amount.start for row, index, _ in extra)
                and TAX_RATE.fullmatch(''.join(text for _, _, text in extra)) is not None)
    return False


def _only_field(cells, link, *extra):
    label, amount = link.label.span, link.amount.span
    rate = (label, amount) if link.label.role in TAX_ROLES and label.row == amount.row else None
    return _on_row(label, amount) and _clean_row(cells, label, label, amount, *extra, tax_rate_between=rate)


def _next_row(cells, upper, lower, gap=3):
    return (0.75 * upper.height <= lower.center_y - upper.center_y <= gap * upper.height
            and not any(upper.center_y < cell.center_y < lower.center_y
                        and not _on_row(cell, upper) and not _on_row(cell, lower) for cell in cells))


def _money_shape(amount):
    match = _money_match(''.join(word.text for word in amount.span.words))
    return match is not None and (any(match[group] for group in ('fraction', 'prefix', 'suffix', 'rupees'))
                                  or ',' in match['whole'])


def _settlement_amounts(cells, links):
    return {link.amount for link in links if link.amount.literal and link.label.name in SETTLEMENT_NAMES
            and _only_field(cells, link)}


def _stacked_links(cells, label, labels, amounts, settled):
    heading = label.span
    headings = [other for other in labels if _on_row(heading, other.span)]
    if (any(other != label and other.role not in SUMMARY_COMPONENTS for other in headings)
            or not _clean_row(cells, heading, *(other.span for other in headings))):
        return

    for amount in amounts:
        value = amount.span
        if (not amount.literal or not _same_column(heading, value)
                or value.bounds[0] < heading.bounds[0] - value.height
                or not _next_row(cells, heading, value)):
            continue
        row_amounts = [other for other in amounts if _on_row(value, other.span)]
        if (len(row_amounts) != len(headings)
                or not _clean_row(cells, value, *(other.span for other in row_amounts))
                or any(other.span.center_y > value.center_y and not _on_row(other.span, value)
                       and other not in settled for other in amounts)):
            continue
        columns = [[other for other in row_amounts if _same_column(column.span, other.span)]
                   for column in headings]
        if any(len(column) != 1 for column in columns) or len({column[0] for column in columns}) != len(columns):
            continue
        support = tuple(Link(column, match[0]) for column, match in zip(headings, columns) if column != label)
        yield Link(label, amount), support


def _document_links(cells, labels, amounts, links):
    settled = _settlement_amounts(cells, links)
    for label in labels:
        span = label.span
        if label.role == 'document':
            headed = any(other.role in SUMMARY_COMPONENTS | {'excluded', 'column'}
                         and _next_row(cells, other.span, span, gap=2.5)
                         and abs(other.span.bounds[0] - span.bounds[0]) <= span.height
                         and _clean_row(cells, other.span, other.span) for other in labels)
            if headed:
                continue
            for amount in amounts:
                if not amount.literal:
                    continue
                right = (_lies_right(span, amount.span, 0.65)
                         and amount.span.bounds[0] - span.bounds[2] <= 24 * span.height)
                left = _lies_left(span, amount.span) and _money_shape(amount)
                link = Link(label, amount)
                if (right or left) and _only_field(cells, link):
                    yield link, ()

        payable = label.role == 'document' or label.role == 'final' and 'payable' in label.name
        if payable:
            yield from _stacked_links(cells, label, labels, amounts, settled)


SMALL_NUMBERS = {word: value for value, word in enumerate(
    'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen '
    'fifteen sixteen seventeen eighteen nineteen'.split())}
TENS = dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20, 100, 10)))
SCALES = {'thousand': 1_000, 'lakh': 100_000, 'million': 1_000_000,
          'crore': 10_000_000, 'billion': 1_000_000_000}


def _small_number(words):
    if not words:
        return None
    hundreds = 0
    if len(words) >= 2 and words[1] == 'hundred' and 0 < SMALL_NUMBERS.get(words[0], 0) < 10:
        hundreds = SMALL_NUMBERS[words[0]] * 100
        words = words[2:]
        if not words:
            return hundreds
        if words[0] == 'and' and len(words) > 1:
            words = words[1:]
    if len(words) == 1:
        value = SMALL_NUMBERS.get(words[0], TENS.get(words[0]))
        return hundreds + value if value is not None else None
    if len(words) == 2 and words[0] in TENS and 0 < SMALL_NUMBERS.get(words[1], 0) < 10:
        return hundreds + TENS[words[0]] + SMALL_NUMBERS[words[1]]
    return None


def _number_in_words(words):
    total, start, limit = 0, 0, 10 ** 12
    for index, word in enumerate(words):
        scale = SCALES.get(word.rstrip('s'))
        if scale:
            group = _small_number(words[start:index])
            if not group or scale >= limit or group * scale >= limit:
                return None
            total += group * scale
            start, limit = index + 1, scale
    if start == len(words):
        return total or None
    tail = words[start:]
    group = _small_number(tail[1:] if total and tail[0] == 'and' else tail)
    return total + group if group is not None else None


def _words_paise(text):
    tokens = text.casefold().replace('-', ' ').strip(' :.,').split()
    if len(tokens) > 48 or not tokens:
        return None
    if tokens[-1] == 'only':
        tokens.pop()
    rupees = {'rupee', 'rupees', 'inr', 'rs', 'rs.'}
    if tokens and tokens[0] in rupees:
        tokens = tokens[1:]
    split = next((i for i, word in enumerate(tokens) if word in rupees), len(tokens))
    if split == len(tokens) and tokens and tokens[-1] in {'paisa', 'paise'}:
        # A leading rupee marker can introduce both denominations.
        split = next((i for i, word in enumerate(tokens) if word == 'and'
                      and _small_number(tokens[i + 1:-1]) is not None), len(tokens))
    whole = _number_in_words(tokens[:split])
    tail = tokens[split + 1:]
    if not tail:
        return whole * 100 if whole is not None else None
    if tail[0] == 'and':
        tail = tail[1:]
    fraction = _small_number(tail[:-1])
    if whole is not None and tail and tail[-1] in {'paisa', 'paise'} and fraction is not None and fraction < 100:
        return whole * 100 + fraction
    return None


def _words_row_clean(cells, anchor, rightmost, fields, amounts):
    detached = [amount.span for amount in amounts if _on_row(anchor, amount.span)
                and amount.span.bounds[0] - rightmost.bounds[2] >= 1.5 * rightmost.height]
    return _clean_row(cells, anchor, *fields, *detached)


def _word_amounts(rows, boxes_by_row, labels, cells, amounts):
    for label in labels:
        if label.role != 'words' or label.span.start != 0:
            continue
        span = label.span
        for cell in cells:
            start = span.end if cell.row == span.row else 0
            right = _on_row(cell, span) and cell.bounds[0] >= span.bounds[2] and start == 0
            below = _next_row(cells, span, cell) and abs(cell.bounds[0] - span.bounds[0]) <= 2 * span.height
            if cell.row != span.row and not (right or below):
                continue
            words = rows[cell.row]
            while start < len(words) and words[start].text in SEPARATORS:
                start += 1
            ends = {len(words), *(other.span.start for other in amounts if other.span.row == cell.row)}
            for end in sorted(ends, reverse=True):
                if end <= start:
                    continue
                paise = _words_paise(' '.join(word.text for word in words[start:end]))
                if paise is None:
                    continue
                amount = _span(cell.row, start, end, words, boxes_by_row[cell.row])
                fields = (span, amount)
                if (_words_row_clean(cells, span, amount if _on_row(span, amount) else span, fields, amounts)
                        and _words_row_clean(cells, amount, amount, fields, amounts)):
                    yield Link(label, Amount(amount, paise))


def _item_amount(amount, cells):
    span = amount.span
    other_text = ' '.join(word.text for cell in cells if _on_row(span, cell)
                          for index, word in enumerate(cell.words)
                          if cell.row != span.row or not span.start <= index < span.end)
    return any(char.isalpha() for char in other_text) and not METADATA.match(other_text.casefold())


def _near_words(link, words):
    span = link.amount.span
    return [other for other in words
            if -2 * span.height <= other.label.span.center_y - span.center_y <= 5 * span.height
            and abs(other.label.span.bounds[0] - link.label.span.bounds[0]) <= 4 * span.height]


def _secondary_total(value, labels, amounts):
    span = value.span
    return any(0.75 * span.height <= span.center_y - label.span.center_y <= 8 * span.height
               and label.role in TAX_ROLES | {'subtotal'} and _under_header(value, label.span)
               and not any(_on_row(label.span, other.span) for other in amounts) for label in labels)


def _later_details(link, words, primary, amounts, cells, labels):
    end = max(link.amount.span.center_y, words.amount.span.center_y, words.label.span.center_y)
    later = [amount for amount in amounts if amount.span.center_y > end + 0.75 * link.amount.span.height]
    if not later:
        return True
    allowed = _settlement_amounts(cells, primary)
    allowed.update(other.amount for other in primary if other.label.role in {'final', 'document'})
    detail_rows = [label.span for label in labels if label.role in TAX_ROLES | {'subtotal'}]
    detail_rows.extend(cell for cell in cells if BANK_DETAILS.match(
        ' '.join(_label_text(word.text) for word in cell.words)))
    return all(amount in allowed or any(end < row.center_y <= amount.span.center_y + 0.45 * row.height
                                        for row in detail_rows) for amount in later)


def _words_summary(link, primary, amounts, cells, labels, quantities, word_amounts):
    nearby = _near_words(link, word_amounts)
    if len(nearby) != 1 or nearby[0].label.name in WEAK_WORD_LABELS:
        return None
    words = nearby[0]
    value = link.amount
    if words.amount.paise != value.paise:
        return None
    if any(other != link and other.label.role == 'generic' and other.amount.literal
           and other.amount.paise == value.paise and words in _near_words(other, word_amounts)
           for other in primary):
        return None

    span = value.span
    row_amounts = [other for other in amounts if _on_row(span, other.span) and other != value]
    if any(other.paise == value.paise or _money_shape(other) or not other.literal
           or other.span.bounds[2] > span.bounds[0] for other in row_amounts):
        return None
    if not _only_field(cells, link, *quantities, *(other.span for other in row_amounts)):
        return None
    if _secondary_total(value, labels, amounts) or not _later_details(link, words, primary, amounts, cells, labels):
        return None
    first, last = sorted((span, words.label.span), key=lambda field: field.center_y)
    if any(first.center_y < cell.center_y < last.center_y
           and not _on_row(first, cell) and not _on_row(last, cell)
           and not _on_row(words.amount.span, cell) for cell in cells):
        return None
    return Total(link, 2, (words,))


def _bare_summary(link, primary, amounts, single_cells, cells, labels, quantities, word_amounts):
    span = link.amount.span
    if not _only_field(cells, link, *quantities):
        return None
    consistent = _summary(link, primary, amounts, single_cells, rounding=True)
    if consistent and (not any(part.label.role in TAX_ROLES | {'paid'} for part in consistent.support)
                       or not all(_only_field(cells, part) for part in consistent.support)):
        consistent = None
    if consistent and any(other.role == 'adjustment'
                          and consistent.support[0].label.span.center_y < other.span.center_y < span.center_y
                          and all(other != part.label for part in consistent.support) for other in labels):
        consistent = None
    if consistent and consistent.support[-1].amount.signed:
        chain = [part.amount.span for part in consistent.support] + [span]
        if not all(_next_row(cells, upper, lower) for upper, lower in zip(chain, chain[1:])):
            consistent = None
    settled = _settlement_amounts(cells, primary)
    later = {other for other in amounts if other.span.center_y - span.center_y > 0.75 * span.height}
    if consistent:
        # Independently supported totals remain visible to the conflict check.
        explicit = {other.amount for other in primary if other.label.role in {'final', 'document'}}
        if later - settled - explicit:
            return None
    elif later - settled:
        return None
    elif any(other.role in TAX_ROLES | TOTAL_ROLES | {'subtotal', 'round_target', 'rounded'}
             and other.span.center_y - span.center_y > 0.75 * span.height for other in labels):
        return None

    words = _near_words(link, word_amounts)
    paid = [other for other in primary if other.label.name in {'amount paid', 'total paid'}
            and other.amount in settled and _aligned(other, link)
            and 0.75 * span.height <= other.amount.span.center_y - span.center_y <= 5 * span.height]
    support = words + paid
    if any(other.amount.paise != link.amount.paise for other in support):
        return None
    if consistent:
        return consistent
    if words and (any(other.label.name not in WEAK_WORD_LABELS for other in words)
                  or _secondary_total(link.amount, labels, amounts)):
        support = paid

    preceding = [other for other in primary if other.amount in single_cells and _aligned(other, link)
                 and 0.75 * span.height <= span.center_y - other.amount.span.center_y <= 12 * span.height]
    if any(other.label.role == 'subtotal' for other in preceding):
        return None
    taxes = [other for other in preceding
             if other.amount.literal and other.label.role in TAX_ROLES and _only_field(cells, other)]
    if len({part.label.name for part in taxes}) != len(taxes) or len({part.label.role for part in taxes}) > 1:
        return None
    if taxes:
        last_tax = max(taxes, key=lambda other: other.amount.span.center_y)
        if _next_row(cells, last_tax.amount.span, span):
            return Total(link, 2, tuple(support or taxes))
    body = any(0.75 * span.height <= span.center_y - other.span.center_y <= 12 * span.height
               and not any(bound.amount == other for bound in primary)
               and _aligned(Link(None, other), link) and _item_amount(other, cells) for other in amounts)
    if support and len(words) <= 1 and len(paid) <= 1 and (body or taxes):
        return Total(link, 2, tuple(support))
    return None


def _summary(link, links, amounts, single_cells, rounding=False):
    label = link.label.span
    earlier = sorted((other for other in links if
                      0.75 * label.height <= label.center_y - other.label.span.center_y <= 12 * label.height
                      and _aligned(other, link)), key=lambda other: other.label.span.center_y)
    boundaries = [index for index, other in enumerate(earlier)
                  if other.label.role in TOTAL_ROLES | {'subtotal', 'rounded', 'round_target'}]
    if not boundaries or earlier[boundaries[-1]].label.role != 'subtotal':
        return None
    parts = earlier[boundaries[-1]:]
    rows = [part.label.span.center_y for part in parts]
    component_roles = {'tax', 'tax_component', 'fee', 'discount'}
    taxes = {part.label.role for part in parts} & TAX_ROLES
    adjustment = parts[-1]
    has_rounding = (rounding and taxes and adjustment.label.name in ROUNDING_NAMES
                    and adjustment.amount.signed and 0 < abs(adjustment.amount.paise) <= 50
                    and all(part.label.role in TAX_ROLES for part in parts[1:-1]))
    if has_rounding:
        component_roles.add('adjustment')
    if (len(parts) > 1 and len(rows) == len(set(rows)) and len(taxes) <= 1
            and len(parts) == len({part.label.name for part in parts})
            and all(part.label.role in component_roles for part in parts[1:])
            and (all(part.amount.literal for part in parts)
                 or has_rounding and not any(part.amount.signed for part in parts[:-1]))
            and all(part.amount in single_cells for part in parts)):
        top = parts[0].label.span.center_y
        components = {part.amount for part in parts[1:]}
        if any(top + 0.75 * label.height < value.span.center_y < label.center_y - 0.75 * label.height
               and value not in components for value in amounts):
            return None
        combined = sum(part.amount.paise * (-1 if part.label.role == 'discount' else 1) for part in parts)
        if combined == link.amount.paise:
            return Total(link, 2, tuple(parts))
    if len(parts) == 1 and parts[0].amount.literal and link.label.name not in {'net total', 'net amount', 'net amt'}:
        paid = [other for other in links if other.label.role == 'paid' and _aligned(other, link)
                and 0.75 * label.height <= other.label.span.center_y - label.center_y <= 5 * label.height
                and other.amount.literal and other.amount in single_cells]
        if len(paid) == 1 and paid[0].amount.paise == link.amount.paise:
            return Total(link, 1, (*parts, paid[0]))
    return None


def _before_round(total, rounded):
    before = total.link
    left, right = before.label.span, rounded.label.span
    if left.row == right.row:
        return before.amount.span.row == right.row and before.amount.span.end <= right.start
    return 0.75 * right.height <= right.center_y - left.center_y <= 6 * right.height and _aligned(before, rounded)


def _roundoff_totals(cells, labels, amounts, primary, totals):
    replacements = defaultdict(list)
    standalone = []
    known = {total.link: total for total in totals}
    settled = _settlement_amounts(cells, primary)
    later_allowed = settled | {total.link.amount for total in totals}
    for label in (item for item in labels if item.role == 'round_target'):
        phrase = label.span
        for amount in amounts:
            if not amount.literal:
                continue
            value = amount.span
            rounded = Link(label, amount)
            if not (_lies_left(phrase, value) or _lies_right(phrase, value, 0.65)):
                continue
            if any(other.span.center_y - value.center_y > 0.75 * value.height
                   and other not in later_allowed for other in amounts):
                continue
            sources = []
            for before in primary:
                if not before.amount.literal or before.label.role not in TOTAL_ROLES or before.amount == amount:
                    continue
                source = before.amount.span
                if _on_row(source, value):
                    if (source.bounds[2] <= min(phrase.bounds[0], value.bounds[0])
                            and _clean_row(cells, value, phrase, value, source, before.label.span)):
                        sources.append(before)
                elif (_next_row(cells, source, value) and _same_column(source, value)
                      and _only_field(cells, before) and _only_field(cells, rounded)):
                    sources.append(before)
            if not sources:
                for prior in amounts:
                    source = prior.span
                    before = Link(None, prior)
                    if (prior == amount or not prior.literal or not _money_shape(prior)
                            or any(link.amount == prior for link in primary)):
                        continue
                    if ((_next_row(cells, source, value) and _same_column(source, value)
                         and _clean_row(cells, source, source) and _only_field(cells, rounded))
                            or (_on_row(source, value) and source.bounds[2] <= min(phrase.bounds[0], value.bounds[0])
                                and _clean_row(cells, value, phrase, value, source))):
                        sources.append(before)
            if len(sources) != 1:
                continue
            before = sources[0]
            seed = known.get(before)
            if amount.paise == (before.amount.paise + 50) // 100 * 100:
                candidate = Total(rounded, 2, (before, *(seed.support if seed else ())))
                if seed:
                    replacements[seed].append(candidate)
                else:
                    standalone.append(candidate)
            elif seed and amount.paise != before.amount.paise:
                replacements[seed].extend((seed, Total(rounded, seed.rank)))
    return replacements, standalone


def _page_totals(rows, direction):
    labels, amounts, quantities = [], [], []
    boxes_by_row, cells = {}, []
    for index, words in enumerate(rows):
        boxes = _project(words, direction)
        if boxes:
            boxes_by_row[index] = boxes
            cells.append(_span(index, 0, len(words), words, boxes))
            labels.extend(_labels(index, words, boxes))
            amounts.extend(_amounts(index, words, boxes))
            quantities.extend(_quantity_spans(index, words, boxes))
        elif words:
            # A long outlier without a total label can be unrelated vertical text.
            text = f" {' '.join(_label_text(word.text) for word in words)} "
            if any(f' {name} ' in text for role in ('final', 'document', 'round_target')
                   for name in ROLE_NAMES[role]):
                return None
    amounts = [amount for amount in amounts if not any(_contains(field, amount.span) for field in quantities)]
    links = list(_links(rows, cells, labels, amounts, quantities))
    links.extend(_left_links(rows, labels, amounts, links))
    links = [link for link in links if link.label.role != 'document'
             and (link.label.role != 'final' or _final_context(link, cells, labels, links))
             and (link.amount.literal or link.label.role in TAX_ROLES | {'subtotal', 'adjustment'})]
    document_support = dict(_document_links(cells, labels, amounts, links))
    links.extend(link for link in document_support if link not in links)
    header_pairs = list(_adjustment_headers(labels, links))
    groups = defaultdict(list)
    for link in links:
        groups[link.label].append(link)
    primary = []
    for group in groups.values():
        for heading, adjustment in header_pairs:
            if heading.center_y < group[0].label.span.center_y and any(
                    _under_header(link.amount, heading) for link in group):
                group = [link for link in group if _under_header(link.amount, heading)
                         or not _under_header(link.amount, adjustment)]
        primary.extend(group)

    single_cells = {amount for amount in amounts if not any(
        other is not amount and (other.span.row == amount.span.row or _same_row(other.span, amount.span))
        for other in amounts)}
    word_amounts = list(_word_amounts(rows, boxes_by_row, labels, cells, amounts))
    totals = []
    for link in primary:
        if link.label.role in {'final', 'document'}:
            totals.append(Total(link, 2, document_support.get(link, ())))
        elif link.label.role == 'generic':
            total = None
            if link.label.name in BARE_TOTALS:
                total = _words_summary(link, primary, amounts, cells, labels, quantities, word_amounts)
                if not total and link.amount in single_cells:
                    total = _bare_summary(link, primary, amounts, single_cells, cells, labels, quantities, word_amounts)
            elif link.amount in single_cells:
                total = _summary(link, primary, amounts, single_cells)
            if total:
                totals.append(total)

    replacements, standalone = _roundoff_totals(cells, labels, amounts, primary, totals)
    for rounded in (link for link in primary if link.label.role == 'rounded'):
        preceding = [total for total in totals if _before_round(total, rounded)]
        if preceding:
            nearest = max(preceding, key=lambda total: (total.link.label.span.center_y,
                                                         total.link.label.span.bounds[0]))
            nearest_rupee = (nearest.link.amount.paise + 50) // 100 * 100
            if rounded.amount.paise == nearest_rupee:
                replacements[nearest].append(Total(rounded, nearest.rank, (nearest.link, *nearest.support)))
            elif rounded.amount.paise != nearest.link.amount.paise:
                replacements[nearest].extend((nearest, Total(rounded, nearest.rank)))
    return standalone + [candidate for total in totals for candidate in replacements.get(total, [total])]


def _quads(span):
    return [[list(point) for point in word.quad] for word in span.words]


def _geometry(link):
    result = dict(amount_quads=_quads(link.amount.span))
    if link.label:
        result['label_quads'] = _quads(link.label.span)
    return result


def select_word_total(lines, threshold=0.90, claim_paise=None):
    """Return an INR total and original image-space word polygons when verified."""
    _validate_options(threshold, claim_paise)
    pages = defaultdict(list)
    continuing = set()
    try:
        for line in lines:
            page = line['page']
            if type(page) is not int or page < 0:
                raise ValueError('invalid page')
            words = tuple(_read_word(word) for word in line['words'])
            native_text = ' '.join(word.text for word in words)
            if FOREIGN.search(line['text']) or FOREIGN.search(native_text):
                return _unverified(threshold, 'unsupported_currency')
            normalized = _label_text(native_text)
            if normalized == 'continued' or CONTINUED.search(normalized):
                continuing.add(page)
            if words:
                pages[page].append(words)
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return _unverified(threshold, 'invalid_capture')

    candidates = []
    last_continued = max(continuing, default=-1)
    for page, rows in pages.items():
        if page <= last_continued:
            continue
        direction = _direction(rows)
        if direction is None:
            return _unverified(threshold, 'ambiguous_orientation')
        totals = _page_totals(rows, direction)
        if totals is None:
            return _unverified(threshold, 'ambiguous_orientation')
        candidates.extend((page, total) for total in totals)
    if not candidates:
        reason = 'continued_receipt' if continuing else 'no_final_evidence'
        return _unverified(threshold, reason)

    rank = max(total.rank for _, total in candidates)
    credible = [(page, total) for page, total in candidates if total.rank == rank]
    if len({total.link.amount.paise for _, total in credible}) > 1:
        return _unverified(threshold, 'conflicting_totals')
    page, best = max(credible, key=lambda entry: (entry[1].confidence,
                                                  entry[1].link.label.role == 'final'))
    confidence = best.confidence
    if confidence < threshold:
        return dict(_unverified(threshold, 'below_threshold'), confidence=confidence)
    paise = best.link.amount.paise
    evidence = dict(_geometry(best.link), page=page)
    if best.support:
        evidence['support'] = [_geometry(link) for link in best.support]
    result = dict(amount_paise=paise, currency='INR', confidence=confidence, threshold=threshold,
                  status=None, reason='verified', evidence=evidence)
    if claim_paise is not None and claim_paise > paise:
        result.update(status='Unusual amount', message='Requested amount exceeds the receipt total.')
    return result


def _local_image(filename):
    path = Path(filename)
    formats = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG'}
    if path.suffix.lower() not in formats or path.drive.startswith('\\\\'):
        raise ValueError('expected a local JPEG or PNG')
    if sys.platform == 'win32':
        import ctypes

        if ctypes.windll.kernel32.GetDriveTypeW(str(path.absolute().anchor)) == 4:
            raise ValueError('expected a local image')
    path = path.resolve(strict=True)
    if sys.platform == 'win32':
        if path.drive.startswith('\\\\') or ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4:
            raise ValueError('expected a local image')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_IMAGE_BYTES:
        raise ValueError('invalid image size')
    from PIL import Image

    with Image.open(path, formats=list(formats.values())) as image:
        width, height = image.size
        if (image.format != formats[path.suffix.lower()] or max(width, height) > MAX_IMAGE_SIDE
                or not 0 < width * height <= MAX_IMAGE_PIXELS or getattr(image, 'n_frames', 1) != 1):
            raise ValueError('invalid image format or dimensions')
        image.verify()
    return path


def _capture(result):
    if result.txts is None:
        return []
    return [dict(text=label, confidence=float(score), bbox=box.tolist(), page=0,
                 words=[dict(text=word, confidence=float(confidence),
                             bbox=[[float(x), float(y)] for x, y in quad])
                        for word, confidence, quad in words])
            for label, score, box, words in zip(result.txts, result.scores, result.boxes,
                                                result.word_results, strict=True)]


def extract_image(filename, threshold=0.90, claim_paise=None):
    _validate_options(threshold, claim_paise)
    try:
        path = _local_image(filename)
    except Exception:
        return _unverified(threshold, 'invalid_image')
    try:
        from receipt_total import create_engine

        lines = _capture(create_engine()(str(path), return_word_box=True))
        return select_word_total(lines, threshold, claim_paise)
    except Exception:
        return _unverified(threshold, 'runtime_failure')


def _worker_main(filename, threshold, claim_paise):
    # Keep the result pipe separate from Python and native OCR logging.
    with os.fdopen(os.dup(sys.stdout.fileno()), 'w', encoding='utf-8') as result_stream:
        with open(os.devnull, 'w') as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
            os.dup2(sink.fileno(), sys.stderr.fileno())
        if sys.platform == 'win32':
            import ctypes
            import msvcrt

            # Windows libraries can also write through the Win32 standard handles.
            set_handle = ctypes.windll.kernel32.SetStdHandle
            set_handle.argtypes = (ctypes.c_ulong, ctypes.c_void_p)
            if not all(set_handle(handle, msvcrt.get_osfhandle(stream.fileno()))
                       for handle, stream in ((-11, sys.stdout), (-12, sys.stderr))):
                raise OSError('worker log redirection failed')
        result = extract_image(filename, float(threshold), int(claim_paise) if claim_paise else None)
        json.dump(result, result_stream, allow_nan=False)


def _worker_command(filename, threshold, claim_paise):
    bootstrap = ('import sys; sys.path.insert(0, sys.argv[1]); '
                 'from word_total import _worker_main; _worker_main(*sys.argv[2:])')
    return [sys.executable, '-I', '-B', '-c', bootstrap, str(Path(__file__).resolve().parent),
            filename, str(threshold), '' if claim_paise is None else str(claim_paise)]


def _valid_quads(quads):
    return (type(quads) is list and bool(quads)
            and all(type(quad) is list and len(quad) == 4 and
                    all(type(point) is list and len(point) == 2 and
                        all(type(coord) in (int, float) and math.isfinite(coord) for coord in point)
                        for point in quad) for quad in quads))


def _valid_evidence(evidence):
    def geometry(value, label_required=True):
        return (type(value) is dict and set(value) <= {'amount_quads', 'label_quads'}
                and _valid_quads(value.get('amount_quads'))
                and (not label_required and 'label_quads' not in value or _valid_quads(value.get('label_quads'))))

    if type(evidence) is not dict or type(evidence.get('page')) is not int or evidence['page'] < 0:
        return False
    fields = dict(evidence)
    fields.pop('page')
    support = fields.pop('support', [])
    return (geometry(fields) and type(support) is list
            and all(geometry(item, label_required=False) for item in support))


def _worker_result(payload, threshold, claim_paise):
    result = json.loads(payload)
    if (type(result) is not dict or type(result.get('threshold')) not in (int, float)
            or result['threshold'] != threshold):
        raise ValueError('invalid worker result')
    reason, confidence = result.get('reason'), result.get('confidence')
    if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
        raise ValueError('invalid worker confidence')
    if reason == 'verified':
        paise = result.get('amount_paise')
        evidence = result.get('evidence')
        if type(paise) is not int or not 0 <= paise < 10 ** 14 or confidence < threshold or not _valid_evidence(evidence):
            raise ValueError('invalid verified result')
        expected = dict(amount_paise=paise, currency='INR', confidence=confidence, threshold=threshold,
                        status=None, reason=reason, evidence=evidence)
        if claim_paise is not None and claim_paise > paise:
            expected.update(status='Unusual amount', message='Requested amount exceeds the receipt total.')
    elif reason in ('invalid_image', 'runtime_failure', 'invalid_capture', 'unsupported_currency',
                    'ambiguous_orientation', 'continued_receipt', 'no_final_evidence', 'conflicting_totals',
                    'below_threshold'):
        expected = _unverified(threshold, reason)
        if reason == 'below_threshold' and confidence < threshold:
            expected['confidence'] = confidence
    else:
        raise ValueError('unknown worker result')
    if result != expected:
        raise ValueError('unexpected worker fields')
    return expected


def _extract_supervised(filename, threshold, claim_paise, timeout):
    deadline = time.monotonic() + timeout
    try:
        command = _worker_command(filename, threshold, claim_paise)
        with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as worker:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                payload, _ = worker.communicate(timeout=remaining)
                if time.monotonic() > deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait()
            if worker.returncode != 0:
                return _unverified(threshold, 'runtime_failure')
        return _worker_result(payload, threshold, claim_paise)
    except subprocess.TimeoutExpired:
        return _unverified(threshold, 'timeout')
    except (OSError, ValueError, OverflowError, RecursionError):
        return _unverified(threshold, 'runtime_failure')


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps(_unverified(0.90, 'invalid_options'), allow_nan=False))
        self.exit(2)


def main(argv=None):
    parser = _JsonArgumentParser(description=__doc__)
    parser.add_argument('image', metavar='IMAGE')
    parser.add_argument('--claim-paise', type=int, metavar='N')
    parser.add_argument('--threshold', type=float, default=0.90)
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT_SECONDS, metavar='SECONDS',
                        help='maximum worker wall time in seconds (default: %(default)s)')
    args = parser.parse_args(argv)
    try:
        _validate_options(args.threshold, args.claim_paise)
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError('timeout must be a positive finite number')
    except ValueError as exc:
        parser.error(str(exc))
    result = _extract_supervised(args.image, args.threshold, args.claim_paise, args.timeout)
    print(json.dumps(result, allow_nan=False))
    return int(result['reason'] in {'invalid_image', 'runtime_failure', 'invalid_capture', 'timeout'})


if __name__ == '__main__':
    raise SystemExit(main())
