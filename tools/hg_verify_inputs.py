"""Resolve local M12 prerequisites and bind saved evidence to its manifests."""
from pathlib import Path
import os

from hg_verify_results import read_json


DEFAULTS = {
    'elf': 'local/extracted/japan/SCPS_150.46',
    'image': '../Hungry Ghosts (Japan).iso',
    'emulator': 'local/references/pcsx2.AppImage',
    'ps2dinfo': 'local/references/PS2DInfo',
    'captures': 'local/m12/captures',
    'm13-captures': 'local/m13/captures',
    'ordinary-source': 'local/m9/m8-a1-zero.c',
}
ENV_PATHS = {'elf': 'HG_ELF', 'image': 'HG_JAPAN_ISO', 'emulator': 'HG_PCSX2', 'ps2dinfo': 'HG_PS2DINFO',
             'm13-captures': 'HG_CAPTURE_DIR'}


def configured_paths(root, path):
    configured = read_json(path) if path else {}
    if not isinstance(configured, dict) or configured.keys() - DEFAULTS.keys():
        raise ValueError(f'Config must be a JSON object with keys from {", ".join(DEFAULTS)}')
    options = DEFAULTS | {key: os.environ[var] for key, var in ENV_PATHS.items() if os.environ.get(var)} | configured
    if any(not isinstance(value, str) or not value.strip() for value in options.values()):
        raise ValueError('Configured paths must be nonempty strings')
    return {key: str(root / Path(value).expanduser()) for key, value in options.items()}


def related_file(directory, name):
    path = directory / name
    if Path(name).is_absolute() or '..' in Path(name).parts or not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f'Unsafe observation path {name}')
    return path


def debugger_files(observation, emulator, profile, contract_profile=None):
    row = read_json(observation)
    directory = observation.parent
    paths = {observation: None, emulator: row['emulator_sha256'], profile: row['profile_sha256']}
    if contract_profile:
        paths[contract_profile] = row['contract_profile_sha256']
    events = row['observations'] + row.get('supplemental', [])
    if 'first_arguments' in row:
        events.append(row['first_arguments'])
    for event in events:
        for key in ('capture', 'screenshot'):
            if key in event:
                paths[related_file(directory, event[key])] = event[key + '_sha256']
    return paths


def frame_files(observation, captures, group):
    paths = {observation: None}
    frames = read_json(observation)['frames']
    for frame in frames:
        if group == 'm11-differential':
            from verify_player_constraint_differential import BASE_STAGE
            if frame['counter'] != BASE_STAGE[1]:
                continue
            rows = [frame['captures'][BASE_STAGE[0]]]
        elif group == 'm12-differential':
            rows = [frame['captures']['grounded-entry']]
        else:
            rows = list(frame['captures'].values()) + frame.get('pushes', [])
        for row in rows:
            paths[related_file(captures, row['name'] + '.json')] = row['sha256']
            if not group.endswith(('-differential', '-frames')):
                for shot in row.get('screenshots', []):
                    paths[related_file(captures.parent / 'stops', shot['name'])] = shot['sha256']
    if len(paths) == 1:
        raise ValueError('Observation contains no required captures')
    return paths


def saved_inputs(root, group, paths):
    if not group:
        return {}
    evidence = root / 'evidence'
    local = root / 'local'
    if group == 'm4':
        runtime = local / 'm4/runtime'
        return debugger_files(runtime / 'observation.json', paths['emulator'], runtime / 'capture-profile.ini')
    if group == 'm5-m6':
        return debugger_files(local / 'm5/runtime/observation-resumed.json', paths['emulator'],
                              local / 'm5/resume/capture-profile.ini') | debugger_files(
            local / 'm6/captures2/observation.json', paths['emulator'], local / 'm6/capture2-profile.ini')
    if group == 'm7':
        return debugger_files(local / 'm7/observation.json', paths['emulator'], local / 'm7/capture-profile.ini',
                              local / 'm7/contract-profile.ini')
    if group == 'm8-setup':
        record = evidence / 'm8/setup-runtime-comparison.json'
        captures = read_json(record)['captures']
        return {record: None, **{local / f'm8/captures/setup-{stage}.json': captures[f'setup_{stage}_sha256']
                                for stage in ('entry', 'return')}}
    if group in ('m8-input', 'm8-connected'):
        record = evidence / 'm8' / ('native-input-observation.json' if group == 'm8-input'
                                     else 'connected-gameplay-observation.json')
        rows = read_json(record)['stages' if group == 'm8-input' else 'observations']
        capture_dir = local / 'm8/captures'
        pinned = {capture_dir / Path(row['capture']).name: row['capture_sha256'] for row in rows}
        if group == 'm8-input':
            needed = ('native-ordinary-branch-001', 'native-neutral-consumed-001',
                      'native-square-branch-001', 'native-square-helper-entry-001')
            pinned = {capture_dir / (name + '.json'): pinned[capture_dir / (name + '.json')] for name in needed}
        # The connected verifier also opens corroborating captures from this folder.
        # Include legacy files absent from the observation as content-bound inputs.
        return {record: None, **pinned}
    if group == 'm9':
        record = evidence / 'm9/player-turn-runtime-comparison.json'
        return {record: None, **{local / 'm9/captures' / (name + '.json'): digest
                                for name, digest in read_json(record)['captures'].items()}}
    if group == 'm10':
        return frame_files(evidence / 'm10/player-walk-observation.json', local / 'm10/captures', group)
    if group.startswith('m11'):
        return frame_files(evidence / 'm11/constraint-observation.json', local / 'm11/captures', group)
    if group.startswith('m12'):
        return frame_files(evidence / 'm12/grounded-observation.json', paths['captures'], group)
    if group == 'm13':
        return frame_files(evidence / 'm13/continuous-observation.json', paths['m13-captures'], group)
    if group == 'm17-native':
        return {}
    if group in ('m18-native', 'm18-differential'):
        record = evidence / 'm18/checks.json'
        pins = read_json(record)['identities']
        result = {record: None, local / 'm18/ovl/A0_B09.BIN': pins['title_overlay_sha256']} if group == 'm18-differential' else {
            record: None, local / 'm18/native/frames.jsonl': pins['native_stop_records_sha256']}
        return result
    if group == 'm17-differential':
        return {evidence / 'm17/checks.json': None}
    raise ValueError(f'Unknown saved input group {group}')
