"""Download pinned paper versions; never substitute another version silently."""
import argparse
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def valid_record(row):
    if not isinstance(row, dict):
        raise ValueError('paper record must be an object')
    string_fields = ('paper_id', 'version_id', 'pdf_url', 'pdf_sha256')
    if any(not isinstance(row.get(k), str) for k in string_fields):
        raise ValueError('paper ID, pinned version, URL and checksum must be strings')
    paper = row['paper_id']
    if not re.fullmatch(r'(computer|mathematics|physics|chemistry)/\d{4}\.\d{4,5}', paper):
        raise ValueError('invalid index paper_id')
    url = urllib.parse.urlsplit(row['pdf_url'])
    if url.scheme != 'https' or url.netloc != 'arxiv.org' or url.query or url.fragment:
        raise ValueError('index download URL must use https://arxiv.org')
    if url.path != '/pdf/' + row['version_id'] or not re.fullmatch(r'\d{4}\.\d{4,5}v\d+', row['version_id']):
        raise ValueError('index URL must specify the pinned version')
    if row['version_id'].split('v')[0] != paper.split('/')[1]:
        raise ValueError('pinned version does not match paper ID')
    if not re.fullmatch(r'[0-9a-f]{64}', row['pdf_sha256']) or type(row.get('pdf_bytes')) is not int or row['pdf_bytes'] < 5:
        raise ValueError('invalid index checksum or byte count')
    return paper


def read_index(path):
    """Use the same paper records as every other consumer; no duplicate manifest."""
    records, seen = [], set()
    with path.open(encoding='utf-8-sig') as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                paper = valid_record(row)
                if row.get('domain') != paper.split('/')[0]:
                    raise ValueError('domain does not match paper ID')
                if paper in seen:
                    raise ValueError('duplicate paper ID')
            except (ValueError, TypeError) as exc:
                raise ValueError(f'paper index line {number}: {exc}') from None
            seen.add(paper)
            records.append(row)
    if not records:
        raise ValueError('paper index is empty')
    return records


def verify(path, row):
    if path.stat().st_size != row['pdf_bytes']:
        return False
    sha = hashlib.sha256()
    with path.open('rb') as f:
        first = f.read(1024 * 1024)
        if not first.startswith(b'%PDF-'):
            return False
        sha.update(first)
        while chunk := f.read(1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest() == row['pdf_sha256']


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--index', type=Path, default=ROOT / 'data/papers.jsonl')
    p.add_argument('--output', type=Path, default=ROOT / 'data/pdfs')
    p.add_argument('--domain', choices=['computer', 'mathematics', 'physics', 'chemistry'])
    p.add_argument('--limit', type=int)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--verify-only', action='store_true')
    args = p.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        p.error('limit must be positive')
    try:
        rows = read_index(args.index)
    except (ValueError, OSError) as exc:
        p.error(str(exc))
    if args.domain:
        rows = [r for r in rows if r['domain'] == args.domain]
    if args.limit:
        rows = rows[:args.limit]
    for row in rows:
        valid_record(row)
    if args.dry_run:
        print(json.dumps({'files': len(rows), 'bytes': sum(r['pdf_bytes'] for r in rows),
                          'first_urls': [r['pdf_url'] for r in rows[:5]], 'network_requests': 0}, indent=2))
        return 0
    failed = 0
    last_request = 0
    for i, row in enumerate(rows, 1):
        target = args.output / (valid_record(row) + '.pdf')
        try:
            if target.exists():
                if not verify(target, row):
                    raise ValueError('existing_file_checksum_mismatch; preserve it and inspect before retrying')
                print(f'[{i}/{len(rows)}] verified {row["paper_id"]}')
                continue
            if args.verify_only:
                raise ValueError('missing_file')
            target.parent.mkdir(parents=True, exist_ok=True)
            time.sleep(max(0, 3 - (time.monotonic() - last_request)))
            request = urllib.request.Request(row['pdf_url'], headers={'User-Agent': 'ASD-Bench/0.1.0'})
            last_request = time.monotonic()
            temporary = target.with_suffix('.pdf.part')
            try:
                with urllib.request.urlopen(request, timeout=120) as response, temporary.open('wb') as f:
                    size = 0
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > row['pdf_bytes']:
                            raise ValueError('download_exceeds_expected_size')
                        f.write(chunk)
                if not verify(temporary, row):
                    raise ValueError('download_checksum_mismatch')
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            print(f'[{i}/{len(rows)}] downloaded {row["paper_id"]}')
        except Exception as exc:
            failed += 1
            # No network response body is emitted.
            print(f'[{i}/{len(rows)}] failed {row["paper_id"]}: {type(exc).__name__}')
    print(f'Processed={len(rows)}, failed={failed}; rerunning verifies and skips completed files.')
    return int(failed > 0)


if __name__ == '__main__':
    raise SystemExit(main())
