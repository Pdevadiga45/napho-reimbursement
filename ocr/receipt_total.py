"""Local RapidOCR receipt total extractor."""
import math
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median

DEFAULT_MODELS = Path(__file__).resolve().parent / 'models'
FOREIGN = re.compile(r'(?i)(?<![A-Z])(?:USD|BDT|PKR|EUR|GBP|AED|CAD|AUD)(?![A-Z])|[$€£৳]')
VALID = re.compile(r'(?:\d+|\d{1,3}(?:,\d{2})*,\d{3})(?:\.\d{1,2})?\Z')
AMOUNT_WORD = r'(?:amount|amt)'
NONFINAL = (
    r'(?:(?:page|table|item|running|carried\s*forward)\s*(?:sub\s*)?total'
    r'|sub\s*total|(?:total\s*)?(?:taxable|tax|[csiu]?gst|savings|discount|items?|qty|quantity)'
    r'(?:\s*(?:total|amount|amt|rate|value))?'
    r'|(?:previous|prior|opening|closing|outstanding|running|account)\s*'
    r'(?:account\s*)?(?:balances?|dues?|total)(?:\s*(?:due|total|amount|amt))?'
    r'|party\s*total(?!\s*(?:payable|receivable))'
    r'|(?:cash|change|tender(?:ed)?|partial)\s*(?:amount\s*)?(?:paid|received|due|total)?'
    r'|(?:net\s*)?amount\s*in\s*words|round\s*off(?:\s*(?:amount|adjustment))?)'
)
FINAL = (
    rf'(?:grand\s*total(?:\s*{AMOUNT_WORD})?|total\s*bill\s*{AMOUNT_WORD}'
    rf'|net\s*(?:payable|receivable)(?:\s*{AMOUNT_WORD})?|net\s*total'
    rf'|{AMOUNT_WORD}\s*(?:due|payable|charged|paid|receivable)'
    rf'|total\s*(?:due|payable)|balance\s*due'
    rf'|(?:total\s*)?invoice\s*value|total\s*\(\s*invoice\s*value\s*\)'
    rf'|party\s*total\s*(?:payable|receivable))'
)
CELL = re.compile(
    rf'(?P<rounded>round\s*off\s*to)(?![^\W\d])'
    rf'|(?P<blocked>{NONFINAL})(?![^\W\d])'
    rf'|(?P<final>{FINAL})(?![^\W\d])'
    rf'|(?P<net>net\s*{AMOUNT_WORD})(?![^\W\d])'
    rf'|(?P<total>total(?:\s*{AMOUNT_WORD})?)(?![^\W\d])'
    r'|(?P<continuation>continue[ds]?)(?=\b)'
    r'|(?P<currency>(?:inr|rs\.?)(?![^\W\d])|\u20b9|\(\s*(?:inr|rs\.?|\u20b9)\s*\))'
    r'|(?P<number>\d[\d,]*(?:\.\d+)?)(?:/-)?(?=$|[\s:;=|\u20b9])'
    r'|(?P<separator>[\s:;=|]+)'
    r'|(?P<other>[^\s:;=|]+)',
    re.I,
)
LABELS = {'final', 'net', 'total', 'rounded', 'blocked'}
HEADER = re.compile(
    r'(?i)^(?:(?:gst\s+)?(?:tax\s+)?(?:invoice|receipt)\b(?=\s*(?:$|[:#(]|no\b|number\b|date\b|id\b))'
    r'|bill\s*(?:no\b|number\b|date\b|id\b)|date\s+of\s+(?:invoice|issue)\b)'
)
FOOTER = re.compile(r'(?i)^(?:thank\s+you|terms\s+and\s+conditions|authori[sz]ed\s+signatory|amount\s+in\s+words)\b')
ORIENTATION_CONFIDENCE = .85


