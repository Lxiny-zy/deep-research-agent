"""Offline review probes; isolated in-memory DB, no lifespan, network or real credentials.

Run from repository root: python docs/review-20261016/probe.py
Assertions describe observed defects, not desired regression behavior.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
from fastapi import FastAPI
from sqlalchemy import event

from deep_research import api
from deep_research.config import Settings
from deep_research.library.repository import SqlLibraryRepository
from deep_research.observability import Event
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.sql_repository import SqlRepository
from deep_research.workbench.qa_store import SqlQaStore, InMemoryQaStore
from deep_research.workbench.usage import usage_today


async def main():
    results = {}
    # Mount the real handler on a fresh app; only persistence/dispatch are isolated.
    app = FastAPI()
    app.post('/api/runs', status_code=202)(api.create_run)
    repo = InMemoryRepository()
    app.state.repo = repo
    app.state.settings = Settings(intent_enabled=False, api_key='', api_credentials=())
    app.state.catalog = None
    app.state.inline_worker = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as c:
        payload = {'query': 'Review sensor reconstruction methods', 'template': 'autoResearch', 'strategy': 'quick'}
        first = await c.post('/api/runs', json=payload, headers={'Idempotency-Key': 'review-probe'})
        second = await c.post('/api/runs', json=payload, headers={'Idempotency-Key': 'review-probe'})
        results['template_retry'] = {'first': first.status_code, 'retry': second.status_code, 'retry_body': second.json()}
        assert first.is_success and second.status_code == 409

    isolated = InMemoryRepository()
    run_id, _ = await isolated.create_run_once('quota', request_hash='quota', owner_id='probe')
    await isolated.finalize(run_id, elapsed=0, total_tokens=123)
    before = await usage_today(isolated, 'probe')
    await isolated.delete_run(run_id)
    after = await usage_today(isolated, 'probe')
    results['quota_delete'] = {'before': before, 'after': after}
    assert before['runs'] == 1 and after['runs'] == 0

    run_id = await isolated.create_run('events')
    events = [Event(stage='RESEARCHER', type='info', message='progress') for _ in range(2000)]
    events.append(Event(stage='RESEARCHER', type='info', message='policy', data={'category': 'source_policy', 'blocked': 7}))
    await isolated.save_events(run_id, events)
    detail = await isolated.get_run(run_id)
    detail = await api._enrich_run_detail(isolated, detail)
    results['truncated_metrics'] = {'stored_events': len(await isolated.get_events(run_id)), 'detail_events': len(detail.events), 'blocked_sources': detail.metrics.blocked_sources, 'expected': 7}
    assert detail.metrics.blocked_sources == 0

    engine = make_engine('sqlite+aiosqlite:///:memory:')
    await create_all(engine)
    sessions = make_sessionmaker(engine)
    try:
        sql = SqlRepository(sessions)
        qa = SqlQaStore(sessions)
        run_id = await sql.create_run('bound task')
        conv = await qa.create('probe', 'bound conversation', run_id)
        await sql.delete_run(run_id)
        results['deleted_task_conversation'] = {'still_readable': await qa.get(conv.id) is not None, 'in_regular_list': any(c.id == conv.id for c in await qa.list('probe'))}
        assert results['deleted_task_conversation'] == {'still_readable': True, 'in_regular_list': False}

        memory = InMemoryQaStore()
        for i in range(201):
            await qa.create('paging-probe', f'c{i}')
            await memory.create('paging-probe', f'c{i}')
        results['conversation_pagination'] = {'sql_list': len(await qa.list('paging-probe')), 'memory_list': len(await memory.list('paging-probe'))}
        assert results['conversation_pagination'] == {'sql_list': 200, 'memory_list': 201}

        library = SqlLibraryRepository(sessions)
        project = await library.create_project('probe', 'visible')
        await library.create_project('other-probe', 'unrelated')
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(engine.sync_engine, 'before_cursor_execute', capture)
        try:
            found = await library.get_project(project.id)
        finally:
            event.remove(engine.sync_engine, 'before_cursor_execute', capture)
        assert found.id == project.id
        results['project_lookup'] = {'sql_statements': len(statements), 'has_where': any('WHERE' in sql.upper() for sql in statements)}
        assert results['project_lookup']['has_where'] is False
    finally:
        await engine.dispose()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    Path(__file__).with_name('probe-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    asyncio.run(main())
