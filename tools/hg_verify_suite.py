"""Run unittest discovery and record which tests actually produced outcomes."""
import argparse
import json
from pathlib import Path
import sys
import unittest


def test_ids(suite):
    if isinstance(suite, unittest.TestSuite):
        return [name for child in suite for name in test_ids(child)]
    return [suite.id()]


class Outcomes(unittest.TextTestResult):
    def __init__(self, *args):
        super().__init__(*args)
        self.succeeded = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.succeeded.append(test.id())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pattern', default='test*.py')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover('tests', pattern=args.pattern)
    discovered = test_ids(suite)
    result = unittest.TextTestRunner(verbosity=2, resultclass=Outcomes).run(suite)
    row = dict(schema='hg-unittest-discovery/v1', pattern=args.pattern, discovered=discovered,
               run=result.testsRun, passed=result.succeeded,
               skipped=[test.id() for test, _ in result.skipped],
               failures=[test.id() for test, _ in result.failures],
               errors=[test.id() for test, _ in result.errors],
               expected_failures=[test.id() for test, _ in result.expectedFailures],
               unexpected_successes=[test.id() for test in result.unexpectedSuccesses])
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(row, stream, indent=2)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
