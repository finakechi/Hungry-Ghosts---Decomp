"""Report contracts for the M25 world-session gates.

m25-world-session: the measured report must equal the tracked receipt
(evidence/m25/world-session.json) exactly, come from a clean sanitized run
with no re-derived problem, and its receipt-level invariants are re-derived
here as well: four cycles (two per scene, interleaved), at least 120
post-readiness updates each, no checker failure, clean teardown pools,
cycle-relative parity, every control detected by its intended check with no
forbidden check, and every invalid input rejected as expected.

m25-authentic: exact equality with evidence/m25/authentic-dependencies.json
plus identity, bridge, cross-run class, fault and boundary checks.

m25-live: exact equality with evidence/m25/live-continuity.json (one
continuous original PCSX2 run) plus re-checked invariants: every stop's image
and debugger hash verified by the verifier, ordered counters, the same-run
tick-0 stop equal to M21 area1-active and the M8 entry, the same-run
00158a10 return equal to the M8 return and to C 00158a10 applied to the
same-run tick 0, original instructions reproducing the same-run 0012f8d4
stop, a contiguous walk window, and the unobserved stops still declared.
A zero exit status or a success field is never sufficient on its own.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ELF = 'a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61'
ISO = '8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e'
KINDS = {'m25-world-session', 'm25-authentic', 'm25-live'}
PARALLEL = {'m25-world-session'}
COORDINATOR = ['0031bb80', '00198f60', '0017f6d0', '0017f2d0', '00158560', '0012c790', '0013b6e0', '001430a0',
               '00143100', '0012bc70', '0018c520', '00143150', '001431a0', '0025c170', '00182480', '0013ce80',
               '00137890', '00189be0']


def _session(check, row, read, need, same):
    need(row['schema'] == 'hg-m25-world-session-verification/v1' and row['exit_code'] == 0 and row['stderr'] == ''
         and row['sanitized'] is ('--sanitize' in check['argv']) and row['failure'] is None
         and row['problems'] == [] and row['matches_evidence'] is True,
         'M25 session build, exit, sanitizer or re-derivation')
    report = row['report']
    same(report, read(ROOT / 'evidence/m25/world-session.json'), 'M25 world-session receipt')
    cycles = report['cycles']
    need([c['scene'] for c in cycles] == ['lantern-court', 'terrace-gallery'] * 2
         and [c['cycle'] for c in cycles] == [1, 1, 2, 2], 'M25 cycle schedule')
    for c in cycles:
        need(c['failures'] == [] and c['enter'] == c['step'] == c['teardown'] == 'OK'
             and c['post_ready_updates'] >= 120 and c['setup_effects_in_order'] == 12
             and c['coordinator_ticks_checked'] == c['rows'] and c['motion']['slide_frames'] > 0
             and c['motion']['floor_changes'] > 0
             and c['after_teardown'] == {'free_nodes': 2048, 'owned_nodes': 0, 'pool_free': [286, 40, 256]},
             f'M25 cycle {c["scene"]}#{c["cycle"]} not proved')
    for a, b in ((0, 2), (1, 3)):
        x, y = cycles[a], cycles[b]
        need(x['trace_sha256'] == y['trace_sha256'] or report['rederived']['parity'][x['scene']] is True,
             'M25 cycle-relative parity')
        need(x['ledger_by_op'] == y['ledger_by_op'] and x['first_tick'] == y['first_tick'] and x['final'] == y['final'],
             'M25 cycle 2 checkpoints differ')
    need(all(report['rederived']['parity'].values()), 'M25 parity not re-derived')
    need(report['checker_coordinator_order'] == COORDINATOR, 'M25 checker coordinator order')
    controls = report['controls']
    need(len(controls) == 14 and all(
        k['detected'] is True and k['intended'] in k['fired'].split(',')
        and (k['forbidden'] is None or k['forbidden'] not in k['fired'].split(',')) for k in controls),
        'M25 controls not detected by their intended checks')
    invalid = report['invalid_inputs']
    need(len(invalid) >= 30 and all(i['expected'] == i['actual'] for i in invalid), 'M25 invalid inputs')
    own = report['ledgers']['ownership']
    need(own['actor_alloc'] == own['actor_release'] and own['node_alloc'] == own['node_release']
         and report['ledgers']['provenance']['unknown'] == 0, 'M25 ledgers unbalanced')
    return {'cycles': len(cycles), 'updates': sum(c['post_ready_updates'] for c in cycles),
            'controls': len(controls), 'invalid_inputs': len(invalid)}


def _authentic(row, read, need, same):
    same(row, read(ROOT / 'evidence/m25/authentic-dependencies.json'), 'M25 authentic dependencies')
    ident = row['identity']
    need(row['elf_sha256'] == ELF and row['iso_sha256'] == ISO and ident['matches_tracked_build'] is True
         and ident['word_xor_crc'] == '91AA4B28' and ident['entry'] == '0x00100008', 'M25 identity')
    need(row['original_bytes']['coordinator_0012f850']['order'] == COORDINATOR, 'M25 coordinator order')
    need(row['cross_run_area1_active_vs_m8_entry']['same_checkpoint_class'] is True
         and row['bridge_00158a10']['passed'] is True
         and all(d['differing'] == 0 for d in row['bridge_00158a10']['domains'].values()), 'M25 bridge')
    need(row['fault_00158560_area1_active']['domain_faults'] == ['0x30'], 'M25 fault 0x30')
    need(row['boundary']['earliest_unsupported_dependency'].startswith('001e3440'), 'M25 boundary')
    return {'captures': len(row['captures']), 'bridge_domains': len(row['bridge_00158a10']['domains'])}


def _zero(domains):
    return bool(domains) and all(d['differing'] == 0 for d in domains.values())


def _live(row, read, need, same):
    same(row, read(ROOT / 'evidence/m25/live-continuity.json'), 'M25 live continuity')
    need(row['schema'] == 'hg-m25-live-continuity/v1' and row['elf_sha256'] == ELF and row['synthetic_only'] is False
         and row['passed'] is True and all(row['checks'].values()), 'M25 live identity or checks')
    cross = row['cross_run']
    need(_zero(cross['tick0_vs_m21_area1_active']) and _zero(cross['tick0_vs_m8_entry'])
         and _zero(cross['return_00158a10_vs_m8_return']) and _zero(row['c_bridge_same_run']['domains'])
         and _zero(row['original_instructions_same_run']['domains']), 'M25 live domain equality')
    need(row['continuity']['ordered'] is True and [s['pc'] for s in row['stops']][:4]
         == ['0012f850', '001585c8', '001585e0', '0012f8d4'], 'M25 live stop order')
    walk = row['walk']
    need(walk['contiguous'] is True and walk['pc_panel_hashes'] == 1 and walk['rows'] == len(walk['table']) >= 80
         and all(walk['matches_full_captures'].values()), 'M25 live walk window')
    need(len(row['unobserved_stops']) == 2 and row['remaining_unobserved'], 'M25 live unobserved stops not declared')
    return {'stops': len(row['stops']), 'walk_ticks': walk['rows']}


def measurements(check, row, read, need, same):
    if check['result'] == 'm25-world-session':
        return _session(check, row, read, need, same)
    if check['result'] == 'm25-live':
        return _live(row, read, need, same)
    return _authentic(row, read, need, same)
