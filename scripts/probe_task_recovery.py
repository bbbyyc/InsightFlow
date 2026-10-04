"""Run inside backend container: isolated fixtures, real DB/model/broker/worker.

Only the initial embedding call is replaced with a deliberate failure, in this
process. The running worker is never patched. Deletes only its own document.
"""
import json
import tempfile
from urllib.request import Request, urlopen
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import select
from app.models import Document, DocumentStatus
from app.models.chunk import Chunk
from app.services.task_service import TaskService
from app.tasks import _run_async, process_document_task


def main():
    report = {'status': 'running', 'scope': 'one synthetic document; injected embedding failure in isolated process; recovery/replays on real worker'}
    document_id = task_id = None
    with tempfile.TemporaryDirectory(prefix='resume-recovery-', dir='/app/data/uploads') as folder:
        source = Path(folder) / 'recovery.md'
        source.write_text('# Recovery evidence\n' + '\n\n'.join(
            f'## Section {n}\nReliable document ingestion retains validated chunks after an embedding outage. ' * 8
            for n in range(8)), encoding='utf-8')
        async def setup(db):
            doc = Document(title='resume-recovery-' + uuid4().hex + '.md', file_type='md',
                           file_path=str(source), status=DocumentStatus.PENDING)
            db.add(doc)
            await db.commit()
            record, _ = await TaskService(db).create_or_get('document_processing', uuid4().hex, 'document', doc.id)
            return str(doc.id), str(record.id)
        async def snapshot(db):
            from app.services.ids import require_uuid
            doc = await db.get(Document, require_uuid(document_id))
            task = await TaskService(db).get(task_id)
            chunks = (await db.scalars(select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index))).all()
            return {'document_status': doc.status.value, 'task_status': task.status.value,
                    'attempt': task.attempt, 'error_type': task.error_type,
                    'chunk_ids': [str(c.id) for c in chunks],
                    'vector_count': sum(c.embedding is not None for c in chunks)}
        try:
            document_id, task_id = _run_async(setup)
            with patch('app.services.document_service.DocumentService._generate_embeddings',
                       new=AsyncMock(side_effect=RuntimeError('probe: temporary embedding outage'))):
                try:
                    process_document_task.run(task_id, document_id)
                except RuntimeError as exc:
                    assert str(exc) == 'probe: temporary embedding outage'
                else:
                    raise AssertionError('Injected failure was not raised')
            failed = _run_async(snapshot)
            report['after_injected_failure'] = failed
            assert failed['document_status'] == 'failed' and failed['task_status'] == 'retrying'
            assert len(failed['chunk_ids']) > 1 and failed['vector_count'] == 0
            report['failed_document_search_counts'] = {}
            for mode in ('bm25', 'vector', 'hybrid'):
                request = Request('http://127.0.0.1:8000/api/search',
                                  data=json.dumps({'query': 'Reliable document ingestion', 'mode': mode,
                                                   'top_k': 5, 'document_ids': [document_id]}).encode(),
                                  headers={'Content-Type': 'application/json'})
                with urlopen(request, timeout=60) as response:
                    report['failed_document_search_counts'][mode] = json.load(response)['total']
            assert all(count == 0 for count in report['failed_document_search_counts'].values())
            # A real worker processes the explicitly redelivered task. This does
            # not claim to measure automatic retry backoff or a worker crash.
            report['recovery_result'] = process_document_task.delay(task_id, document_id).get(timeout=180)
            recovered = _run_async(snapshot)
            report['after_recovery'] = recovered
            assert recovered['document_status'] == 'completed' and recovered['task_status'] == 'succeeded'
            assert recovered['chunk_ids'] == failed['chunk_ids']
            assert recovered['vector_count'] == len(recovered['chunk_ids'])
            jobs = [process_document_task.delay(task_id, document_id) for _ in range(5)]
            report['replay_results'] = [job.get(timeout=60) for job in jobs]
            after = _run_async(snapshot)
            report['after_five_replays'] = after
            assert after == recovered
            report['status'] = 'passed'
        finally:
            if document_id:
                async def cleanup(db):
                    from app.services.document_service import DocumentService
                    return await DocumentService(db).delete_document(document_id)
                report['fixture_deleted'] = _run_async(cleanup)
            result = json.dumps(report, ensure_ascii=False, indent=2)
            Path('/tmp/resume_task_recovery.json').write_text(result, encoding='utf-8')
            print(result)


if __name__ == '__main__':
    main()
