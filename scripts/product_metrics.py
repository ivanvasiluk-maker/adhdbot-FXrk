#!/usr/bin/env python3
"""Read-only aggregate report, excluding QA users. Never exports task/profile text."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from datetime import date
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.product_metrics import product_metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--through', required=True, type=date.fromisoformat,
                        help='Inclusive cutoff in the recorded local calendar dates')
    parser.add_argument('--assessments', type=Path, help='Explicit pilot judgements JSON: model_accuracy and memory_value lists')
    args = parser.parse_args()
    assessments = json.loads(args.assessments.read_text()) if args.assessments else {}

    path = Path(args.db).resolve()
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        users = [r[0] for r in db.execute('SELECT user_id FROM users WHERE COALESCE(is_test_user,0)=0')]
        rows = [dict(r) for r in db.execute('SELECT * FROM attempt_evidence')]
    print(json.dumps({'cutoff': args.through.isoformat(), 'cohort_size': len(users),
                      'metrics': product_metrics(users, rows, args.through.isoformat(),
                          model_assessments=assessments.get('model_accuracy', []),
                          memory_assessments=assessments.get('memory_value', [])),
                      'limitations': ['Behaviour change is reported continuation, not a causal treatment effect.',
                                      'Retention is return on distinct reporting dates, not D7 cohort retention.',
                                      'Model accuracy and memory value use only explicit matched pilot judgements, if supplied.']},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
