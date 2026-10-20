"""Standalone checks of production allocation methods; Odoo UI needs live QA."""
import ast
from decimal import Decimal, ROUND_HALF_UP
import math
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

source = ast.parse((Path(__file__).parents[1] / 'models/event_charity_disbursement.py').read_text())
methods = []
for node in source.body:
    if isinstance(node, ast.ClassDef):
        for method in node.body:
            if isinstance(method, ast.FunctionDef) and method.name in (
                    '_compute_charity_disbursement', '_check_charity_disbursement', '_validate_charity_certificate'):
                method.decorator_list = []
                methods.append(method)
ns = {'math': math, '_': lambda text: text, 'ValidationError': ValueError, 'UserError': ValueError}
exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
             '<production charity methods>', 'exec'), ns)


def event(**overrides):
    values = dict(x_pl_net_total=1000, x_coordinator_to_supplies=200,
                  x_charity_disbursement_enabled=True,
                  x_charity_beneficiary_id=1,
                  x_charity_disbursement_method='all',
                  x_charity_disbursement_percent=25,
                  x_charity_disbursement_fixed=150,
                  x_currency_id=NS(round=lambda v: float(Decimal(str(v)).quantize(
                      Decimal('.01'), rounding=ROUND_HALF_UP))))
    values.update(overrides)
    return NS(**values)


def compute(**overrides):
    row = event(**overrides)
    ns['_compute_charity_disbursement']([row])
    return row


class CharityTests(unittest.TestCase):
    def test_all_proceeds_preserve_coordinator_reserve(self):
        e = compute()
        self.assertEqual(e.x_charity_available_proceeds, 800)
        self.assertEqual(e.x_charity_disbursement_amount, 800)
        self.assertEqual(e.x_charity_retained_net, 200)

    def test_percentage_of_available_proceeds(self):
        e = compute(x_charity_disbursement_method='percent')
        self.assertEqual(e.x_charity_disbursement_amount, 200)
        self.assertEqual(e.x_charity_retained_net, 800)

    def test_fixed_amount_is_capped_at_remaining_proceeds(self):
        e = compute(x_pl_net_total=100, x_coordinator_to_supplies=0,
                    x_charity_disbursement_method='fixed', x_charity_disbursement_fixed=150)
        self.assertEqual(e.x_charity_disbursement_amount, 100)
        self.assertEqual(e.x_charity_disbursement_shortfall, 50)
        self.assertEqual(e.x_charity_retained_net, 0)

    def test_fixed_donation_cannot_spend_coordinator_reserve(self):
        e = compute(x_pl_net_total=300, x_coordinator_to_supplies=200,
                    x_charity_disbursement_method='fixed', x_charity_disbursement_fixed=250)
        self.assertEqual(e.x_charity_disbursement_amount, 100)
        self.assertEqual(e.x_charity_disbursement_shortfall, 150)
        self.assertEqual(e.x_charity_retained_net, 200)

    def test_fixed_donation_is_zero_when_costs_exceed_income(self):
        e = compute(x_pl_net_total=-100, x_charity_disbursement_method='fixed')
        self.assertEqual(e.x_charity_disbursement_amount, 0)
        self.assertEqual(e.x_charity_retained_net, -100)

    def test_certificate_requires_enabled_positive_allocation(self):
        class CertificateRecord:
            def ensure_one(self): pass
        row = CertificateRecord()
        row.env = NS(user=NS(has_group=lambda name: True))
        row.x_is_event = True
        row.x_charity_disbursement_enabled = True
        row.x_charity_beneficiary_id = 1
        row.x_charity_disbursement_amount = 100
        self.assertTrue(ns['_validate_charity_certificate'](row))
        row.x_charity_disbursement_amount = 0
        with self.assertRaises(ValueError):
            ns['_validate_charity_certificate'](row)
        row.x_charity_disbursement_amount = 100
        row.x_charity_disbursement_enabled = False
        with self.assertRaises(ValueError):
            ns['_validate_charity_certificate'](row)

    def test_disabled_allocation_keeps_profit(self):
        e = compute(x_charity_disbursement_enabled=False)
        self.assertEqual(e.x_charity_disbursement_amount, 0)
        self.assertEqual(e.x_charity_retained_net, 1000)

    def test_loss_has_no_percentage_or_all_proceeds(self):
        for method in ('all', 'percent'):
            e = compute(x_pl_net_total=-100, x_charity_disbursement_method=method)
            self.assertEqual(e.x_charity_disbursement_amount, 0)
            self.assertEqual(e.x_charity_retained_net, -100)

    def test_reserve_larger_than_profit(self):
        e = compute(x_pl_net_total=100)
        self.assertEqual(e.x_charity_available_proceeds, 0)
        self.assertEqual(e.x_charity_disbursement_amount, 0)

    def test_cent_rounding_does_not_create_shortfall_for_all_proceeds(self):
        e = compute(x_pl_net_total=100.005, x_coordinator_to_supplies=0)
        self.assertEqual(e.x_charity_disbursement_amount, 100.01)
        self.assertEqual(e.x_charity_disbursement_shortfall, 0)

    def test_beneficiary_required(self):
        with self.assertRaises(ValueError):
            ns['_check_charity_disbursement']([event(x_charity_beneficiary_id=False)])

    def test_percentage_range_and_fixed_amount_validation(self):
        for value in (-1, 101, float('nan')):
            with self.assertRaises(ValueError):
                ns['_check_charity_disbursement']([event(
                    x_charity_disbursement_method='percent', x_charity_disbursement_percent=value)])
        with self.assertRaises(ValueError):
            ns['_check_charity_disbursement']([event(
                x_charity_disbursement_method='fixed', x_charity_disbursement_fixed=-10)])
        for value in (0, 100):
            ns['_check_charity_disbursement']([event(
                x_charity_disbursement_method='percent', x_charity_disbursement_percent=value)])


if __name__ == '__main__':
    unittest.main()
