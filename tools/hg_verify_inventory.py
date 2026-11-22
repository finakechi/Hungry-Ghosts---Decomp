"""Bind the executable M12 inventory to the immutable historical command list."""
from pathlib import Path
import re

from hg_verify_results import read_json


TOKENS = {
    '$HG_ELF': '{elf}', '$HG_JAPAN_ISO': '{image}', '$HG_PS2DINFO': '{ps2dinfo}',
    '$HG_PCSX2': '{emulator}',
}


def adapt(command, historic_directory):
    for argument in command:
        if argument.startswith(historic_directory + '/'):
            relative = argument.removeprefix(historic_directory + '/')
            yield '{artifact:' + relative + '}'
        elif argument.startswith('$HG_PS2DINFO/tools/'):
            yield '{shared:' + Path(argument).name + '}'
        else:
            yield TOKENS.get(argument, argument)


def inventory(path, historical_path, profile):
    config, history = read_json(path), read_json(historical_path)
    mapping = config['checks']
    historical = history['checks']
    numbers = [f'{index:02d}' for index in range(79)]
    if (config['schema'] != 'hg-verification-inventory/v2' or history['schema'] != 'hg-m12-checks/v2'
            or [row['historical'] for row in mapping] != numbers
            or [row['log'] for row in historical] != [n + '.log' for n in numbers]):
        raise ValueError('Inventory must map historical checks 00–78 in order')
    checks = {}
    directory = 'local/m12/final-checks-' + history['regression_inventory']['run']
    for check, recorded in zip(mapping, historical):
        number = check['historical']
        argv = list(adapt(recorded['command'], directory))
        if number in ('22', '23'):
            argv = ['python', '-B', 'tools/hg_m8_controls.py', 'setup' if number == '22' else 'lists',
                    '--output', '{artifact:result.json}']
            if number == '23':
                argv += ['--elf', '{elf}', '--ps2dinfo', '{ps2dinfo}']
        elif number == '50':
            argv = ['python', '-B', 'tools/hg_publication_audit.py', '--worktree',
                    '--output', '{artifact:result.json}']
        elif number == '78':
            argv.insert(argv.index('--output'), '--m8-a1-zero-control')
            argv[argv.index('--source') + 1] = '{ordinary-source}'
        elif number == '25':
            argv = ['{dependency:sanitizer-build/sanitizer}']

        check = {**check, 'argv': argv, 'historical_command': recorded['command'],
                 'expected': 'expected-rejection' if check['evidence'] == 'differential-control'
                 or number in ('20', '22') else 'passed'}
        checks[check['id']] = describe(check)
    total = len(checks)
    for check in config['supplemental']:
        checks[check['id']] = describe({'expected': 'passed', **check})
    if (total != len(numbers) or len(checks) != total + len(config['supplemental'])
            or any(not re.fullmatch('[a-z0-9][a-z0-9-]*', name) for name in checks)
            or any(name not in checks for name in config['focused'])
            or len(set(config['focused'])) != len(config['focused'])):
        raise ValueError('Invalid check identifiers or focused selection')

    def ordered(names):
        result = []
        ready = set()
        while names:
            for name in names:
                row = checks[name]
                if set(row.get('requires', []) + row.get('after', [])) <= ready:
                    result.append(row)
                    ready.add(name)
                    names = [item for item in names if item != name]
                    break
            else:
                raise ValueError('Inventory has a missing or cyclic dependency')
        return result

    # Validate the entire graph even for focused runs. After edges prioritize
    # cheap diagnostic gates without suppressing independent verification.
    focused = config['focused']
    primary = [row['id'] for row in mapping]
    all_checks = ordered([name for name in focused if name in primary] + [name for name in primary if name not in focused])
    return all_checks if profile == 'full' else ordered(focused)


def describe(check):
    argv = check['argv']
    inputs = [item for item in argv if item.startswith(('local/', 'analysis/', 'evidence/'))
              or item.startswith(('{shared:', '{dependency:', '{elf}', '{image}', '{emulator}', '{ordinary-source}'))]
    if '{ps2dinfo}' in argv:
        inputs.append('{shared:verify_elf_evidence.py}')
    if check['result'] == 'local-inputs':
        inputs.extend(['{elf}', '{image}'])
    if check.get('input_group') in ('m12', 'm12-differential'):
        inputs = ['{captures}' if item == 'local/m12/captures' else item for item in inputs]
        argv = ['{captures}' if item == 'local/m12/captures' else item for item in argv]
    outputs = ['output.log']
    if '--output' in argv:
        outputs.append(argv[argv.index('--output') + 1].removeprefix('{artifact:').removesuffix('}'))
    if check['result'] == 'sanitizer-build':
        outputs.append('sanitizer')
    side_outputs = []
    if 'cc' in check.get('tools', []) and '--output' in argv:
        side_outputs += ['build/**', '*.so']
    if check.get('controls'):
        side_outputs.append('*-controls/**')
    if check['id'] in ('walk-camera-saved-runtime', 'constraint-saved-runtime', 'grounded-saved-runtime'):
        side_outputs.append('*.baseline.json')
    if check['result'] == 'compileall':
        side_outputs.append('pycache/**')
    return {**check, 'argv': argv, 'name': check.get('name', check['id'].replace('-', ' ').capitalize()),
            'inputs': list(dict.fromkeys(inputs)), 'outputs': outputs, 'side_outputs': side_outputs}
