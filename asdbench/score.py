"""Score JSONL submissions: python -m asdbench.score --help."""
import argparse
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .client import CompletionError, complete, validate_url
from .core import MARKER, canonical, digest, response_schema, strict_json, validate_review, validate_submission


def read_submissions(path):
    records, ids, pairs = [], set(), set()
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = validate_submission(strict_json(line))
        except (ValueError, TypeError) as exc:
            raise ValueError(f'input line {number}: {exc}') from None
        pair = (record['system'], record['paper_id'])
        if record['id'] in ids or pair in pairs:
            raise ValueError(f'input line {number}: duplicate ID or system/paper pair')
        ids.add(record['id']); pairs.add(pair); records.append(record)
    if not records:
        raise ValueError('input contains no submissions')
    return records


def atomic_write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as handle:
        for record in records:
            handle.write(canonical(record) + '\n')
    temporary.replace(path)


def load_resume(path, expected, signature):
    if not path.exists():
        return {}
    existing = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        row = strict_json(line)
        if not isinstance(row, dict) or row.get('id') not in expected or row['id'] in existing:
            raise ValueError('existing output contains unrelated or duplicate IDs; use a different output file')
        old = expected[row['id']]
        if any(row.get(k) != old[k] for k in ('system', 'paper_id')):
            raise ValueError('existing output uses different system/paper identities; use a new output file')
        if row.get('material_sha256') != old['material_sha256']:
            raise ValueError('existing output uses different scientific content; use a new output file')
        if row.get('status') == 'completed':
            if row.get('signature') != signature:
                raise ValueError('model, prompt or settings changed; use a different output file')
            required = ('subscores', 'Novelty', 'Feasibility', 'Value', 'Risk', 'Total')
            if any(k not in row for k in required):
                raise ValueError('existing output is missing required scores')
            review, _ = validate_review(canonical({k: row[k] for k in required}))
            if any(row[k] != review[k] for k in review):
                raise ValueError('existing output contains inconsistent aggregate scores')
            existing[row['id']] = row
    return existing


def run_one(job, args, instructions, prompt_hash, signature):
    payload = {'model': args.model, 'temperature': 0.0, 'max_tokens': args.max_tokens,
               'messages': [{'role': 'system', 'content': instructions},
                            {'role': 'user', 'content': canonical(job['material'])}]}
    if not args.omit_thinking_flag:
        payload['chat_template_kwargs'] = {'enable_thinking': False}
    if args.response_format == 'json_schema':
        payload['response_format'] = {'type': 'json_schema', 'json_schema': {
            'name': 'scientific_question_review', 'strict': True, 'schema': response_schema()}}
    elif args.response_format == 'json_object':
        payload['response_format'] = {'type': 'json_object'}
    output = {k: job[k] for k in ('id', 'system', 'paper_id', 'material_sha256')}
    output.update(prompt_sha256=prompt_hash, review_model=args.model, signature=signature)
    for attempt in range(args.attempts):
        try:
            response = complete(args.base_url, args.api_key, payload, args.timeout)
            scores, corrected = validate_review(response)
            output.update(scores, status='completed', aggregate_corrected=corrected,
                          reviewed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
            return output
        except (CompletionError, ValueError):
            # Never print provider bodies or scientific content on failures.
            if attempt + 1 < args.attempts:
                time.sleep(min(2 ** attempt, 8))
    output.update(status='failed', error='review_failed_after_retries')
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--prompt', type=Path, default=Path(__file__).resolve().parents[1] / 'prompts/review_prompt_zh.txt')
    parser.add_argument('--base-url', default=os.getenv('ASD_BASE_URL', ''))
    parser.add_argument('--model', default=os.getenv('ASD_MODEL', 'Qwen3.6-27B'))
    parser.add_argument('--workers', type=int, default=5)
    parser.add_argument('--attempts', type=int, default=3)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--max-tokens', type=int, default=2048)
    parser.add_argument('--max-input-bytes', type=int, default=250000)
    parser.add_argument('--response-format', choices=['json_schema', 'json_object', 'none'], default='json_schema')
    parser.add_argument('--omit-thinking-flag', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args(argv)
    args.api_key = os.getenv('ASD_API_KEY', '')
    if min(args.workers, args.attempts, args.max_tokens, args.max_input_bytes) < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('workers, attempts, timeout and budgets must be positive')
    if not args.model.strip():
        parser.error('model must not be empty')
    if args.input.resolve() == args.output.resolve() or args.prompt.resolve() == args.output.resolve():
        parser.error('output must differ from input and prompt')
    try:
        if not args.prepare_only:
            args.base_url = validate_url(args.base_url)
        raw_prompt = args.prompt.read_bytes()
        text = raw_prompt.decode('utf-8-sig')
        if text.count(MARKER) != 1 or text.split(MARKER)[1].strip():
            raise ValueError('review prompt must end with one material placeholder')
        instructions = text.split(MARKER)[0]
        records = read_submissions(args.input)
        for record in records:
            if len(canonical(record['material']).encode('utf-8')) > args.max_input_bytes:
                raise ValueError('submission exceeds input byte budget; content was not truncated')
            record['material_sha256'] = digest(canonical(record['material']))
        prompt_hash = digest(raw_prompt)
        signature = digest(canonical({'version': 'asdbench-score-v1', 'prompt': prompt_hash,
            'model': args.model, 'endpoint_sha256': digest(args.base_url), 'temperature': 0,
            'max_tokens': args.max_tokens, 'response_format': args.response_format,
            'thinking_flag_omitted': args.omit_thinking_flag, 'schema': response_schema()}))
        expected = {r['id']: r for r in records}
        if args.prepare_only:
            if args.output.exists():
                raise ValueError('prepare-only output already exists; use a new file')
            atomic_write(args.output, [{**{k: r[k] for k in ('id', 'system', 'paper_id', 'material_sha256')},
                'status': 'prepared', 'prompt_sha256': prompt_hash} for r in records])
            print(f'Validated {len(records)} submissions; no model request was made.')
            return 0
        results = load_resume(args.output, expected, signature)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    pending = [r for r in records if r['id'] not in results]
    print(f'Submissions={len(records)}, cached={len(results)}, pending={len(pending)}, workers={args.workers}')
    interrupted = False
    executor = ThreadPoolExecutor(max_workers=args.workers)
    futures = {executor.submit(run_one, r, args, instructions, prompt_hash, signature): r for r in pending}
    try:
        for future in as_completed(futures):
            row = future.result()
            results[row['id']] = row
            atomic_write(args.output, [results[r['id']] for r in records if r['id'] in results])
            print(f"Finished {len(results)}/{len(records)}: {row['status']}", flush=True)
    except KeyboardInterrupt:
        interrupted = True
        for future in futures:
            future.cancel()
        print('Stopping after in-flight requests; rerun the same command to resume.')
    finally:
        executor.shutdown(wait=True)
        for future in futures:
            if future.done() and not future.cancelled() and future.exception() is None:
                row = future.result(); results[row['id']] = row
        atomic_write(args.output, [results[r['id']] for r in records if r['id'] in results])
    if interrupted:
        return 130
    return int(any(r.get('status') != 'completed' for r in results.values()) or len(results) != len(records))


if __name__ == '__main__':
    raise SystemExit(main())
