#!/usr/bin/env python3
"""M17 corridor negative controls for the lifecycle-connected frame.

Each control compiles src/resource_loader.c with one deliberate defect in
src/game_lifecycle.c and runs the first frames of the original-game obs3
corridor in --lifecycle mode. The defect must be rejected by a guarded
comparison against the native captures; the null control must pass.

    python3 -B tools/verify_lifecycle_corridor_controls.py --elf "$HG_ELF" \
        --ps2dinfo "$HG_PS2DINFO" --capture-dir "$HG_CAPTURE_DIR" \
        --area-image "$HG_AREA_IMAGE" --output local/m17/out/corridor-controls.json
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
import verify_player_continuous as VC
from resource_harness import library
from verify_player_grounded_runtime import Captures
from verify_resource_loader import shared_module
from hg_evidence_safety import digest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src/game_lifecycle.c'
CONTROLS = [
    ('null-control', None, None),
    ('missing-gameplay-frame-tick', 'w32(e,G(0x2f80),r32(e,G(0x2f80))+1u);', ''),
    ('missing-main-loop-counter', 'w32(e,G(0x2f84),r32(e,G(0x2f84))+1u);', ''),
    ('accumulator-step-two', 'hg_ee_fp(0,r32(e,G(0x2f7c)),0x3f800000u,&fault)',
     'hg_ee_fp(0,r32(e,G(0x2f7c)),0x40000000u,&fault)'),
    ('vblank-counter-skipped', 'w32(e,0x95e300,r32(e,0x95e300)+1u);', ''),
    ('mode-pointer-not-refreshed', 'w32(e,MODE_PTR,MODE_BLOCK);', 'w32(e,MODE_PTR,MODE_BLOCK+4);'),
    ('double-vblank-delivery', '''        hg_vblank_start_handler(e);
        hg_vblank_end_handler(e);''', '''        hg_vblank_start_handler(e);
        hg_vblank_start_handler(e);
        hg_vblank_end_handler(e);'''),
]


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--capture-dir', type=Path, required=True)
    p.add_argument('--area-image', type=Path, required=True)
    p.add_argument('--frames', type=int, default=2)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise FileExistsError(a.output)
    obs = VC.observation()
    obs['frames'] = sorted(obs['frames'], key=lambda fr: fr['counter'])[:a.frames]
    caps = Captures(a.capture_dir)
    caps.elfdata = a.elf.read_bytes()
    caps.segments, _ = shared_module(a.ps2dinfo).segments_from_elf(caps.elfdata)
    from verify_area_lifecycle import load_area1, AREA_BASE
    caps.area = (load_area1(a.area_image), AREA_BASE)
    ownership = VC.inter_frame_ownership(caps, obs)
    source = SOURCE.read_text()
    folder = ROOT / 'local/m17/corridor-controls'
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, old, new in CONTROLS:
        text = source if old is None else source.replace(old, new)
        if old is not None and source.count(old) != 1:
            raise SystemExit(f'{name}: anchor not unique')
        path = folder / f'{name}.c'
        path.write_text(text)
        digest_ = hashlib.sha256(text.encode()).hexdigest()
        out = folder / f'{name}-{digest_[:12]}.so'
        lib = library(output=out, extra=(path,))
        try:
            result = VC.sequence(lib, caps, obs, ownership, None, lifecycle=True)
            failures = result['failures']
            outcome = 'clean' if result['result'] == 'passed' else 'rejected'
        except AssertionError as exc:
            failures, outcome = [dict(stage='driver', error=str(exc)[:300])], 'rejected'
        expected = 'clean' if old is None else 'rejected'
        rows.append(dict(control=name, source_sha256=digest_, outcome=outcome, expected=expected,
                         effective=outcome == expected, failures=failures[:6]))
        print(f'{name:<32} {outcome:<9} {"EFFECTIVE" if outcome == expected else "INEFFECTIVE"} {failures[:2]}',
              flush=True)
    summary = dict(schema='hg-m17-corridor-controls/v1', frames=a.frames, elf_sha256=digest(a.elf),
                   baseline_sha256=hashlib.sha256(source.encode()).hexdigest(), controls=rows,
                   passed=all(r['effective'] for r in rows))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.open('x').write(json.dumps(summary, indent=2) + '\n')
    print('PASSED' if summary['passed'] else 'FAILED')
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
