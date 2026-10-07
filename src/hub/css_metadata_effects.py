"""Parent-process effects for the standalone, once-only CSS metadata check."""
import os
import sys
import threading

from . import css_trial as c, css_process_config as p
from .codex_adapter import child_environment, isolated_launch
from .service_contract import ServiceError


class MetadataEffects:
    limits = {'version': 1, 'schema': 1, 'auth_mode': 2, 'mount': 5, 'source_head': 4, 'preview_head': 4, 'helper': 1}
    timeouts = {'version': 5, 'schema': 10, 'auth_mode': 5, 'mount': 1, 'source_head': 5, 'preview_head': 2, 'helper': 120}
    installed = False

    def __init__(self):
        self.counts = dict.fromkeys(self.limits, 0)
        self.audit = []
        self.violations = []
        self.parent_environment = dict(os.environ)  # Private, never serialized or hashed.
        self.schema_directory = None
        self.helper_command = None
        self.source_check = None
        self.source_receipt = None
        self.owner = threading.get_ident()

    def install(self):
        # Audit hooks live until process exit. This route runs only in its own
        # single-threaded script process, never in the production Hub service.
        if self.installed or threading.active_count() != 1 or os.getcwd() != str(c.HUB_ROOT):
            raise ServiceError('CSS_PRECHECK_STANDALONE_PROCESS_REQUIRED')
        type(self).installed = True
        sys.addaudithook(self.event)

    def reject(self, reason):
        if len(self.violations) < 16:
            self.violations.append(reason)
        raise ServiceError('CSS_PRECHECK_EFFECT_REJECTED')

    def bind_helper(self, config_hash):
        from .css_config_precheck import metadata_overrides
        self.helper_command = isolated_launch(('codex', 'app-server', '--stdio', *metadata_overrides()),
            root=c.TRIAL, expected_config_sha256=config_hash, read_only=True)[0]

    def caller(self):
        # Inspect public code identity only. Never inspect locals or retain a
        # stack. Identical argv in css_trial.verify is not this metadata route.
        prefix=str(c.HUB_ROOT/'src/hub')+'/'
        frame=sys._getframe(1)
        while frame is not None:
            code=frame.f_code
            if code.co_filename.startswith(prefix)and not code.co_filename.endswith('/css_metadata_effects.py'):
                return code.co_filename[len(prefix):],code.co_name
            frame=frame.f_back
        return None

    def event(self, event, args):
        if event.startswith('socket.'):
            return self.reject('PARENT_SOCKET')
        if event in {'os.system', 'os.exec', 'os.posix_spawn', 'os.fork', 'os.forkpty', 'pty.spawn'}:
            return self.reject('OTHER_SPAWN')
        if event != 'subprocess.Popen':
            return
        if threading.get_ident() != self.owner:
            return self.reject('OTHER_WRITER_THREAD')
        executable, argv, cwd, env = args
        if type(argv) not in (list, tuple) or any(type(x) is not str for x in argv):
            return self.reject('ARGV')
        argv = tuple(argv)
        commands = {
            'version': ('codex', '--version'),
            'auth_mode': ('codex', 'login', 'status'),
            'mount': ('findmnt', '--json', '--target', str(c.HUB_ROOT), '--output', 'TARGET,SOURCE,FSTYPE,UUID'),
            'source_head': ('git', 'rev-parse', 'HEAD'),
            'preview_head': ('git', '-C', str(c.ROOT), 'rev-parse', 'HEAD'),
        }
        if self.schema_directory is not None:
            commands['schema'] = ('codex', 'app-server', 'generate-json-schema', '--experimental', '--out', self.schema_directory)
        if self.helper_command is not None:
            commands['helper'] = self.helper_command
        kind = next((k for k, value in commands.items() if argv == value), None)
        if kind is None or executable != argv[0]:
            return self.reject('ARGV')
        callers={'version':('css_config_precheck.py','load_native_schemas'),
            'schema':('css_config_precheck.py','load_native_schemas'),
            'auth_mode':('css_trial.py','auth_check'),'mount':('task_storage.py','mount_identity'),
            'source_head':('css_capability_recovery.py','mapping_check'),
            'preview_head':('readonly_preview.py','check'),'helper':('codex_adapter.py','open')}
        if self.caller()!=callers[kind]:return self.reject('CALL_SITE')
        if argv[0]=='codex'and (self.source_check is None or self.source_receipt is None or
                self.source_check()!=self.source_receipt):return self.reject('PUBLIC_SOURCE_OR_BINARY')
        expected_cwd = {'source_head': str(c.ROOT), 'helper': str(c.TRIAL),
            'version': str(c.HUB_ROOT), 'schema': str(c.HUB_ROOT)}.get(kind)
        if (str(cwd) if cwd is not None else None) != expected_cwd:
            return self.reject('CWD')
        expected_env = None if kind in {'mount', 'source_head', 'preview_head'} else (
            child_environment() if kind == 'auth_mode' else p.process_environment())
        if env != expected_env or (env is None and dict(os.environ) != self.parent_environment):
            return self.reject('ENVIRONMENT')
        if self.counts[kind] >= self.limits[kind]:
            return self.reject('COUNT')
        self.counts[kind] += 1
        self.audit.append({'operation': kind, 'ordinal': self.counts[kind],
            'timeout_seconds': self.timeouts[kind],
            'timeout_evidence': 'pinned source and bounded runner',
            'cwd_category': {'source_head': 'canonical_source', 'helper': 'trial'}.get(kind, 'Hub'),
            'environment': 'inherited_private_snapshot' if env is None else ('base4' if kind == 'auth_mode' else 'process5')})

    def fact(self):
        complete = not self.violations and all(self.counts[k] == n for k, n in
            {'version': 1, 'schema': 1, 'auth_mode': 2, 'helper': 1}.items())
        return {'parent_processes': self.audit, 'counts': dict(self.counts),
            'effect_limits_satisfied': complete, 'violations': list(self.violations),
            'parent_socket_audit': 'active; creation and operations rejected',
            'child_network_visibility': 'NOT_RUN', 'raw_outputs_or_environment_retained': False}
