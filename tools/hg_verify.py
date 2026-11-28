#!/usr/bin/env python3
"""Plan, run, resume and summarize the owner-local M12 verification."""
import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import shlex
import signal
import subprocess
import sys
import tempfile
import stat
import time
from threading import Lock

from hg_evidence_safety import digest
from hg_publication_audit import worktree_paths
from hg_verify_inputs import ENV_PATHS, configured_paths, saved_inputs
from hg_verify_inventory import PROFILES, inventory
from hg_verify_milestones import PARALLEL as MILESTONE_PARALLEL
from hg_verify_results import (
    ERROR, INTERRUPTED, INVALID, MISSING, PASSED, REJECTED, SKIPPED, UNRUN, classify, read_json,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / 'local/verification'
INVENTORY = ROOT / 'config/m12-verification.json'
HISTORICAL = ROOT / 'evidence/m12/checks.json'
PINS = ROOT / 'evidence/m2/shared-tools.json'
MEDIA = ROOT / 'evidence/local-inputs.json'
STATE_SCHEMA = 'hg-m12-verification/v2'
SOURCE_DIRS = ('src', 'tools', 'tests', 'ghidra_scripts', 'analysis', 'evidence', 'config')
ACCEPTED = {PASSED, REJECTED}
JOBS = (1, 2, 4, 8)
# Audited readers and attempt-isolated verifiers whose build/temp/output paths
# stay strictly within the active attempt directory and HG_VERIFY_BUILD_DIR.
PARALLEL_RESULTS = {
    'structure', 'hg-bytes', 'ps2dinfo-bytes', 'overlay-bytes', 'git-diff',
    'compileall', 'audit', 'local-inputs', 'sanitizer-build', 'sanitizer-run',
    'unittest', 'discovery', 'disc-read', 'report', 'legacy-controls',
    'ordinary-control', 'runtime', 'grounded-differential',
    'continuous', 'predispatch', 'predispatch-contracts', 'counter-guard',
    'lifecycle-differential', 'lifecycle-native', 'lifecycle-controls',
    'lifecycle-corridor-controls', 'lifecycle-sequence',
    'front-end-differential', 'front-end-native', 'front-end-controls',
    'content-corpus', 'audio-instructions', 'audio-connected',
} | MILESTONE_PARALLEL
# Dispatch priority when jobs > 1: launch measured long-running gates early
# alongside fast diagnostics so no single straggler stretches the tail.
# Serial runs (jobs == 1) always dispatch in exact inventory order.
PARALLEL_HEAVY_ORDER = (
    'constraint-differential', 'turn-camera-saved-runtime', 'grounded-saved-runtime',
    'constraint-saved-runtime', 'synthetic-suite', 'walk-camera-saved-runtime',
    'grounded-differential', 'turn-camera-instructions', 'm16-corridor',
    'lifecycle-corridor', 'm14-register-contracts', 'm14-native-window',
    'lifecycle-differential', 'front-end-differential', 'connected-native-lifecycle',
    'connected-native-controls', 'lifecycle-corridor-controls', 'lifecycle-negative-controls',
    'connected-player-saved-runtime', 'player-transition-instructions',
    'connected-player-instructions', 'area-lifecycle-instructions', 'grounded-m11-frames',
)


class Interrupted(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, indent=2) + '\n')


def atomic_text(path, value):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
            parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        finally:
            temporary.unlink(missing_ok=True)


