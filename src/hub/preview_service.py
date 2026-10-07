"""Registered image views and shared annotations; no execution capability."""
from __future__ import annotations

import copy
from pathlib import Path

from .service_contract import ServiceError
from .workbench_store import digest, validate_binding, validate_command
from .design_records import content_hash

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
IMAGE_MIMES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}


class PreviewService:
    def __init__(self, projects, designs, store):
        self.projects, self.designs, self.store = projects, designs, store
        self.fixture_bridge = None
        self.readonly_preview = None

    def design_reference(self, binding):
        snapshot = self.designs.snapshot()
        expected = {'id': binding['candidate_id'], 'revision': binding['candidate_revision'],
                    'content_hash': binding['candidate_hash']}
        matches = [v for v in snapshot['effective'].values() if not v.get('superseded')
                   and not v.get('stale') and v['event']['candidate'] == expected
                   and v['event']['scope']['members'] == [{'project_id': binding['project_id'], 'pages': binding['pages']}]]
        if len(matches) != 1 or matches[0]['event']['action'] not in {'select', 'request_changes', 'defer'}:
            return None
        event = matches[0]['event']
        return {'reference': {'store_revision': snapshot['store_revision'], 'event_id': event['id'],
                              'event_hash': content_hash(event), 'action': event['action']},
                'feedback': event['feedback'], 'action': event['action']}

    @staticmethod
    def _eligible(project):
        return (project.get('connection_read_allowed', project.get('enabled')) is True
                and project.get('enabled') is True
                and project.get('access_profile') != 'no_current_goal_access'
                and project.get('current_state_status') != 'removed_local'
                and project.get('local_presence', {}).get('status') in {None, 'registered_local'}
                and project.get('root_kind') not in {'cloud', 'remote'}
                and project.get('project_type') not in {'cloud', 'cloud_project'}
                and isinstance(project.get('root_path'), str)
                and Path(project['root_path']).is_absolute())

    def catalog(self):
        resolver = self.projects._resolver()  # Metadata only; never refresh roots.
        resolver._check_registry()
        state = self.designs._read()  # Existing validated immutable facts.
        artifacts = {f['id']: f for f in state['facts'] if f['kind'] == 'artifact_ref'}
        candidates = [f for f in state['facts'] if f['kind'] == 'candidate']
        latest = {f['id']: max(c['revision'] for c in candidates if c['id'] == f['id']) for f in candidates}
        previews, unavailable = [], []
        for candidate in candidates:
            if candidate['revision'] != latest[candidate['id']]: continue
            if candidate.get('purpose') == 'read_only_snapshot' and (self.readonly_preview is None
                    or self.readonly_preview.candidate_id != candidate['id']):
                unavailable.append({'candidate_id': candidate['id'], 'reason': 'REAL_PREVIEW_UNAVAILABLE'})
                continue
            members = candidate['scope']['members']
            # A multi-project family image is not an unambiguous project view.
            if len(members) != 1: continue
            member = members[0]; pid = member['project_id']
            project = resolver.projects.get(pid)
            if project is None or not self._eligible(project): continue
            try: self.designs._assert_current_candidate(state, candidate)
            except ServiceError as error:
                unavailable.append({'candidate_id': candidate['id'], 'reason': error.code})
                continue
            for binding in candidate['artifact_bindings']:
                artifact = artifacts.get(binding['artifact_id'])
                if artifact is None or artifact['scope'] != candidate['scope'] or artifact['classification'] != candidate['classification'] or artifact['sha256'] != binding['sha256']:
                    continue
                location = artifact['location']
                if location['kind'] != 'hub_relative' or Path(location['value']).suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                path = Path(location['value'])
                if path.is_absolute() or '..' in path.parts or '\\' in location['value']:
                    continue
                preview_id = 'preview-' + digest([pid, candidate['id'], artifact['id']])[:32]
                descriptor = {'project_id': pid, 'preview_id': preview_id,
                    'candidate_id': candidate['id'], 'candidate_revision': candidate['revision'],
                    'candidate_hash': candidate['content_hash'], 'artifact_id': artifact['id'],
                    'artifact_sha256': artifact['sha256'], 'type': 'prototype',
                    'classification': artifact['classification'], 'registry_hash': resolver.authority['registry_hash'],
                    'pages': sorted(member['pages'])}
                try: validate_binding(descriptor)
                except (ValueError, TypeError): continue
                previews.append({'binding': descriptor, 'project_name': project['name'],
                                 'image_url': '/api/previews/' + preview_id + '/image',
                                 'source': 'registered_artifact_view', 'execution_allowed': False,
                                 'external_code_version': None,
                                 'design_feedback': self.design_reference(descriptor)})
                adapter = self.readonly_preview
                if adapter is not None and candidate['id'] == adapter.candidate_id:
                    try:
                        detail = adapter.describe()
                        if (detail['candidate_revision'], detail['candidate_hash']) != (candidate['revision'], candidate['content_hash']):
                            raise ServiceError('REAL_PREVIEW_VERSION_STALE', status=409)
                        captures = adapter.manifest['captures']
                        if not any(c.get('sha256') == artifact['sha256'] and c.get('path') == location['value'] for c in captures.values()):
                            raise ServiceError('REAL_PREVIEW_CAPTURE_STALE', status=409)
                    except ServiceError as error:
                        previews.pop()
                        unavailable.append({'candidate_id': candidate['id'], 'reason': error.code})
                        continue
                    descriptor['type'] = 'screenshot'
                    previews[-1].update({'source': detail['source'], 'external_code_version': detail['code_head'],
                        'capture': detail, 'live_url': '/api/live-preview/' + preview_id,
                        'purpose': 'read_only_snapshot', 'decision_eligible': False})
        resolver._check_registry()
        ids = [p['binding']['preview_id'] for p in previews]
        if len(ids) != len(set(ids)):
            raise ServiceError('PREVIEW_AMBIGUOUS', status=503)
        return {'previews': previews, 'unavailable': unavailable,
                'design_revision': state['revision'], 'classification': state['store_classification'],
                'workflow_classification': self.store.classification}

    def resolve(self, preview_id, *, binding=None, image=False):
        if binding is not None:
            try: validate_binding(binding)
            except (ValueError, TypeError): raise ServiceError('INVALID_PREVIEW_BINDING') from None
        matches = [p for p in self.catalog()['previews'] if p['binding']['preview_id'] == preview_id]
        if len(matches) != 1:
            raise ServiceError('PREVIEW_UNAVAILABLE', status=404)
        selected = matches[0]; current = selected['binding']
        if binding is not None and current != binding:
            raise ServiceError('PREVIEW_STALE', status=409)
        if not image: return selected
        result = self.designs.artifact(current['artifact_id'], candidate_id=current['candidate_id'],
                                      candidate_revision=current['candidate_revision'])
        if result.content_type not in IMAGE_MIMES or result.disposition != 'inline' or result.sha256 != current['artifact_sha256']:
            raise ServiceError('PREVIEW_IMAGE_REJECTED', status=409)
        # Never serve bytes after catalog/registry drift during the artifact read.
        self.resolve(preview_id, binding=current)
        return result

    def annotations(self):
        state = self.store.read(); records = []
        current = {p['binding']['preview_id']: p['binding'] for p in self.catalog()['previews']}
        for row in state['records']:
            item = copy.deepcopy(row)
            binding = row['record']['annotation']['binding']
            item['material_state'] = 'current' if current.get(binding['preview_id']) == binding else 'stale'
            if item['material_state'] == 'current':
                try: self.validate_references(row['command'], historical=True)
                except ServiceError: item['material_state'] = 'stale'
            item['execution_allowed'] = False
            records.append(item)
        return {'revision': state['revision'], 'records': records, 'classification': state['classification']}

    def validate_references(self, command, *, context=None, historical=False):
        binding = command['binding']
        self.resolve(binding['preview_id'], binding=binding, image=True)
        if 'design_reference' in command:
            current = self.design_reference(binding)
            if current is None or current['reference'] != command['design_reference']:
                raise ServiceError('DESIGN_REFERENCE_STALE', status=409)
        if 'element_hint' in command:
            if self.fixture_bridge is None:
                raise ServiceError('BRIDGE_DISABLED', status=403)
            self.fixture_bridge.validate(command['element_hint'], binding, context=context, historical=historical)

    def save(self, command, *, backend_instance, context=None, authorize=lambda: None, actor='owner_session'):
        try: command = validate_command(command)
        except (ValueError, TypeError, UnicodeError): raise ServiceError('INVALID_ANNOTATION') from None
        binding = command['binding']
        self.validate_references(command, context=context)
        def guard():
            authorize()
            self.validate_references(command, context=context)
            authorize()
        return self.store.save(command, backend_instance=backend_instance, guard=guard, actor=actor)

    def receipt(self, request_id):
        receipt = self.store.receipt(request_id)
        if receipt is None: raise ServiceError('ANNOTATION_REQUEST_NOT_FOUND', status=404)
        return {'receipt': receipt, 'outcome': 'COMMITTED'}
