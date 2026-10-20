"""Standalone arithmetic checks; run python3 tests/test_fund_calculations.py.
Database permissions, posting actions and payroll integration require Odoo 19.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

source = ast.parse((Path(__file__).parents[1] / 'models/event_fund.py').read_text())
methods = []
for node in source.body:
    if isinstance(node, ast.ClassDef):
        for method in node.body:
            if isinstance(method, ast.FunctionDef) and method.name in ('_compute_totals', '_compute_signed', '_validate_event_contribution'):
                method.decorator_list = []
                methods.append(method)
ns = {'UserError': ValueError, '_': lambda text: text}
exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])), '<fund production methods>', 'exec'), ns)


class Records(list):
    def filtered(self, predicate):
        return Records(row for row in self if predicate(row))

    def mapped(self, name):
        return [getattr(row, name) for row in self]


def totals(entries, purchases=()):
    fund = NS(entry_ids=Records(entries), purchase_ids=Records(purchases))
    ns['_compute_totals']([fund])
    return fund


def entry(amount, state='posted', purchase=None):
    return NS(signed_amount=amount, state=state, purchase_id=purchase)


class FundTests(unittest.TestCase):
    def test_only_posted_contributions_count(self):
        fund = totals([entry(500), entry(999, state='draft')])
        self.assertEqual(fund.available, 500)

    def test_expense_and_payroll_reduce_balance(self):
        fund = totals([entry(500), entry(-100), entry(-150)])
        self.assertEqual((fund.income, fund.spending, fund.balance), (500, 250, 250))

    def test_confirmed_po_reserves_funds_draft_does_not(self):
        fund = totals([entry(500)], [NS(state='purchase', amount_total=300), NS(state='draft', amount_total=999)])
        self.assertEqual((fund.balance, fund.committed, fund.available), (500, 300, 200))

    def test_partial_po_settlement_does_not_double_count(self):
        po = NS(state='purchase', amount_total=300)
        fund = totals([entry(500), entry(-100, purchase=po)], [po])
        self.assertEqual((fund.balance, fund.committed, fund.available), (400, 200, 200))
        fund = totals([entry(500), entry(-300, purchase=po)], [po])
        self.assertEqual((fund.balance, fund.committed, fund.available), (200, 0, 200))

    def test_cancelled_po_releases_only_unpaid_commitment(self):
        po = NS(state='cancel', amount_total=300)
        fund = totals([entry(500), entry(-100, purchase=po)], [po])
        self.assertEqual((fund.balance, fund.committed, fund.available), (400, 0, 400))

    def test_contribution_requires_full_cash_receipts(self):
        event = NS(id=1, x_is_event=True, x_coordinator_paid_from_event=False,
                   x_coordinator_fee_paid=False, x_attendance_ids=Records(),
                   x_coordinator_to_supplies=200, x_price_locked=True,
                   x_agreed_grand=1000, x_event_grand_total=1300,
                   x_balance_remaining=0, x_total_paid=1000)
        record = NS(event_id=event, amount=200,
                    currency_id=NS(compare_amounts=lambda a, b: (a > b) - (a < b)))
        self.assertEqual(ns['_validate_event_contribution'](record), 1)
        event.x_total_paid = 500
        # A zero balance due to write-off does not qualify as fully paid.
        with self.assertRaises(ValueError):
            ns['_validate_event_contribution'](record)
        event.x_total_paid = 1000
        event.x_coordinator_fee_paid = True
        with self.assertRaises(ValueError):
            ns['_validate_event_contribution'](record)
        event.x_coordinator_fee_paid = False
        event.x_balance_remaining = 100
        with self.assertRaises(ValueError):
            ns['_validate_event_contribution'](record)

    def test_signed_entry_amounts(self):
        rows = [NS(kind=kind, amount=100) for kind in ('contribution', 'opening', 'expense', 'payroll')]
        ns['_compute_signed'](rows)
        self.assertEqual([row.signed_amount for row in rows], [100, 100, -100, -100])


if __name__ == '__main__':
    unittest.main()