@dataclass(frozen=True)
class Cell:
    kind: str
    box: tuple
    score: float
    page: int
    tokens: frozenset
    paise: int | None = None

    @property
    def height(self):
        return max(1, self.box[3] - self.box[1])

    @property
    def y(self):
        return (self.box[1] + self.box[3]) / 2

def _box(quad):
    xs, ys = zip(*quad)
    return min(xs), min(ys), max(xs), max(ys)

def _money(s):
    if not VALID.fullmatch(s):
        return None
    rupees, _, paise = s.replace(',', '').partition('.')
    try:
        return int(rupees) * 100 + int(paise.ljust(2, '0'))
    except ValueError:
        return None


def _same_line(first, second):
    return abs(first.y - second.y) <= 0.6 * min(first.height, second.height)


def _cells(lines):
    for line in lines:
        pieces = []
        positions = []
        previous = None
        for token, content in sorted(line, key=lambda pair: pair[0].box[0]):
            if previous is not None:
                gap = token.box[0] - previous.box[2]
                height = min(token.height, previous.height)
                adjacent = _same_line(previous, token) and -0.25 * height <= gap <= 2 * height
                pieces.append(' ' if adjacent else '|')
                positions.append(None)
            pieces.append(content)
            positions.extend((token, index, len(content)) for index in range(len(content)))
            previous = token
        for match in CELL.finditer(''.join(pieces)):
            kind = match.lastgroup
            if kind == 'separator':
                continue
            # Interpolate within a box when OCR joins a label, currency and value.
            covered = [p for p in positions[match.start():match.end()] if p is not None]
            lefts, rights, tops, bottoms = [], [], [], []
            for token, index, length in covered:
                left, top, right, bottom = token.box
                step = (right - left) / length
                lefts.append(left + index * step)
                rights.append(left + (index + 1) * step)
                tops.append(top)
                bottoms.append(bottom)
            paise = _money(match['number']) if kind == 'number' else None
            if kind == 'number' and paise is None:
                kind = 'other'
            yield Cell(kind, (min(lefts), min(tops), max(rights), max(bottoms)),
                       min(token.score for token, _, _ in covered), covered[0][0].page,
                       frozenset().union(*(token.tokens for token, _, _ in covered)), paise)


def _valid_tokens(tokens):
    pages = {}
    foreign = False
    for index, token in enumerate(tokens):
        try:
            quad = tuple((float(point[0]), float(point[1])) for point in token['bbox'])
            box = _box(quad)
            score = float(token['confidence'])
            page = int(token.get('page', 0))
            content = str(token['text']).strip()
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            continue
        if not content or not 0 <= score <= 1 or not all(math.isfinite(v) for p in quad for v in p):
            continue
        foreign |= bool(FOREIGN.search(content))
        cell = Cell('token', box, score, page, frozenset({index}))
        pages.setdefault(page, []).append((cell, content, quad))
    return pages, foreign


def _dominant_vertical(native):
    horizontal = vertical = 0
    for token, content, _ in native:
        if token.score < ORIENTATION_CONFIDENCE or sum(char.isalpha() for char in content) < 4:
            continue
        left, top, right, bottom = token.box
        width, height = max(1, right - left), max(1, bottom - top)
        horizontal += width >= 1.6 * height
        vertical += height >= 1.6 * width
    return vertical >= 3 and vertical >= 3 * horizontal


def _make_cells(valid):
    lines = []
    for token, content in sorted(valid, key=lambda pair: (pair[0].page, pair[0].y)):
        matching = [line for line in lines if line[0][0].page == token.page
                    and _same_line(token, line[0][0])]
        if matching:
            min(matching, key=lambda line: abs(line[0][0].y - token.y)).append((token, content))
        else:
            lines.append([(token, content)])
    return list(_cells(lines))


