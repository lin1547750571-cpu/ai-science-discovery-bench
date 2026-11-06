"""Convert a user's local scorer JSONL output to an Excel-readable CSV."""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from asdbench.core import SUBSCORES, DIMENSIONS, canonical, strict_json, validate_review


def export(source, target):
    if source.resolve() == target.resolve() or target.exists():
        raise ValueError('use a new CSV output path distinct from the input')
    rows = []
    for number, line in enumerate(source.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        r = strict_json(line)
        if r.get('status') == 'completed':
            expected, _ = validate_review(canonical({k:r[k] for k in ('subscores', *DIMENSIONS, 'Total')}))
            if any(r[k] != expected[k] for k in expected):
                raise ValueError(f'inconsistent aggregates at line {number}')
        row = {k:r.get(k) for k in ['id', 'system', 'paper_id', *DIMENSIONS, 'Total', 'status', 'review_model', 'prompt_sha256', 'material_sha256']}
        row.update(r.get('subscores', {}))
        for k in ['id', 'system', 'paper_id', 'review_model']:
            if isinstance(row.get(k), str) and row[k].lstrip().startswith(('=', '+', '-', '@')):
                row[k] = "'" + row[k]
        rows.append(row)
    columns = ['id', 'system', 'paper_id', *SUBSCORES, *DIMENSIONS, 'Total', 'status', 'review_model', 'prompt_sha256', 'material_sha256']
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.tmp')
    with temporary.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows)
    temporary.replace(target)
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        n = export(args.input, args.output)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(f'Exported {n} rows. Blank scores and failed/prepared statuses are preserved, not replaced by zero.')


if __name__ == '__main__':
    main()