def git_value(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=10)
    return result.stdout.strip() if result.returncode == 0 else None


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Inputs:
    def __init__(self, options, stop):
        self.stop = stop
        self.files = {}
        self.cache_lock = Lock()
        self.file_locks = {}
        self.pins = read_json(PINS)['tools']
        self.media = read_json(MEDIA)['inputs']
        self.paths = {key: Path(value) for key, value in options.items()}
        shared = self.paths['ps2dinfo']
        env_path = os.pathsep.join(os.path.abspath(path) for path in
                                   os.environ.get('PATH', os.defpath).split(os.pathsep))
        self.environment = {
            'PATH': env_path, 'LC_ALL': 'C.UTF-8',
            'ASAN_OPTIONS': 'detect_leaks=1:halt_on_error=1', 'UBSAN_OPTIONS': 'halt_on_error=1',
            'GIT_CONFIG_NOSYSTEM': '1',
        }
        self.python = Path(sys.executable).resolve()
        self.tools = {}
        self.shared_revision = git_value(shared, 'rev-parse', 'HEAD')
        self.source_names, self.source_files, self.revision = self.sources()
        self.identity = {
            'hungry_ghosts': {'revision': self.revision, 'source_sha256': canonical_hash(self.source_files),
                             'working_files': git_value(ROOT, 'status', '--short', '--untracked-files=all')},
            'ps2dinfo': {'revision': self.shared_revision, 'required_tool_hashes': self.pins},
            'python': {'path': str(self.python), 'version': sys.version, 'sha256': self.file(self.python)},
            'inventory_sha256': self.file(INVENTORY),
        }

    @staticmethod
    def stamp(path):
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f'Expected a regular file: {path}')
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def file(self, path, expected=None, *, probe=False):
        self.stop.check()
        key = path.resolve()
        stamp = self.stamp(path)
        if probe and stamp[2] > 8 * 1024 * 1024:
            return None
        with self.cache_lock:
            lock = self.file_locks.setdefault(key, Lock())
        with lock:
            self.stop.check()
            stamp = self.stamp(path)
            cached = self.files.get(key)
            if not cached or cached[0] != stamp:
                cached = stamp, digest(path)
                if self.stamp(path) != stamp:
                    raise ValueError(f'Input changed while reading {path}')
                self.files[key] = cached
        if expected and cached[1] != expected:
            raise ValueError(f'Input identity mismatch for {path}')
        return cached[1]

    def sources(self):
        names = subprocess.check_output(
            ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', *SOURCE_DIRS],
            cwd=ROOT).decode().split('\0')
        files = {}
        for name in sorted(set(names) - {''}):
            try:
                files[name] = self.file(ROOT / name)
            except FileNotFoundError:
                files[name] = None
        return tuple(files), files, git_value(ROOT, 'rev-parse', 'HEAD')

    def tool(self, name):
        with self.cache_lock:
            cached = self.tools.get(name)
        if cached is not None:
            return cached
        executable = shutil.which(name, path=self.environment['PATH'])
        if not executable:
            raise FileNotFoundError(f'Missing tool {name} on PATH')
        path = Path(executable)
        files = [path]
        if name == 'cc':
            for argument in ('-print-prog-name=cc1', '-print-prog-name=as', '-print-prog-name=ld',
                             '-print-file-name=libasan.so', '-print-file-name=libubsan.so'):
                found = subprocess.run([executable, argument], env=self.environment, stdout=subprocess.PIPE,
                                       text=True, check=True, timeout=10).stdout.strip()
                resolved = shutil.which(found, path=self.environment['PATH'])
                if Path(found).is_absolute() and Path(found).is_file():
                    files.append(Path(found))
                elif resolved:
                    files.append(Path(resolved))
        with self.cache_lock:
            return self.tools.setdefault(name, files)

    def resolve(self, argument, directory, dependencies):
        if argument.startswith('{artifact:'):
            return str(directory / argument[10:-1])
        if argument.startswith('{dependency:'):
            check_id, file = argument[12:-1].split('/', 1)
            return str(dependencies[check_id] / file)
        if argument.startswith('{shared:'):
            return str(self.paths['ps2dinfo'] / 'tools' / argument[8:-1])
        if argument.startswith('{'):
            key, _, suffix = argument[1:].partition('}')
            return str(self.paths[key] / suffix.lstrip('/'))
        return argument

    def git_basis(self, check):
        number = check.get('historical')
        if number == '49':
            base = git_value(ROOT, 'rev-parse', '--verify', 'origin/main^{commit}')
            if not base:
                raise FileNotFoundError('Missing Git ref origin/main')
            return {'base': base}
        if number in ('50', '51'):
            paths = worktree_paths(ROOT)
            return {'files': {name.as_posix(): self.file(ROOT / name)
                              if (ROOT / name).is_file() and not (ROOT / name).is_symlink() else None
                              for name in paths},
                    'index': git_value(ROOT, 'ls-files', '--stage'),
                    'unstaged_diff': digest_bytes(subprocess.check_output(
                        ['git', 'diff', '--no-ext-diff', '--binary'], cwd=ROOT)) if number == '51' else None}
        return None

    def snapshot(self, check, dependencies, *, probe=False):
        argv = check['argv']
        group = check.get('input_group')
        expected = {INVENTORY: self.identity['inventory_sha256'], HISTORICAL: self.source_files['evidence/m12/checks.json'],
                    self.python: self.identity['python']['sha256']}
        for name in check.get('tools', []):
            expected.update(dict.fromkeys(self.tool(name)))
        items = check['inputs']
        if check['id'] == 'synthetic-suite' and self.paths['image'].exists():
            items = [*items, '{image}']
        # Bind and authenticate the ELF for the retail M24 test when it is present;
        # without it the named public skip contract applies.
        if check['id'] == 'synthetic-suite' and self.paths['elf'].exists():
            items = [*items, '{elf}']
        for item in items:
            if item.startswith('{dependency:') and probe:
                continue
            if group and (item == '{captures}' or item.startswith('local/') and item in
                          [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg in ('--capture-dir', '--runtime', '--pack-runtime')]):
                continue
            path = ROOT / self.resolve(item, ROOT, dependencies)
            if item.startswith('{shared:'):
                expected[path] = self.pins[path.name]
            elif item in ('{elf}', '{image}'):
                media = self.media[ENV_PATHS[item[1:-1]]]
                if self.stamp(path)[2] != media['size']:
                    raise ValueError(f'Wrong input file size for {path}')
                expected[path] = media['sha256']
            elif path.is_dir():
                # Older export trees may use nested snapshots and COMPLETE markers.
                children = [p for p in path.rglob('*') if p.is_file()]
                if not children:
                    raise FileNotFoundError(f'No input files found in {path}')
                expected.update(dict.fromkeys(children))
            else:
                expected[path] = None
        expected.update(saved_inputs(ROOT, group, self.paths))
        if group == 'm17-native':
            record = ROOT / 'evidence/m17/results.json'
            observation = read_json(record)['native_observations'][check['id']]
            for stage in ('before', 'after'):
                expected[ROOT / argv[argv.index('--' + stage) + 1]] = observation[stage + '_sha256']
        # The M8 connected replay also uses auxiliary legacy captures.
        if group == 'm8-connected':
            capture_dir = ROOT / 'local/m8/captures'
            for path in capture_dir.glob('*.json'):
                expected.setdefault(path, None)
        hashes = {str(path): self.file(path, pin, probe=probe) for path, pin in expected.items()}
        if probe:
            return hashes
        basis = {
            'check': check, 'source_sha256': self.identity['hungry_ghosts']['source_sha256'],
            'revision': self.revision, 'environment': self.environment, 'platform': list(os.uname()),
            'python_version': sys.version, 'git': self.git_basis(check),
            'ps2dinfo_revision': self.shared_revision if any(item.startswith('{shared:') for item in check['inputs']) else None,
            'inputs': hashes,
        }
        return basis

    def require_current(self, check, dependencies, basis):
        _, current, revision = self.sources()
        if revision != self.revision or current != self.source_files:
            raise ValueError('Repository sources changed during verification')
        if basis['ps2dinfo_revision'] and git_value(self.paths['ps2dinfo'], 'rev-parse', 'HEAD') != self.shared_revision:
            raise ValueError('Shared tool revision changed during verification')
        if self.snapshot(check, dependencies) != basis:
            raise ValueError('Check configuration, tools or inputs changed during verification')

    def argv(self, check, directory, dependencies):
        argv = [self.resolve(arg, directory, dependencies) for arg in check['argv']]
        if argv[0] == 'python':
            switches = {'-B', '-u', '-s'}
            first = next(i for i in range(1, len(argv)) if argv[i] not in switches)
            return [str(self.python), '-s', '-B', '-u', *argv[first:]]
        return argv

    def child_environment(self, directory, authenticated_image, authenticated_elf=True):
        return {**self.environment, **{name: str(self.paths[key]) for key, name in ENV_PATHS.items()
                                         if (key != 'image' or authenticated_image)
                                         and (key != 'elf' or authenticated_elf)},
                'HG_VERIFY_BUILD_DIR': str(directory / 'build'),
                'PYTHONPYCACHEPREFIX': str(directory / 'pycache')}


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