def _header_before_footer(tokens, cells):
    trusted = [(token, content) for token, content in tokens if token.score >= ORIENTATION_CONFIDENCE]
    headers = [token.y for token, content in trusted if HEADER.match(content)]
    footers = [token.y for token, content in trusted if FOOTER.match(content)]
    footers.extend(cell.y for cell in cells if cell.kind == 'final' and cell.score >= ORIENTATION_CONFIDENCE)
    if not headers or not footers:
        return False
    min_gap = 3 * median(token.height for token, _ in tokens)
    gaps = [footer - header for header in headers for footer in footers]
    return sum(gap >= min_gap for gap in gaps) * 3 >= len(gaps) * 2


def _page_views(native):
    if not _dominant_vertical(native):
        return [_make_cells((token, content) for token, content, _ in native)]
    # RapidOCR returns corners in image coordinates. Their order does not give
    # the direction in which the recognizer read the text.
    views, ordered = [], []
    for direction in (1, -1):
        tokens = [(replace(token, box=_box(tuple((direction * y, -direction * x) for x, y in quad))),
                   content) for token, content, quad in native]
        cells = _make_cells(tokens)
        views.append(cells)
        if _header_before_footer(tokens, cells):
            ordered.append(cells)
    return ordered if len(ordered) == 1 else views


def _side_cells(label, cells, right):
    edge = label.box[2 if right else 0]
    height = label.height
    nearby = []
    for cell in cells:
        if cell == label or not _same_line(label, cell):
            continue
        gap = cell.box[0] - edge if right else edge - cell.box[2]
        if -0.25 * height <= gap <= (24 if right else 12) * height:
            nearby.append((gap, cell))
    return [cell for _, cell in sorted(nearby, key=lambda pair: pair[0])]


def _amount_run(label, cells, right=True):
    """Read the local numeric and currency cells up to the next label or text."""
    run = []
    for cell in cells:
        if cell.kind not in ('number', 'currency'):
            break
        run.append(cell)
    if not right:
        run.reverse()
    amounts = []
    currency = None
    for cell in run:
        if cell.kind == 'currency':
            currency = cell
            continue
        local_currency = (currency is not None and _same_line(currency, cell)
                          and cell.box[0] - currency.box[2] <= 3 * max(currency.height, cell.height))
        score = min(label.score, cell.score, currency.score if local_currency else 1)
        anchored = 'currency' if local_currency else 'token' if label.tokens & cell.tokens else None
        amounts.append((cell, score, anchored))
        currency = None
    return amounts


def _below_cells(label, cells):
    nearby = [cell for cell in cells if 0 < cell.y - label.y <= 2.5 * label.height
              and not _same_line(label, cell)
              and label.box[0] - 0.25 * label.height <= cell.box[0]
              <= label.box[2] + 12 * label.height]
    if not nearby:
        return []
    first = min(nearby, key=lambda cell: cell.y)
    return sorted((cell for cell in nearby if _same_line(first, cell)), key=lambda cell: cell.box[0])


def _bind(cells):
    labels = [cell for cell in cells if cell.kind in LABELS]
    by_page = {}
    by_token = {}
    for cell in cells:
        by_page.setdefault(cell.page, []).append(cell)
        if len(cell.tokens) == 1:
            by_token.setdefault(cell.tokens, []).append(cell)
    # A value owned on the right of any label, including a non-final role, cannot
    # also become the value on the left of the next label or the row above it.
    direct = {}
    runs = {}
    for label in labels:
        if label.kind == 'final' and label.tokens in by_token:
            literal = _amount_run(label, _side_cells(label, by_token[label.tokens], True))
            if literal:
                direct[label] = literal
        runs[label] = direct.get(label) or _amount_run(label, _side_cells(label, by_page[label.page], True))
    owned = {cell for run in runs.values() for cell, _, _ in run}
    for label in labels:
        if label.kind != 'blocked' and not runs[label]:
            left = _amount_run(label, _side_cells(label, by_page[label.page], False), right=False)
            runs[label] = [entry for entry in left if entry[0] not in owned]
    owned.update(cell for run in runs.values() for cell, _, _ in run)
    candidates = []
    explicit_support = set()
    for label, run in runs.items():
        if label.kind == 'blocked':
            continue
        if not run:
            below = _amount_run(label, _below_cells(label, by_page[label.page]))
            run = [entry for entry in below if entry[0] not in owned]
        anchored = any(entry[2] for entry in run)
        bound = [entry for entry in run if entry[2] or not anchored]
        if label.kind == 'total' and len(bound) > 1:
            continue
        for cell, score, anchor in bound:
            entry = (label, cell, score)
            candidates.append(entry)
            if label.kind == 'final' and (label in direct or anchor == 'currency'):
                explicit_support.add(entry)
    return candidates, explicit_support


