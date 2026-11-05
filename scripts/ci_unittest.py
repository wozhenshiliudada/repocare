"""Run a deterministic part of ordinary unittest discovery without dropping cases."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import unittest


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def partition(cases, shards):
    if isinstance(shards, bool) or not isinstance(shards, int) or shards < 1:
        raise ValueError('shards must be a positive integer')
    cases = list(cases)
    parts = [[] for _ in range(shards)]
    for case in cases:
        # Keep module/class fixtures together. Repeated cases from ordinary
        # discovery, including imported TestCases, must remain repeated.
        module = type(case).__module__
        index = int.from_bytes(hashlib.sha256(module.encode()).digest()[:8], 'big') % shards
        parts[index].append(case)
    if Counter(id(case) for part in parts for case in part) != Counter(map(id, cases)):
        raise RuntimeError('partition lost or duplicated a discovered test occurrence')
    return parts


def fingerprint(ids):
    return hashlib.sha256(json.dumps(ids, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.durations = []

    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        self.durations.append({'id': test.id(), 'seconds': round(time.monotonic() - self.started, 3)})
        super().stopTest(test)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-dir', default='tests')
    parser.add_argument('--shard', type=int, required=True, help='One-based shard number')
    parser.add_argument('--shards', type=int, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args(argv)
    if not 1 <= args.shard <= args.shards:
        parser.error('require 1 <= shard <= shards')

    cases = list(flatten(unittest.TestLoader().discover(args.start_dir)))
    selected = partition(cases, args.shards)[args.shard - 1]
    if not cases or not selected:
        parser.error('discovery and the selected shard must both contain tests')
    ids = [case.id() for case in cases]
    chosen = [case.id() for case in selected]
    report = {
        'schema': 1, 'source_sha': os.environ.get('GITHUB_SHA'),
        'shard': args.shard, 'shards': args.shards,
        'discovered_count': len(ids), 'discovered_ids': ids, 'discovery_sha256': fingerprint(ids),
        'selected_count': len(chosen), 'selected_ids': chosen,
        'selection_sha256': fingerprint(chosen),
        'completed': False, 'status': 'planned' if args.plan_only else 'running',
        'started_at': datetime.now(timezone.utc).isoformat(),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    save()
    print(f'Python shard {args.shard}/{args.shards}: {len(chosen)} of {len(ids)} discovered cases', flush=True)
    if args.plan_only:
        return 0
    try:
        result = unittest.TextTestRunner(verbosity=2, resultclass=TimedResult).run(unittest.TestSuite(selected))
    except BaseException:
        report['status'] = 'interrupted'
        save()
        raise
    report.update(
        completed=result.testsRun == len(selected), tests_run=result.testsRun,
        failures=[case.id() for case, _ in result.failures],
        errors=[case.id() for case, _ in result.errors],
        skipped=[{'id': case.id(), 'reason': reason} for case, reason in result.skipped],
        expected_failures=[case.id() for case, _ in result.expectedFailures],
        unexpected_successes=[case.id() for case in result.unexpectedSuccesses],
        durations=result.durations, finished_at=datetime.now(timezone.utc).isoformat(),
    )
    success = result.wasSuccessful() and report['completed']
    report['status'] = 'passed' if success else 'failed'
    save()
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