class Stop:
    signum = None

    def handle(self, signum, frame):
        self.signum = signum

    def check(self):
        if self.signum:
            raise Interrupted(signal.Signals(self.signum).name)


@contextmanager
def interruptible():
    stop = Stop()
    handlers = {sig: signal.signal(sig, stop.handle) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield stop
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


@contextmanager
def run_lock(folder):
    with (folder / 'writer.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f'Run {folder.name} already has a writer') from exc
        yield lock.fileno()


def run_child(argv, directory, env, lock_fd, stop, timeout):
    def send(child, signum):
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    locks = (lock_fd,) if isinstance(lock_fd, int) else lock_fd
    with (directory / 'output.log').open('x') as log:
        stop.check()
        with subprocess.Popen(argv, cwd=ROOT, env={**env, 'TMPDIR': str(directory)},
                              stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                              start_new_session=True, pass_fds=locks) as child:
            deadline = time.monotonic() + timeout
            try:
                while True:
                    stop.check()
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f'Verifier exceeded {timeout} seconds')
                    try:
                        code = child.wait(timeout=0.2)
                        try:
                            os.killpg(child.pid, 0)
                        except ProcessLookupError:
                            return code
                        send(child, signal.SIGKILL)
                        raise ValueError('Verifier left running child processes')
                    except subprocess.TimeoutExpired:
                        continue
            except (Interrupted, TimeoutError):
                send(child, stop.signum or signal.SIGTERM)
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                finally:
                    send(child, signal.SIGKILL)
                    child.wait()
                raise


def artifacts(directory):
    if directory.is_symlink():
        raise ValueError('Attempt directory must not be a symlink')
    sealed = {}
    for path in sorted(directory.rglob('*')):
        relative = path.relative_to(directory).as_posix()
        if relative in ('attempt.json', 'receipt.json'):
            continue
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f'Invalid attempt artifact {relative}')
        before = Inputs.stamp(path)
        sealed[relative] = digest(path)
        if before != Inputs.stamp(path):
            raise ValueError(f'Artifact changed while sealing {relative}')
    return sealed


