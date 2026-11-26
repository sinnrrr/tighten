#!/bin/sh
set -e
dir=${1:?}
rm -rf "$dir"
mkdir -p "$dir/ledger" "$dir/data"
cd "$dir"
git init -q
git config user.name "Demo Dev"
git config user.email "dev@example.com"
cat >pyproject.toml <<'EOF'
[project]
name = "ledger"
version = "0.2.0"
description = "Small CLI for tracking personal expenses"
requires-python = ">=3.11"
EOF
touch ledger/__init__.py
cat >data/expenses.csv <<'EOF'
date,category,amount,note
2025-03-02,groceries,54.20,weekly shop
2025-03-04,transport,12.50,monthly bus pass top up
2025-03-09,eating out,31.00,dinner with Sam
2025-03-11,groceries,N/A,farmers market
2025-03-15,rent,950.00,march rent
2025-03-18,transport,8.40,taxi
2025-03-21,subscriptions,11.99,music
2025-03-26,eating out,18.75,lunch
EOF
cat >ledger/store.py <<'EOF'
import csv


def load_expenses(path):
    expenses = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            expenses.append(
                {
                    "date": row["date"],
                    "category": row["category"],
                    "amount": float(row["amount"]),
                    "note": row["note"],
                }
            )
    return expenses
EOF
cat >ledger/report.py <<'EOF'
from collections import defaultdict


def totals_by_category(expenses):
    totals = defaultdict(float)
    for e in expenses:
        totals[e["category"]] += e["amount"]
    return dict(totals)


def format_report(totals):
    lines = []
    for category, total in sorted(totals.items()):
        lines.append(f"{category:<15}{total:>10.2f}")
    return "\n".join(lines)
EOF
cat >ledger/cli.py <<'EOF'
import sys

from ledger.report import format_report, totals_by_category
from ledger.store import load_expenses


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/expenses.csv"
    expenses = load_expenses(path)
    print(format_report(totals_by_category(expenses)))


if __name__ == "__main__":
    main()
EOF
printf '__pycache__/\n.venv/\n.claude/\n' >.gitignore
mkdir .claude
cat >.claude/settings.local.json <<'EOF'
{
  "statusLine": {"type": "command", "command": "true"},
  "claudeMdExcludes": ["**/.claude/CLAUDE.md", "**/AGENTS.md"],
  "permissions": {"allow": ["Bash(python:*)", "Bash(python3:*)", "Bash(uv:*)", "Bash(ruff:*)", "Bash(cd:*)"]}
}
EOF
git add -A
git commit -q --no-verify -m "Add expense loader and category report"
