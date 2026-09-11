from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import app, get_current_user
from app.models import AuthUser
from app.contracts.knowledge import KnowledgeMutation
from app.services.knowledge_repository import empty_state
from app.services import knowledge_service as service

class MemoryRepository:
    def __init__(self): self.states, self.receipts = {}, {}
    def read(self, owner): return deepcopy(self.states.get(owner, empty_state()))
    def mutate(self, owner, key, fingerprint, apply, project_id=None):
        existing = self.receipts.get((owner,key))
        if existing:
            if existing[0] != fingerprint: raise HTTPException(409,'Different request')
            return self.read(owner), existing[1]
        state = self.read(owner)
        result = apply(state)
        state['revision'] += 1
        self.states[owner] = deepcopy(state)
        self.receipts[(owner,key)] = fingerprint, result
        return state, result

class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.actor = AuthUser(sub='owner', name='Owner', email='owner@example.com')
        self.repo = MemoryRepository()
        self.counter = 0
        self.project = SimpleNamespace(current_snapshots={'requirements':SimpleNamespace(payload={'requirements':[{'id':'R1','text':'Original rule'}]})})
        self.patches=[patch.object(service,'repository',return_value=self.repo),patch.object(service,'project_for',side_effect=lambda p,a: self.project if p else None)]
        for item in self.patches: item.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
    def mutate(self, action, entry=None, project='p1', draft=None, key=None):
        self.counter += 1
        return service.mutate_knowledge(self.actor, project, KnowledgeMutation(action=action,entry_id=entry.id if entry else None,
            base_revision=entry.revision if entry else 0,draft=draft), key or str(self.counter))
    def draft(self, **kwargs):
        return {'text':'Include boundary values','stages':['use_cases'],**kwargs}
    def test_approval_revisions_preserve_old_active_until_approved(self):
        entry=self.mutate('propose',draft=self.draft())
        self.assertIsNone(entry.active)
        self.assertEqual(service.resolve_memories(self.actor,'p1','use_cases')[0],[])
        entry=self.mutate('approve',entry)
        updated=self.mutate('revise',entry,draft=self.draft(text='Include both boundaries'))
        self.assertEqual(updated.active['text'],'Include boundary values')
        self.assertEqual(updated.pending['text'],'Include both boundaries')
        with self.assertRaises(HTTPException) as error: self.mutate('retire',entry)
        self.assertEqual(error.exception.status_code,409)
        updated=self.mutate('approve',updated)
        self.assertEqual(updated.active['text'],'Include both boundaries')
    def test_selection_is_explicit_and_scoped_and_retirement_is_global(self):
        entry=self.mutate('propose',project=None,draft=self.draft())
        entry=self.mutate('approve',entry,project=None)
        self.assertEqual(service.resolve_memories(self.actor,'p1','use_cases')[0],[])
        self.mutate('select',entry)
        self.assertEqual(len(service.resolve_memories(self.actor,'p1','use_cases')[0]),1)
        self.assertEqual(service.resolve_memories(self.actor,'p2','use_cases')[0],[])
        self.mutate('retire',entry,project=None)
        self.assertEqual(service.resolve_memories(self.actor,'p1','use_cases')[0],[])
    def test_requirement_changes_exclude_knowledge(self):
        entry=self.mutate('propose',draft=self.draft(requirement_ids=['R1']))
        entry=self.mutate('approve',entry)
        self.assertEqual(len(service.resolve_memories(self.actor,'p1','use_cases',['R1'])[0]),1)
        self.project.current_snapshots['requirements'].payload['requirements'][0]['text']='Changed rule'
        self.assertEqual(service.list_knowledge(self.actor,'p1').entries[0].status,'needs_confirmation')
        self.assertEqual(service.resolve_memories(self.actor,'p1','use_cases',['R1'])[0],[])
        entry=self.mutate('approve',entry)
        self.assertEqual(entry.status,'active')
    def test_request_idempotency_and_owner_isolation(self):
        a=self.mutate('propose',draft=self.draft(),key='same')
        b=self.mutate('propose',draft=self.draft(),key='same')
        self.assertEqual(a,b)
        with self.assertRaises(HTTPException): self.mutate('propose',draft=self.draft(text='different'),key='same')
        other=AuthUser(sub='other',name='Other',email='other@example.com')
        self.assertEqual(service.list_knowledge(other,'p1').entries,[])
        with self.assertRaises(HTTPException): self.mutate('retire',a,project='p2')
    def test_promotion_requires_generalization_and_separate_approval(self):
        entry=self.mutate('propose',draft=self.draft())
        entry=self.mutate('approve',entry)
        promoted=self.mutate('promote',entry,draft=self.draft())
        self.assertEqual(promoted.scope,'personal')
        self.assertIsNone(promoted.active)
        with self.assertRaises(HTTPException): self.mutate('promote',entry,draft=self.draft(kind='business_fact'))
    def test_delete_purges_versions_and_selections(self):
        entry=self.mutate('propose',project=None,draft=self.draft())
        entry=self.mutate('approve',entry,project=None)
        self.mutate('select',entry)
        self.mutate('delete',entry,project=None)
        state=self.repo.read(self.actor.sub)
        self.assertEqual(state['entries'][entry.id]['versions'],[])
        self.assertEqual(state['selections']['p1'],[])
    def test_secrets_rejected(self):
        with self.assertRaises(ValueError): KnowledgeMutation(action='propose',draft=self.draft(text='password=secret123'))
    def test_api_requires_idempotency_key(self):
        app.dependency_overrides[get_current_user]=lambda:self.actor
        try:
            with TestClient(app) as client:
                self.assertEqual(client.post('/me/knowledge',json={'action':'propose','draft':self.draft()}).status_code,422)
                response=client.post('/me/knowledge',json={'action':'propose','draft':self.draft()},headers={'X-Request-ID':'api'})
                self.assertEqual(response.status_code,200,response.text)
        finally: app.dependency_overrides.clear()

class MemoryAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_owner_bound_snapshot(self):
        from app.services.knowledge_memory import ApprovedKnowledgeMemory
        from app.services.guidance_service import build_manifest
        adapter=ApprovedKnowledgeMemory('owner',build_manifest('use_cases','test'))
        with self.assertRaises(PermissionError): await adapter.add_session_to_memory(None)
        with self.assertRaises(PermissionError): await adapter.search_memory(app_name='app',user_id='other',query='all')
        self.assertEqual((await adapter.search_memory(app_name='app',user_id='owner',query='all')).memories,[])
