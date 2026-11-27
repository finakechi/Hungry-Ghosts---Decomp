"""Cloud-only scheduler benchmark using independently checked synthetic EE words."""
import json
from pathlib import Path
import resource
import statistics
import sys
import tempfile
import time

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / 'tools'))
import hg_verify as runner

def classify_fixture(check, directory, code, **_):
    if code:
        return runner.ERROR, {'basis': 'Synthetic CPU case failed'}
    row = runner.read_json(directory / 'checks.json')
    if row['cases'] != 25000 or row['seed'] != int(check['argv'][-2]) or len(row['checksum']) != 64:
        return runner.ERROR, {'basis': 'Incomplete synthetic CPU coverage'}
    return runner.PASSED, row


CHILD = """
import hashlib, json, random, struct, sys
from ee_subset import Machine
seed, output = int(sys.argv[1]), sys.argv[2]
randomizer = random.Random(seed)
h = hashlib.sha256()
code = struct.pack('<IIIIII', 0x24420001, 0x10000002, 0x00021043,
                   0x24420100, 0x03e00008, 0x24420004)
for _ in range(25000):
    x = randomizer.getrandbits(32)
    cpu = Machine()
    cpu.put(0x1000, code)
    cpu.reg[2] = x
    word = (x + 1) % (1 << 32)
    signed = word if word < (1 << 31) else word - (1 << 32)
    want = signed // 2 + 4
    got = cpu.run(0x1000)
    if got != want:
        raise AssertionError((seed, x, got, want))
    h.update(struct.pack('<i', got))
with open(output, 'x') as stream:
    json.dump(dict(seed=seed, cases=25000, checksum=h.hexdigest()), stream)
"""


def run_group(jobs):
    checks = [dict(id=f'ee-{i:02}', name='Synthetic EE differential', evidence='synthetic',
                   result='fixture-ee', expected=runner.PASSED, inputs=[], outputs=['checks.json', 'output.log'],
                   argv=['python', '-c', CHILD, str(0x15046 + i), '{artifact:checks.json}'])
              for i in range(16)]
    stop = runner.Stop()
    opts = runner.configured_paths(ROOT, None)
    with tempfile.TemporaryDirectory(prefix='hg-benchmark-') as tmp:
        folder = Path(tmp)
        inputs = runner.Inputs(opts, stop)
        inputs.environment['PYTHONPATH'] = str(ROOT / 'tools')
        start = time.monotonic()
        with runner.run_lock(folder) as lock:
            rows = runner.schedule(checks, jobs, stop, lambda c, _: runner.attempt(folder, c, inputs, {}, lock, stop),
                                   lambda *_: None)
        seconds = round(time.monotonic() - start, 3)
        assert len(rows) == len(checks)
        normalized = []
        for check in checks:
            row = rows[check['id']]
            assert row['verdict'] == runner.PASSED, row['details']
            assert (folder / row['attempt'] / 'checks.json').is_file()
            argv = [str(arg).replace(str(folder / row['attempt']), '<attempt>') for arg in row['argv']]
            normalized.append({key: row[key] for key in (
                'id', 'evidence', 'fingerprint', 'exit_code', 'verdict', 'details', 'artifacts')} | {'argv': argv})
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return seconds, peak, runner.canonical_hash(normalized)


def main():
    # Standalone synthetic fixture. Production gate contracts and the safe
    # concurrency allowlist are unchanged when invoking hg_verify.py directly.
    runner.classify = classify_fixture
    runner.PARALLEL_RESULTS = {'fixture-ee'}
    timings = {jobs: [] for jobs in runner.JOBS}
    hashes = set()
    for order in (runner.JOBS, tuple(reversed(runner.JOBS)), (2, 8, 1, 4)):
        for jobs in order:
            seconds, peak, seal = run_group(jobs)
            print(jobs, seconds, peak, seal, flush=True)
            hashes.add(seal)
            timings[jobs].append(seconds)
    assert len(hashes) == 1
    print(json.dumps(dict(timings=timings, medians={jobs: statistics.median(rows)
                                                  for jobs, rows in timings.items()},
                          semantic_receipt_sha256=hashes.pop(), per_child_peak_rss_kib=peak)))


if __name__ == '__main__':
    main()