def _near_rounding(before, label, targets):
    before_label, before_amount, _ = before
    height = max(before_label.height, label.height)
    if before_label.page != label.page or abs(before_label.y - label.y) > 2.5 * height:
        return False
    if abs(before_label.box[0] - label.box[0]) <= 3 * height:
        return True
    if any(abs(before_amount.box[2] - amount.box[2]) <= 2 * height for _, amount, _ in targets):
        return True
    return _same_line(before_label, label) and any(
        0 <= gap <= 4 * height for gap in (label.box[0] - before_amount.box[2],
                                          before_label.box[0] - label.box[2]))


def _is_table_total(entry, explicit, cells):
    label, amount, _ = entry
    height = label.height
    # Require a distinct summary and evidence that this total closes an item
    # table. An unqualified total in the same summary can still disagree.
    if not any(final.page == label.page and (
        abs(label.y - final.y) > 4 * height or abs(label.box[0] - final.box[0]) > 3 * height
    ) for final, _, _ in explicit):
        return False
    nearby = [cell for cell in cells if cell.page == label.page
              and .6 * height < label.y - cell.y <= 4 * height]
    for numeric in nearby:
        if numeric.kind != 'number' or abs(numeric.box[2] - amount.box[2]) > height:
            continue
        row = [cell for cell in nearby if _same_line(numeric, cell)]
        if (not any(cell.kind in LABELS for cell in row)
                and any(cell.kind == 'number' and cell != numeric for cell in row)
                and any(cell.kind == 'other' and abs(cell.box[0] - label.box[0]) <= height for cell in row)):
            return True
    return False


