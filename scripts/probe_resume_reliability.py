"""Live API evidence: concurrent idempotency and permanent failure isolation.

Creates unique, disposable fixtures; never deletes existing user documents.
"""
import argparse
import concurrent.futures
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--api', default='http://127.0.0.1:8000')
    parser.add_argument('--output', default='eval/reports/reliability_20260920.json')
    args = parser.parse_args()
    run_id = uuid4().hex
    prefix = 'resume-probe-' + run_id
    report = {'run_id': run_id, 'fixture_kind': 'synthetic reliability fixtures, not retrieval-quality dataset',
              'status': 'running', 'checks': {}, 'cleanup': []}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=120) as client:
        client.get(args.api + '/api/ready').raise_for_status()

        def upload(kind):
            data = (b'# Reliable ingestion\nA document task must produce only one copy of each chunk.\n'
                    if kind == 'valid' else b'(cid:123)' * 100)
            response = client.post(args.api + '/api/documents/upload',
                                   files={'file': (prefix + '-' + kind + '.md', data, 'text/markdown')},
                                   headers={'Idempotency-Key': prefix + '-' + kind})
            return {'http_status': response.status_code, 'body': response.json()}

        def wait(document_id):
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                response = client.get(args.api + '/api/documents/' + document_id)
                response.raise_for_status()
                doc = response.json()
                if doc['status'] in ['completed', 'failed']:
                    return doc
                time.sleep(1)
            raise TimeoutError('Fixture processing timed out')

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
                attempts = list(pool.map(upload, ['valid'] * 20))
            report['concurrent_uploads'] = attempts
            assert all(r['http_status'] in (200, 409) for r in attempts), attempts
            # 409 is the documented in-progress initialization response; retry
            # with the same key, not a new task. Preserve first-attempt counts.
            resolved = [upload('valid') if r['http_status'] == 409 else r for r in attempts]
            assert all(r['http_status'] == 200 for r in resolved)
            ids = {r['body']['id'] for r in resolved}
            tasks = {r['body']['task']['id'] for r in resolved}
            assert len(ids) == len(tasks) == 1
            valid = wait(next(iter(ids)))
            assert valid['status'] == 'completed', valid
            assert len(valid['chunks']) == valid['chunk_count'] > 0
            assert len({c['chunk_index'] for c in valid['chunks']}) == len(valid['chunks'])
            report['checks']['concurrent_idempotency'] = {
                'requests': 20, 'initial_successes': sum(r['http_status'] == 200 for r in attempts),
                'initial_conflicts': sum(r['http_status'] == 409 for r in attempts),
                'unique_document_ids': len(ids), 'unique_task_ids': len(tasks),
                'chunks': valid['chunk_count'], 'duplicate_chunk_indexes': 0}
            report['valid_fixture'] = valid
            print('Concurrent idempotency checked', flush=True)

            bad_upload = upload('invalid')
            assert bad_upload['http_status'] == 200
            bad = wait(bad_upload['body']['id'])
            assert bad['status'] == 'failed' and bad['task']['status'] == 'failed'
            assert bad['task']['attempt'] == 1 and bad['task']['error_type'] == 'DocumentQualityError'
            assert bad['chunk_count'] == len(bad['chunks']) == 0
            counts = {}
            for mode in ['bm25', 'vector', 'hybrid']:
                response = client.post(args.api + '/api/search', json={
                    'query': 'cid 123', 'mode': mode, 'top_k': 5, 'document_ids': [bad['id']]})
                response.raise_for_status()
                counts[mode] = response.json()['total']
            assert all(n == 0 for n in counts.values())
            report['checks']['permanent_failure_isolation'] = {
                'task_attempts': bad['task']['attempt'], 'document_status': bad['status'],
                'error_type': bad['task']['error_type'], 'chunks_written': 0, 'retrieved_by_mode': counts}
            report['invalid_fixture'] = bad
            report['status'] = 'passed'
            print('Permanent failure and three-way retrieval isolation checked', flush=True)
        except Exception as exc:
            report['status'] = 'failed'
            report['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            # Enumerate our exact fresh prefix, including fixtures created by an
            # upload whose response failed. Never touch unrelated documents.
            try:
                response = client.get(args.api + '/api/documents')
                response.raise_for_status()
                for doc in response.json():
                    if doc['title'] in {prefix + '-valid.md', prefix + '-invalid.md'}:
                        deleted = client.delete(args.api + '/api/documents/' + doc['id'])
                        report['cleanup'].append({'id': doc['id'], 'http_status': deleted.status_code})
            except Exception as exc:
                report['cleanup_error'] = str(exc)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['checks'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
