"""A separately registered disposable native trial, never real-project authority."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import time

from .codex_adapter import AppServerAdapter
from .connections import parse_owned_hub_state
from .native_config_registration import load_registration, TASK
from .service_contract import ServiceError
from .task_service import root_identity
from .workbench_store import digest


class RuntimeTrialAdapter(AppServerAdapter):
    """The Root backend supplies one frozen grant; HTTP cannot enroll a trial."""
    def __init__(self, root, *, hub_root, grant, trial_id):
        self.hub_root=Path(hub_root)
        self.trial_grant=grant
        self.trial_id=trial_id
        self.trial_thread=None
        self.trial_turn=None
        super().__init__(root)
        self._registration()

    def _registration(self):
        try:
            identity=load_registration(self.hub_root)
            fd=os.open(self.hub_root/'STATE.yaml',os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(fd,'rb')as stream:
                before=os.fstat(stream.fileno());raw=stream.read(1048577);after=os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_uid!=os.getuid()
                    or len(raw)>1048576
                    or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)
                    !=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
                raise ValueError('state identity')
            value=parse_owned_hub_state(raw)['pch_refactor']['r53_implementation']['runtime_trial']
            fields={'purpose','task_id','trial_id','root','root_identity','grant_sha256','metadata_registration_sha256','expires_at'}
            if (type(value)is not dict or set(value)!=fields or value['purpose']!='disposable_fixture'
                    or value['task_id']!=TASK or value['trial_id']!=self.trial_id
                    or value['root']!=str(self.root) or value['root_identity']!=root_identity(self.root)
                    or value['grant_sha256']!=digest(self.trial_grant)
                    or value['metadata_registration_sha256']!=identity.semantic_sha256
                    or type(value['expires_at'])not in (int,float) or not time.time()<value['expires_at']<time.time()+3600
                    or self.trial_grant.get('root')!=str(self.root)
                    or self.trial_grant.get('isolated_root')is not True
                    or self.trial_grant.get('checks')!=['fixture_sample']):raise ValueError('trial scope')
            return identity
        except ServiceError:raise
        except (OSError,ValueError,KeyError,TypeError):
            raise ServiceError('NATIVE_TRIAL_REGISTRATION_REJECTED',status=503)from None

    def _configuration_hash(self):
        return self._registration().config_sha256

    def thread(self, previous=None):
        if previous is not None or self.trial_thread is not None:
            raise ServiceError('NATIVE_TRIAL_REPLAY_REJECTED',status=409)
        self._registration()
        self.trial_thread=super().thread()
        return self.trial_thread

    def start(self, thread_id, text, image):
        if thread_id!=self.trial_thread or self.trial_turn is not None:
            raise ServiceError('NATIVE_TRIAL_REPLAY_REJECTED',status=409)
        self._registration()
        # A failed dispatch is uncertain, never an invitation to replay it.
        self.trial_turn='dispatching'
        self.trial_turn=super().start(thread_id,text,image)
        return self.trial_turn

    def interrupt(self, thread_id, turn_id):
        if thread_id!=self.trial_thread or turn_id!=self.trial_turn or turn_id=='dispatching':
            raise ServiceError('NATIVE_TRIAL_BINDING_REJECTED',status=409)
        return super().interrupt(thread_id,turn_id)