def reusable(folder, basis):
    attempts = sorted(folder.glob('attempt-*'), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if not attempts:
        return None
    # Tied directory timestamps cannot establish which attempt is newest.
    # Fail closed instead of reusing an earlier completed receipt.
    if len(attempts) > 1 and attempts[0].stat().st_mtime_ns == attempts[1].stat().st_mtime_ns:
        return None
    directory = attempts[0]
    try:
        row = read_json(directory / 'receipt.json')
        seal = row.pop('receipt_sha256')
        if (row['schema'] == STATE_SCHEMA and row['verdict'] in ACCEPTED
                and canonical_hash(row) == seal and row['fingerprint'] == basis
                and row['artifacts'] == artifacts(directory)):
            if basis.get('check') and (
                    row['id'] != basis['check']['id'] or row['expected'] != basis['check']['expected']
                    or row['expected'] != row['verdict']
                    or row['attempt'] != directory.relative_to(folder.parent.parent).as_posix()
                    or any(name not in row['artifacts'] for name in basis['check'].get('outputs', []))):
                return None
            return {**row, 'receipt_sha256': seal, 'reused': True}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def attempt(folder, check, inputs, dependencies, lock_fd, stop):
    check_dir = folder / 'checks' / check['id']
    check_dir.mkdir(parents=True, exist_ok=True)
    started, start = now(), time.monotonic()
    preflight = None
    basis = None
    try:
        basis = inputs.snapshot(check, dependencies)
        inputs.require_current(check, dependencies, basis)
        row = reusable(check_dir, basis)
        stop.check()
        if row:
            return row | {'reuse_seconds': round(time.monotonic() - start, 3)}
    except FileNotFoundError as exc:
        preflight = MISSING, str(exc)
    except (ValueError, KeyError, TypeError) as exc:
        preflight = INVALID, str(exc)
    except (OSError, subprocess.SubprocessError) as exc:
        preflight = ERROR, str(exc)
    except Interrupted as exc:
        preflight = INTERRUPTED, str(exc)

    previous_attempts = len(list(check_dir.glob('attempt-*')))
    directory = Path(tempfile.mkdtemp(prefix='attempt-', dir=check_dir))
    argv = inputs.argv(check, directory, dependencies)
    row = {
        'schema': STATE_SCHEMA, 'id': check['id'], 'historical': check.get('historical'),
        'name': check['name'], 'evidence': check['evidence'], 'expected': check['expected'],
        'attempt': directory.relative_to(folder).as_posix(), 'started_utc': started, 'number': previous_attempts + 1,
        'argv': argv, 'cwd': str(ROOT), 'fingerprint': basis, 'exit_code': None, 'reused': False,
        'executed': False, 'child_seconds': None,
    }
    atomic_json(directory / 'attempt.json', row)
    if preflight:
        verdict, reason = preflight
        details = {'phase': 'preflight', 'basis': reason}
    else:
        launched = time.monotonic()
        try:
            row['executed'] = True
            image_bound = str(inputs.paths['image']) in basis['inputs']
            elf_bound = str(inputs.paths['elf']) in basis['inputs']
            # Discovery gates export HG_ELF only after authenticating it; other
            # verifiers keep the historical environment.
            env = inputs.child_environment(directory, image_bound,
                                           elf_bound or check['result'] != 'discovery')
            row['exit_code'] = run_child(argv, directory, env, lock_fd, stop,
                                         timeout=check.get('timeout', 180))
            row['child_seconds'] = round(time.monotonic() - launched, 3)
            stop.check()
            inputs.require_current(check, dependencies, basis)
            verdict, details = classify(check, directory, row['exit_code'], authenticated_image=image_bound,
                                        authenticated_elf=elf_bound)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            verdict, details = ERROR, {'basis': str(exc)}
        except Interrupted as exc:
            verdict, details = INTERRUPTED, {'basis': str(exc)}
        if row['child_seconds'] is None:
            row['child_seconds'] = round(time.monotonic() - launched, 3)
    try:
        sealed = artifacts(directory)
    except (OSError, ValueError) as exc:
        sealed = {}
        verdict, details = ERROR, {'basis': str(exc)}
    if verdict == check['expected'] and any(name not in sealed for name in check.get('outputs', [])):
        verdict, details = ERROR, {'basis': 'A required artifact was not sealed'}
    row.update(verdict=verdict, details=details, ended_utc=now(), seconds=round(time.monotonic() - start, 3),
               artifacts=sealed)
    row['receipt_sha256'] = canonical_hash(row)
    atomic_json(directory / 'receipt.json', row)
    return row


def brief(check, verdict, reason=None):
    return {**{key: check.get(key) for key in ('id', 'historical', 'name', 'evidence', 'expected', 'argv')},
            'verdict': verdict, 'details': {'basis': reason} if reason else {}}


def summary(folder, manifest, checks, identity, rows, state, session):
    checks = [rows.get(check['id']) or brief(check, UNRUN) for check in checks]
    counts = dict(Counter(row['verdict'] for row in checks))
    accepted = sum(row['verdict'] == row['expected'] for row in checks)
    reused = sum(row.get('reused', False) for row in checks)
    executed = sum(row.get('executed', False) and not row.get('reused') for row in checks)
    full = manifest['profile'] in ('full', 'historical', 'current')
    full_verdict = ('passed' if state == 'complete' and accepted == len(checks) else 'incomplete') if full else 'unrun'
    result = {'schema': STATE_SCHEMA, 'run': manifest['run'], 'profile': manifest['profile'],
              'state': state, 'updated_utc': now(), 'identity': identity, 'counts': counts,
              'accepted': accepted, 'reused': reused, 'executed': executed, 'checks': checks,
              'historical_checks': sum(row['historical'] is not None for row in checks),
              'full_verdict': full_verdict,
              'session_seconds': round(time.monotonic() - session['start'], 3),
              'jobs': session['jobs'],
              'first_actionable_seconds': session['first_actionable_seconds'],
              'first_actionable_check': session['first_actionable_check'],
              'historical_full_seconds': manifest['historical_full_seconds'],
              'child_seconds': round(sum((row.get('child_seconds') or 0) for row in checks if not row.get('reused')), 3),
              'retries': sum(max(0, len(list((folder / 'checks' / row['id']).glob('attempt-*'))) - 1) for row in checks),
              'session_history': session['history'],
              'human_intervention': {'resume_commands': session['resumes'],
                                     'interruptions': sum(row['state'] == 'interrupted' for row in session['history'])
                                     + int(state == 'interrupted')}}
    atomic_json(folder / 'summary.json', result)
    lines = [f'# M12 {manifest["profile"]} verification — {state}', '',
             f'{accepted}/{len(checks)} gates accepted. {executed} executed, {reused} reused, {result["retries"]} retries.',
             f'Historical coverage {result["historical_checks"]}/79. Full verdict **{full_verdict}**.',
             f'Session ({session["jobs"]} workers) {result["session_seconds"]:.2f}s; child commands {result["child_seconds"]:.2f}s; '
             f'historical full run {result["historical_full_seconds"]:.2f}s.',
             'Verdicts ' + ', '.join(f'{name} {n}' for name, n in sorted(counts.items())) + '.']
    if state == 'complete' and not reused and session['jobs'] == 1:
        overhead = result['session_seconds'] - result['child_seconds']
        lines.append(f'Runner time outside child commands {overhead:.2f}s.')
    if session['first_actionable_check']:
        lines.append(f'First actionable result after {session["first_actionable_seconds"]:.2f}s '
                     f'({session["first_actionable_check"]}).')
    for label, kinds in (
        ('Synthetic and local integrity', {'synthetic', 'structure', 'local-integrity'}),
        ('Original bytes and instruction-model comparisons', {'original-bytes', 'differential', 'differential-control'}),
        ('Saved original-game runtime comparisons', {'saved-runtime'}),
    ):
        lines += ['', f'## {label}', '', '| Historical | Check | Verdict | Attempt duration |',
                  '|---|---|---|---|']
        for row in checks:
            if row['evidence'] not in kinds:
                continue
            verdict = row['verdict'] + (' (reused)' if row.get('reused') else '')
            if 'attempt' in row:
                verdict = f'[{verdict}]({row["attempt"]}/receipt.json)'
            elapsed = f'{row["seconds"]:.2f}s' if 'seconds' in row else '—'
            lines.append(f'| {row["historical"] or "—"} | `{row["id"]}` | {verdict} | {elapsed} |')
    lines += ['', '## Measurements', '']
    for row in checks:
        details = row['details']
        if row['id'] == 'grounded-saved-runtime' and 'controls' in details:
            controls = Counter(item['verdict'] for item in details['controls'].values())
            lines += [f'- Grounded replay measured {details["stages"]} stages across {details["native_targets"]} '
                      f'native captures, {details["registers"]} register comparisons and {details["handoffs"]} handoffs. '
                      f'{controls.get(REJECTED, 0)} mutation rejections, {controls.get(PASSED, 0)} clean '
                      f'null controls and {controls.get("expected-refusal", 0)} expected refusals.']
        if row['id'] == 'grounded-differential' and 'counts' in details:
            measured = details['counts']
            lines.append(f'- Grounded instruction differential measured {measured["passed"]} equal, '
                         f'{measured["unsupported"]} expected refusals, {measured["failed"]} divergences '
                         f'and {measured["error"]} harness errors.')
        if row['id'] == 'grounded-m11-frames' and 'stages' in details:
            lines.append(f'- M11 frame regression measured {details["equal_stages"]}/{details["stages"]} equal checkpoints.')
        if row['id'] == 'synthetic-suite' and 'tests' in details:
            lines.append(f'- Synthetic suite ran {details["tests"]} tests.')
    lines += ['', '## Commands and blockers', '']
    for row in checks:
        command = shlex.join(row['argv'])
        lines.append(f'- **{row["id"]}** `{command.replace(chr(96), chr(39))}`')
        if row['verdict'] != row['expected']:
            reason = row['details'].get('basis', row['verdict']).replace('\n', ' ')
            lines.append(f'  - {reason}')
    atomic_text(folder / 'summary.md', '\n'.join(lines) + '\n')
    return result


def plan(profile, options):
    checks = inventory(INVENTORY, HISTORICAL, profile)
    inputs = Inputs(options, Stop())
    rows = []
    for check in checks:
        dependencies = {name: Path(f'<{name}-attempt>') for name in check.get('requires', [])}
        row = {**check, 'argv': inputs.argv(check, Path('<attempt>'), dependencies), 'status': 'ready'}
        try:
            files = inputs.snapshot(check, dependencies, probe=True)
            row['resolved_inputs'] = files
            row['content_hashes_deferred'] = sum(value is None for value in files.values())
            inputs.git_basis(check)
        except FileNotFoundError as exc:
            row.update(status=MISSING, blocker=str(exc))
        except (ValueError, KeyError, TypeError) as exc:
            row.update(status=INVALID, blocker=str(exc))
        except (OSError, subprocess.SubprocessError) as exc:
            row.update(status=ERROR, blocker=str(exc))
        rows.append(row)
    print(json.dumps({'profile': profile, 'identity': inputs.identity,
                      'historical_checks': sum('historical' in row for row in rows), 'checks': rows}, indent=2))


def dispatch_order(pending, jobs):
    items = list(pending.items())
    if jobs <= 1:
        return items
    rank = {name: i for i, name in enumerate(PARALLEL_HEAVY_ORDER)}
    return sorted(items, key=lambda pair: (0 if pair[0] in rank else 1, rank.get(pair[0], 0)))


def schedule(checks, jobs, stop, run, complete):
    """Dispatch ready checks. Only the coordinator mutates the run summary."""
    pending = {check['id']: check for check in checks}
    active = {}
    rows = {}

    def finish(check, row):
        rows[check['id']] = row
        complete(check, row, active, len(pending))

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        while active or pending and not stop.signum:
            for name, check in dispatch_order(pending, jobs):
                if stop.signum or len(active) >= jobs:
                    break
                required = check.get('requires', [])
                if not set(required + check.get('after', [])) <= rows.keys():
                    continue
                parents = [rows[parent] for parent in required]
                blocked = [row['id'] for row in parents if row['verdict'] != row['expected']]
                if blocked:
                    del pending[name]
                    finish(check, brief(check, SKIPPED, 'Blocked by ' + ', '.join(blocked)))
                    continue
                exclusive = bool(check.get('exclusive')) or check['result'] not in PARALLEL_RESULTS
                if active and (exclusive or any(
                        bool(row.get('exclusive')) or row['result'] not in PARALLEL_RESULTS
                        for row in active.values())):
                    # Drain before starting an exclusive gate.
                    continue
                del pending[name]
                active[pool.submit(run, check, parents)] = check
                if exclusive:
                    break
            if active:
                ready, _ = wait(active, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in sorted(ready, key=lambda item: active[item]['id']):
                    check = active.pop(future)
                    try:
                        row = future.result()
                    except Exception as exc:
                        row = brief(check, ERROR, f'Worker failed: {type(exc).__name__}: {exc}')
                    finish(check, row)
            elif pending and not stop.signum and not any(
                    set(check.get('requires', []) + check.get('after', [])) <= rows.keys()
                    for check in pending.values()):
                raise ValueError('Inventory has a missing or cyclic dependency')
    return rows


def finish_identity(folder, checks, inputs, rows):
    """Recheck accepted evidence before aggregating the final verdict."""
    for check in checks:
        row = rows[check['id']]
        if row['verdict'] != row['expected']:
            continue
        parents = [rows[name] for name in check.get('requires', [])]
        dependencies = {parent['id']: folder / parent['attempt'] for parent in parents}
        try:
            if any(parent['verdict'] != parent['expected'] for parent in parents):
                raise ValueError('A required receipt became invalid')
            inputs.require_current(check, dependencies, row['fingerprint'])
            if artifacts(folder / row['attempt']) != row['artifacts']:
                raise ValueError('Attempt artifacts changed before run completion')
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            rows[check['id']] = {**row, 'verdict': ERROR, 'recorded_verdict': row['verdict'],
                                 'details': {'basis': str(exc)}}


def execute(folder, manifest, checks, start, resuming, jobs):
    with run_lock(RUN_ROOT) as checkout_lock, run_lock(folder) as lock, interruptible() as stop:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        print(f'Run {folder.name}', flush=True)
        history = read_json(folder / 'sessions.json') if (folder / 'sessions.json').is_file() else []
        session = {'start': start, 'first_actionable_seconds': None, 'first_actionable_check': None,
                   'history': history, 'jobs': jobs, 'resumes': sum(row['resume'] for row in history) + int(resuming)}
        record = {'started_utc': now(), 'resume': resuming, 'jobs': jobs, 'state': 'running'}
        atomic_json(folder / 'sessions.json', [*history, record])
        rows = {}
        summary(folder, manifest, checks, {}, rows, 'running', session)
        inputs = None
        try:
            inputs = Inputs(manifest['options'], stop)
            def run(check, parents):
                dependencies = {row['id']: folder / row['attempt'] for row in parents}
                for row in parents:
                    if artifacts(dependencies[row['id']]) != row['artifacts']:
                        return brief(check, SKIPPED, f'Required gate artifact changed for {row["id"]}')
                return attempt(folder, check, inputs, dependencies, (checkout_lock, lock), stop)

            def complete(check, row, active, pending):
                rows[check['id']] = row
                if row['verdict'] != row['expected'] and not session['first_actionable_check']:
                    session.update(first_actionable_check=check['id'],
                                   first_actionable_seconds=round(time.monotonic() - start, 3))
                elapsed = time.monotonic() - start
                remaining = len(active) + pending
                eta = remaining * elapsed / len(rows)
                status = Counter(item['verdict'] for item in rows.values())
                print(f'{check["id"]} {row["verdict"]} | active {len(active)}, completed {len(rows)}, '
                      f'pending {pending}, blocked {status[SKIPPED] + status[MISSING] + status[INVALID]}, '
                      f'reused {sum(item.get("reused", False) for item in rows.values())}, '
                      f'passed {status[PASSED]}, rejected as expected {status[REJECTED]}, '
                      f'failed/interrupted {len(rows) - sum(status[k] for k in (PASSED, REJECTED, SKIPPED, MISSING, INVALID))}, '
                      f'elapsed {elapsed:.1f}s, throughput-based ETA ~{eta:.1f}s', flush=True)
                summary(folder, manifest, checks, inputs.identity, rows, 'running', session)

            schedule(checks, jobs, stop, run, complete)
            if not stop.signum:
                finish_identity(folder, checks, inputs, rows)
        except Interrupted:
            pass
        state = 'interrupted' if stop.signum else 'complete'
        result = summary(folder, manifest, checks, inputs.identity if inputs else {}, rows, state, session)
        record.update(state=state, seconds=result['session_seconds'], executed=result['executed'], reused=result['reused'],
                      first_actionable_seconds=result['first_actionable_seconds'],
                      first_actionable_check=result['first_actionable_check'])
        atomic_json(folder / 'sessions.json', [*history, record])
        print(f'{result["accepted"]}/{len(checks)} gates accepted, {result["reused"]} reused. {folder / "summary.md"}')
        if stop.signum:
            return 128 + stop.signum
        return int(result['accepted'] != len(checks))


def main(argv=None):
    start = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'run'):
        command = commands.add_parser(name)
        command.add_argument('--profile', choices=PROFILES, default='focused')
        command.add_argument('--config', type=Path)
    for name in ('resume', 'summarize'):
        commands.add_parser(name).add_argument('--run', required=True)
    commands.choices['run'].add_argument('--jobs', type=int, choices=JOBS, default=1)
    commands.choices['resume'].add_argument('--jobs', type=int, choices=JOBS)
    args = parser.parse_args(argv)
    try:
        if args.command == 'plan':
            plan(args.profile, configured_paths(ROOT, args.config))
            return 0

        root = RUN_ROOT.resolve()
        local = RUN_ROOT.parent.resolve()
        if not local.is_relative_to(ROOT.parent) or root != local / RUN_ROOT.name:
            raise ValueError('Verification outputs must stay under the project’s local directory')
        if args.command == 'run':
            profile = args.profile
            checks = inventory(INVENTORY, HISTORICAL, profile)
            configured = configured_paths(ROOT, args.config)
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            prefix = datetime.now(timezone.utc).strftime('m12-%Y%m%dT%H%M%SZ-')
            folder = Path(tempfile.mkdtemp(prefix=prefix, dir=root))
            historic = read_json(HISTORICAL)['regression_inventory']
            seconds = (datetime.fromisoformat(historic['completed_utc']) -
                       datetime.fromisoformat(historic['started_utc'])).total_seconds()
            manifest = {'schema': STATE_SCHEMA, 'run': folder.name, 'profile': profile,
                        'historical_full_seconds': round(seconds, 3), 'created_utc': now(),
                        'options': configured, 'cwd': str(ROOT), 'jobs': args.jobs}
            atomic_json(folder / 'manifest.json', manifest)
        else:
            if not re.fullmatch(r'm12-[A-Za-z0-9_-]+', args.run):
                raise ValueError('Use a verification run ID')
            folder = root / args.run
            if folder.is_symlink():
                raise ValueError('Run directory must be a local directory')
            manifest = read_json(folder / 'manifest.json')
            if (manifest['run'], manifest['cwd']) != (folder.name, str(ROOT)):
                raise ValueError('Run manifest does not match this worktree')
            if args.command == 'summarize':
                print((folder / 'summary.md').read_text(encoding='utf-8'), end='')
                return 0
            if manifest['schema'] == 'hg-m12-verification/v1':
                raise ValueError('Stage 1 receipts predate the full inventory and input binding. Start a new run.')
            if manifest['schema'] != STATE_SCHEMA or manifest['profile'] not in PROFILES:
                raise ValueError('Unsupported verification run manifest')
            checks = inventory(INVENTORY, HISTORICAL, manifest['profile'])
        jobs = args.jobs or manifest.get('jobs', 1)
        if type(jobs) is not int or jobs not in JOBS:
            raise ValueError('Invalid worker count in run manifest')
        return execute(folder, manifest, checks, start, args.command == 'resume', jobs)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f'M12 verification: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
