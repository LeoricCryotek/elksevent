"""Run without an Odoo server: python3 tests/test_pl_calculations.py.

Execute the production calculation methods with small recordset doubles. These
checks cover financial arithmetic; database/view installation requires Odoo 19.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

source = ast.parse((Path(__file__).parents[1] / 'models/project_task.py').read_text())
names = {'_pl_rental_income', '_pl_coordinator_payout', '_compute_financials',
         '_compute_event_sales', '_bookkeeper_pl_rows', '_get_pl_report_data',
         '_compute_pl_summary', 'action_pull_clover_sales'}
methods = []
for cls in source.body:
    if isinstance(cls, ast.ClassDef):
        for node in cls.body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                node.decorator_list = []
                methods.append(node)
namespace = {'_': lambda message, **kw: message,
             'fields': NS(Datetime=NS(now=lambda: 'now'))}
exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
             '<production P&L methods>', 'exec'), namespace)


class Records(list):
    @property
    def ids(self):
        return list(range(len(self)))

    def mapped(self, name):
        return [getattr(r, name) for r in self]

    def filtered(self, predicate):
        if isinstance(predicate, str):
            return Records(r for r in self if getattr(r, predicate))
        return Records(r for r in self if predicate(r))

    def __sub__(self, other):
        return Records(r for r in self if r not in other)


class Event:
    def __init__(self, **values):
        self.__dict__.update(values)

    def __getattr__(self, name):
        return 0.0

    def __iter__(self):
        return iter([self])

    def __len__(self):
        return 1

    def ensure_one(self):
        pass

    def _event_settings(self):
        return False

    def _event_room_discount(self):
        return 1000.0, 0.0

    def _amount_paid(self):
        return 0.0

    def _pl_labor_paid_by_dept(self):
        return [{'dept': 'Bar', 'code': 'bar', 'wages': 80,
                 'tips': 20, 'total': 100}]

    def _event_discount_label(self):
        return 'Discount'

    @staticmethod
    def _elks_is_volunteer_att(att):
        return att.volunteer


for name in names:
    setattr(Event, name, namespace[name])


def event(**overrides):
    vals = dict(
        x_price_locked=True, x_agreed_billed=1000, x_billed_snapshot=0,
        x_total_billed=1100, x_invoice_ids=Records(), x_coordinator_fee=200,
        x_coordinator_paid_from_event=True, x_coordinator_fee_paid=True,
        x_attendance_ids=Records([NS(x_coordinator_fee_share=200,
                                    worked_hours=4, volunteer=False),
                                 NS(x_coordinator_fee_share=0,
                                    worked_hours=6, volunteer=True)]),
        x_room_booking_ids=Records([NS(subtotal=1000, rate=1000,
            cleaning_fee=0, service_fee=0, room_id=NS(name='Hall',
            x_room_rate=1000, x_cleaning_fee=0, x_service_fee=0))]),
        x_cost_line_ids=Records([NS(total=100, x_taxable=False, name='Supplies',
            x_auto_source='', x_line_cogs=60, cost_type_id=NS(category='other'))]),
        x_purchase_order_ids=Records([NS(state='purchase', amount_total=999)]),
        x_sales_clover=500, x_sales_drink=0, x_sales_food=0,
        x_sales_entry=0, x_sales_other=0, x_guest_count=100,
        x_event_sales_income=500,
    )
    vals.update(overrides)
    return Event(**vals)


class ProfitTests(unittest.TestCase):
    def test_locked_zero_is_not_replaced_by_live_price(self):
        self.assertEqual(event(x_agreed_billed=0)._pl_rental_income(), 0)

    def test_snapshot_and_live_fallback(self):
        self.assertEqual(event(x_price_locked=False, x_billed_snapshot=900)._pl_rental_income(), 900)
        self.assertEqual(event(x_price_locked=False)._pl_rental_income(), 1100)

    def test_zero_final_snapshot(self):
        e = event(x_price_locked=False, x_invoice_ids=Records([
            NS(x_event_invoice_kind='final', state='posted')]))
        self.assertEqual(e._pl_rental_income(), 0)

    def test_coordinator_posted_and_pending_and_pooled(self):
        self.assertEqual(event()._pl_coordinator_payout(), 200)
        e = event(x_coordinator_fee_paid=False, x_attendance_ids=Records())
        self.assertEqual(e._pl_coordinator_payout(), 200)
        e.x_coordinator_paid_from_event = False
        self.assertEqual(e._pl_coordinator_payout(), 0)

    def test_pnl_excludes_purchase_orders_and_includes_coordinator(self):
        e = event()
        e._compute_financials()
        self.assertEqual(e.x_total_costs, 360)  # 60 goods + 100 labor/tips + 200 fee
        self.assertEqual(e.x_net_income, 640)  # locked 1000, not live 1300
        e._compute_event_sales()
        e._compute_pl_summary()
        self.assertEqual(e.x_pl_net_total, 1140)
        self.assertEqual(e.x_event_volunteer_hours, 6)
        self.assertEqual(e.x_profit_margin_pct, 76)
        report = e._get_pl_report_data()
        self.assertEqual(report['grand_profit'], e.x_pl_net_total)
        self.assertEqual(report['grand_costs'], 360)
        self.assertEqual(report['events'][0]['po_lines'], [])
        self.assertEqual(sum(row['cogs'] for row in report['events'][0]['cost_items']), 360)
        self.assertEqual(sum(row['total'] for row in report['events'][0]['sales_lines']), 500)

    def test_bookkeeper_rows_reconcile_to_same_profit(self):
        e = event()
        e._compute_financials()
        e._compute_event_sales()
        rows = e._bookkeeper_pl_rows()
        self.assertEqual(sum(r['revenue'] for r in rows), 1500)
        self.assertEqual(sum(r['cogs'] for r in rows), 360)
        self.assertEqual(sum(r['net'] for r in rows), e.x_pl_net_total)

    def test_clover_mixed_order_excludes_venue_line_only(self):
        sales = Records([NS(net_amount=700, total_amount=750)])
        lines = Records([
            NS(product_id=NS(name=''), description='Event Services Payments',
               margin_amount=200, amount=200),
            NS(product_id=NS(name='Beer'), description='Beer',
               clover_item_id='beer', unit_qty=10, amount=550, margin_amount=500)])
        class Model:
            def __init__(self, records=()):
                self.records = records
                self.created = []
            def sudo(self): return self
            def search(self, domain): return self.records
            def create(self, values): self.created.append(values)
        items = Model()
        e = event(env={'clover.sale': Model(sales), 'clover.sale.line': Model(lines),
                       'product.product': Records(), 'elks.event.clover.item': items},
                  x_clover_top_item_ids=NS(unlink=lambda: None), id=1)
        e._clover_window = lambda: (1, 2)
        e.message_post = lambda **kw: None
        e.action_pull_clover_sales()
        self.assertEqual(e.x_sales_clover, 500)
        self.assertEqual(e.x_clover_sales_gross, 550)
        self.assertEqual([r['name'] for r in items.created], ['Beer'])
        # Re-pulling replaces the scalar total instead of adding another batch.
        e.action_pull_clover_sales()
        self.assertEqual(e.x_sales_clover, 500)


if __name__ == '__main__':
    unittest.main()
