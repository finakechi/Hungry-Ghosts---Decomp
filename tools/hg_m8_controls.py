#!/usr/bin/env python3
"""Re-run the M8 actor setup and update-list mutation checks from the M12 inventory."""
import argparse
import json
from pathlib import Path
import sys

from hg_evidence_safety import digest
import verify_actor_lists as lists

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from resource_harness import library
from test_actor_setup import ActorSetupTests, configure as configure_setup
from test_actor_lists import configure as configure_lists

SETUP = {
    'lost-fifth-argument': (
        'hg_actor_setup_animation(e,actor,mode,sequence);',
        '(void)mode;hg_actor_setup_animation(e,actor,0,sequence);',
        'test_six_arguments_and_reloaded_handle', 'Lists differ:', 'First differing element 5'),
    'early-timer-expiry': (
        'if(remaining==0) {', 'if(remaining<=1) {',
        'test_input_transition_followed_by_held_release_and_expiry', '0 != 2', '0 != 2'),
    'lost-active-dispatch': (
        'if(pc==0x18c280) {hg_actor_active(&p->env,a);return 0;}',
        'if(pc==0x18c280) {return 0;}',
        'test_connected_area_setup_and_active_dispatch', "('call', 1619648, 5242880, 5243600) not found", 'not found'),
}
LISTS = {
    'late-next': (
        'uint32_t callback=r32(e,actor+0x1c),next=r32(e,actor+0x24);\n        call(e,callback,actor,0,0,0);',
        'uint32_t callback=r32(e,actor+0x1c);\n        call(e,callback,actor,0,0,0);\n        uint32_t next=r32(e,actor+0x24);',
        'hg_actor_update_main/1: external call trace differs'),
    'counter-overwrite': (
        'w16(e,0x3eaed8,r16(e,0x3eaed8)+1);', 'w16(e,0x3eaed8,1);',
        'hg_actor_update_main/1: memory differs at 003eaed8:'),
}


def mutant(original, directory, name, old, new):
    if original.count(old) != 1:
        raise ValueError(f'{name}: expected exactly one mutation site')
    source = directory / (name + '.c')
    with source.open('x') as stream:
        stream.write(original.replace(old, new))
    return source, library(source=source, output=source.with_suffix('.so'))


def actor_setup(original, directory):
    baseline = configure_setup(library(output=directory / 'baseline.so'))
    for name, (old, new, test, prefix, marker) in SETUP.items():
        case = ActorSetupTests(test)
        case.lib = baseline
        getattr(case, test)()
        source, loaded = mutant(original, directory, name, old, new)
        case.lib = configure_setup(loaded)
        try:
            getattr(case, test)()
        except AssertionError as exc:
            reason = str(exc)
            result = 'rejected' if reason.startswith(prefix) and marker in reason else 'error'
        else:
            result, reason = 'ESCAPED', 'Mutation left the targeted assertion unchanged'
        yield dict(control=name, result=result, compiled=True, test=test, reason=reason[:500],
                   source_sha256=digest(source))


def actor_lists(original, directory, elf, shared):
    if digest(elf) != lists.EXPECTED:
        raise ValueError('Wrong original ELF')
    data = elf.read_bytes()
    segments, _ = lists.shared_module(shared).segments_from_elf(data)
    functions = {0x1430a0: ('hg_actor_update_main', 0)}
    baseline = configure_lists(library(output=directory / 'baseline.so'))
    lists.run(baseline, data, segments, functions=functions)
    for name, (old, new, prefix) in LISTS.items():
        source, loaded = mutant(original, directory, name, old, new)
        try:
            lists.run(configure_lists(loaded), data, segments, functions=functions)
        except AssertionError as exc:
            reason = str(exc)
            result = 'rejected' if reason.startswith(prefix) else 'error'
        else:
            result, reason = 'ESCAPED', 'Mutation left the targeted comparison unchanged'
        yield dict(control=name, result=result, compiled=True, reason=reason[:500], source_sha256=digest(source))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('group', choices=['setup', 'lists'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--elf', type=Path)
    parser.add_argument('--ps2dinfo', type=Path)
    args = parser.parse_args()
    if args.group == 'lists' and (not args.elf or not args.ps2dinfo):
        parser.error('lists requires --elf and --ps2dinfo')
    directory = args.output.parent / (args.output.stem + '-controls')
    directory.mkdir()
    original = (ROOT / 'src/resource_loader.c').read_text()
    controls = list(actor_setup(original, directory) if args.group == 'setup'
                    else actor_lists(original, directory, args.elf, args.ps2dinfo))
    ok = all(row['result'] == 'rejected' for row in controls)
    report = dict(schema='hg-m8-negative-controls/v1', result='passed' if ok else 'failed', controls=controls)
    with args.output.open('x') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(report['result'])
    return int(not ok)


if __name__ == '__main__':
    raise SystemExit(main())
