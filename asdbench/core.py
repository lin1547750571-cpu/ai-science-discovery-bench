"""Strict inputs and deterministic aggregation for the 16-item rubric."""
import hashlib
import json
import re
from decimal import Decimal, ROUND_HALF_UP

SUBSCORES = tuple(f'{p}{i}' for p in 'NFVR' for i in range(1, 5))
DIMENSIONS = ('Novelty', 'Feasibility', 'Value', 'Risk')
WEIGHTS = (
    ('0.35', '0.25', '0.25', '0.15'),
    ('0.20', '0.35', '0.30', '0.15'),
    ('0.35', '0.30', '0.20', '0.15'),
    ('0.35', '0.25', '0.25', '0.15'),
)
SCIENCE_FIELDS = {'proposal', 'Title', 'Motivation', 'Experiment', 'claim', 'plan', 'alternatives', 'evidence'}
MARKER = '{{RESEARCH_QUESTION}}'
LEGACY_MARKER = '{{在这里粘贴科研问题、部分研究方案或完整问题包}}'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    if not isinstance(value, bytes):
        value = value.encode('utf-8')
    return hashlib.sha256(value).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def strict_json(text, decimal_numbers=False):
    def bad_number(value):
        raise ValueError(f'non-finite JSON number: {value}')
    return json.loads(text, object_pairs_hook=_unique, parse_constant=bad_number,
                      parse_float=Decimal if decimal_numbers else float,
                      parse_int=Decimal if decimal_numbers else int)


def _number(value, name, subscore=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f'{name}: expected a JSON number or null')
    number = Decimal(str(value))
    if not number.is_finite() or not Decimal(0) <= number <= Decimal(10):
        raise ValueError(f'{name}: score must be finite and within 0–10')
    if subscore and number * 10 != (number * 10).to_integral_value():
        raise ValueError(f'{name}: at most one decimal place is allowed')
    return number


def rounded(value):
    return None if value is None else float(value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))


def aggregate(subscores):
    if not isinstance(subscores, dict) or set(subscores) != set(SUBSCORES):
        raise ValueError('exactly 16 subscores are required')
    values = {k: _number(v, k, True) for k, v in subscores.items()}
    raw = {}
    for i, dimension in enumerate(DIMENSIONS):
        group = [values[k] for k in SUBSCORES[4*i:4*i+4]]
        raw[dimension] = None if any(v is None for v in group) else sum(
            Decimal(w) * v for w, v in zip(WEIGHTS[i], group))
    raw['Total'] = None if any(raw[k] is None for k in DIMENSIONS) else (
        Decimal('0.35') * raw['Novelty'] + Decimal('0.35') * raw['Feasibility'] +
        Decimal('0.20') * raw['Value'] + Decimal('0.10') * (Decimal(10) - raw['Risk']))
    return {'subscores': {k: rounded(values[k]) for k in SUBSCORES},
            **{k: rounded(v) for k, v in raw.items()}}


def validate_review(text):
    value = strict_json(text, decimal_numbers=True)
    keys = {'subscores', *DIMENSIONS, 'Total'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('review must contain exactly subscores, four dimensions and Total')
    for key in (*DIMENSIONS, 'Total'):
        _number(value[key], key)
    result = aggregate(value['subscores'])
    corrected = any(_number(value[key], key) != _number(result[key], key) for key in (*DIMENSIONS, 'Total'))
    return result, corrected


def response_schema():
    score = {'type': ['number', 'null'], 'minimum': 0, 'maximum': 10}
    properties = {'subscores': {'type': 'object', 'properties': {k: score for k in SUBSCORES},
                                'required': list(SUBSCORES), 'additionalProperties': False}}
    properties.update({k: score for k in (*DIMENSIONS, 'Total')})
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _scientific_value(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        if any(not isinstance(x, str) for x in value):
            raise ValueError('science lists must contain strings')
        return any(x.strip() for x in value)
    if isinstance(value, dict):
        return any([_scientific_value(v) for v in value.values()])
    raise ValueError('science content must be text, a list of text or a text object')


def validate_submission(value):
    if not isinstance(value, dict):
        raise ValueError('each submission must be a JSON object')
    for key in ('id', 'system', 'paper_id'):
        if not isinstance(value.get(key), str) or not value[key].strip() or len(value[key]) > 256:
            raise ValueError(f'{key}: a nonempty string up to 256 characters is required')
    if not re.fullmatch(r'[a-z]+/[A-Za-z0-9_.-]+', value['paper_id']):
        raise ValueError('paper_id must be domain/arxiv_id (without a PDF extension)')
    material = value.get('material')
    if not isinstance(material, dict) or not material or not set(material) <= SCIENCE_FIELDS:
        raise ValueError('material must use only the documented scientific fields')
    valid = [_scientific_value(x) for x in material.values()]
    if not any(valid):
        raise ValueError('scientific material is empty')
    # Identity and metadata are deliberately not sent to the reviewer.
    return {k: value[k] for k in ('id', 'system', 'paper_id', 'material')}