def _prioritize(candidates, cells):
    continued_pages = {cell.page for cell in cells if cell.kind == 'continuation'}
    explicit = [entry for entry in candidates if entry[0].kind == 'final']
    totals = [entry for entry in candidates if entry[0].kind != 'rounded'
              and not (entry[0].kind == 'total' and _is_table_total(entry, explicit, cells))]
    replaced = set()
    rounded = []
    for label in (cell for cell in cells if cell.kind == 'rounded'):
        targets = [entry for entry in candidates if entry[0] == label]
        adjacent = [entry for entry in totals if _near_rounding(entry, label, targets)]
        replaced.update(adjacent)
        # Both values must be unique within the summary and agree under nearest-rupee rounding.
        before_values = {cell.paise for _, cell, _ in adjacent}
        target_values = {cell.paise for _, cell, _ in targets}
        if len(before_values) != 1 or len(target_values) != 1:
            continue
        before = next(iter(before_values))
        _, target, score = max(targets, key=lambda entry: entry[2])
        if target.paise == ((before + 50) // 100) * 100:
            rounded.append((label, target, min(score, max(entry[2] for entry in adjacent))))
    return rounded + [entry for entry in totals if entry not in replaced
                      and (entry[0].kind == 'final' or entry[0].page not in continued_pages)]


def _without_fallback(candidates):
    return [entry for entry in candidates if entry[0].kind != 'net'] or candidates


def _page_candidates(views):
    readings = []
    for cells in views:
        bound, support = _bind(cells)
        candidates = _without_fallback(_prioritize(bound, cells))
        readings.append((candidates, any(entry in support for entry in candidates)))
    if len(readings) == 1:
        return readings[0][0]
    (first, first_anchored), (second, second_anchored) = readings
    if first_anchored != second_anchored:
        return first if first_anchored else second
    first_values = {(label.kind == 'net', cell.paise) for label, cell, _ in first}
    second_values = {(label.kind == 'net', cell.paise) for label, cell, _ in second}
    if first_values == second_values:
        # Once the interpretations agree, use the more conservative confidence.
        return min((first, second), key=lambda entries: max((entry[2] for entry in entries), default=0))
    return None

def select_total(tokens, threshold=0.90, claim_paise=None, source=None):
    """Bind literal amounts to nearby financial roles and project a result."""
    if not 0 <= threshold <= 1: raise ValueError('threshold must be in [0, 1]')
    start=time.perf_counter()
    result={'amount_paise':None,'currency':None,'confidence':0.0,'threshold':threshold,
            'status':'Total not verified','reason':'no_final_evidence','source':source}
    pages, foreign = _valid_tokens(tokens)
    currency_time = time.perf_counter()
    views = [_page_views(native) for native in pages.values()]
    candidate_time = time.perf_counter()
    per_page = [_page_candidates(page) for page in views]
    candidates = _without_fallback([entry for page in per_page if page for entry in page])
    bind_time = time.perf_counter()
    if foreign: result['reason']='unsupported_currency'
    elif any(page is None for page in per_page): result['reason']='ambiguous_orientation'
    elif candidates:
        values = {cell.paise for _, cell, _ in candidates}
        _, best, confidence = max(candidates, key=lambda entry: entry[2])
        result.update(candidate_amount_paise=best.paise,confidence=round(confidence,6),currency='INR')
        if len(values)>1: result.update(reason='conflicting_totals',confidence=0.0)
        elif confidence < threshold: result['reason']='below_threshold'
        else:
            result.update(amount_paise=best.paise,reason='verified',status=None)
            if claim_paise is not None and claim_paise>best.paise:
                result.update(status='Unusual amount',message='Requested amount exceeds the receipt total.')
    result['stage_seconds']={'currency':currency_time-start,'candidate':candidate_time-currency_time,'role_binding':bind_time-candidate_time,'projection':time.perf_counter()-bind_time}
    return result

def create_engine(models_dir=DEFAULT_MODELS):
    from rapidocr import RapidOCR
    from rapidocr.inference_engine.onnxruntime.provider_config import ProviderConfig
    from rapidocr.utils.download_file import DownloadFile
    model_dir=Path(models_dir)
    if not model_dir.is_dir() or not list(model_dir.glob('*.onnx')): raise RuntimeError('models_unavailable')
    def local_only(params):
        path=Path(params.save_path)
        if not path.is_file() or (params.sha256 and not DownloadFile.check_file_sha256(path,params.sha256)):
            raise RuntimeError('models_unavailable_or_hash_mismatch')
    DownloadFile.run=local_only
    ProviderConfig.get_ep_list=lambda self:[('CPUExecutionProvider',self.cpu_ep_cfg())]
    engine=RapidOCR(params={'Global.model_root_dir':str(model_dir),'Global.log_level':'error','EngineConfig.onnxruntime.intra_op_num_threads':2,'EngineConfig.onnxruntime.inter_op_num_threads':1,'EngineConfig.onnxruntime.use_cuda':False,'EngineConfig.onnxruntime.use_dml':False,'EngineConfig.onnxruntime.use_cann':False,'EngineConfig.onnxruntime.use_coreml':False})
    for name in ('det','cls','rec'): getattr(engine,'_load_'+name+'_model')()
    if any(getattr(engine,'text_'+n).session.session.get_providers()!=['CPUExecutionProvider'] for n in ('det','cls','rec')): raise RuntimeError('unexpected_provider')
    return engine
