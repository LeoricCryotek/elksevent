# Elks Event Bookings (`elksevent`)

Odoo 19 module that runs the lodge's facility event rentals on top of Project —
from the public request form through board approval, quoting, department
staffing, billing, and the P&L. All events live in one global **Events**
project; each event is a `project.task` with `x_is_event = True`.

## Pipeline

New → Contacted → Submitted to Board → Booked → After Action → Completed /
Cancelled. Board approval (Event Officer group) books the event, fires the
checklist kickoff, publishes the calendar entry, and emails the customer.

## The event form (tabs)

- **Event Details** — dates/times, guests, host + (for weddings) bride/groom/
  coordinator contacts, rooms, access/cleanup, Elks Event / Member Rental /
  Non-Profit flags, "Event Description" (working note) and "Calendar Event
  Details" (what feeds the lodge calendar).
- **Checklist** — equipment + per-department **requests**: tick Bar / Food /
  Cleaning and note what the customer asked for (number of bars, beer tub,
  drink/menu requests, other notes). Managers decide the actual staffing on
  their call sheet. Also the department call-out send/email buttons (green once
  certified) and the insurance section.
- **Rooms** — room bookings (rate/cleaning/service fees per room).
- **Event Costs** — customer charges that roll into the single "Event Rental"
  price, each with a COGS column feeding the P&L by category.
- **Financials** — Billing summary, UBI/property-tax reserve, Cost Summary
  (P&L), COGS by category, deposit, quote/invoice, and the Bookkeeper Summary
  print button.
- **Timesheets** — planned staff assignments (Role = HR/volunteer position),
  event timeclock hours, and the Volunteers block (see `elksvolunteer`).

## Department call-outs

Managers set in Lodge Settings receive a portal call-sheet (`/my/callout/...`),
assign staff by name (employee or free-text) with start/end → hours, rate →
cost, plus department gratuity, and **certify**. On certify the roster labor
posts to the event's COGS (a "… Staff (certified roster)" line), gratuity posts
as a pass-through, and the coordinator is emailed. Managers can reopen a
certified call-out to edit. A daily cron and a "Remind Pending Managers" button
chase un-certified call-outs.

## Billing

- **Build Quote** builds the single Event Rental sale order.
- **Deposit Invoice** / **Final Invoice** — click to *view* the invoice
  (creates it the first time); the final credits the paid deposit.
- **Refresh Invoices** — rebuilds cost lines + quote so the P&L and bookkeeper
  breakdown update, re-lays any DRAFT invoice, and leaves confirmed invoices
  untouched.

## Lodge Settings (Configuration)

Labor rates & service fees, coordinator-fee rule, **Event Rental Insurance
Default ($187)**, **Linen tiers** (≤30 = $300, 31–60 = $600, 61+ = $900),
department managers, customer survey, terms/agreement options, calendar user,
and **Notify on New Website Request** (users emailed + chat-pinged on each
submission).

## Auto-charges

- **Insurance** — added to costs when a non-lodge event is insured by the lodge
  (Insurance Provided By = Lodge), at the settings default.
- **Linens** — added whenever "Linen" is ticked (even in-house), priced by the
  guest-count tier.

Both refresh with guest count / settings on Build Quote / Refresh Invoices.

## Reports

Per-event & date-range P&L, Assessor/UBI, AP GL breakout, Customer Contract,
Department Call-Out sheets, Insurance Worksheet, Board Agenda, and the
Bookkeeper Summary (revenue vs cost vs net by P&L category).

## Deploy

Python changes need a service restart before the upgrade:

```
sudo systemctl restart odona-lewistonelks896.com
# then upgrade only this module (never -u all):
-u elksevent
```

## Recent changes

- 19.0.9.42 — Linen tier auto-charge; removed the Financials labor-estimate
  section and Contract/Agreement notes; manifest/README refresh.
- 19.0.9.41 — Event Insurance default $187 (+ migration); Refresh Invoices
  rename & P&L-only for confirmed invoices; checklist service areas simplified
  to requests-only; fixed a gratuity double-count.
- 19.0.9.40 — Deposit/Final invoice buttons view-or-create.
- 19.0.9.39 — Notify-on-new-website-request users (email + chat).
- 19.0.9.38 — Wedding contacts; Event Description vs Calendar Event Details.
- 19.0.9.37 — Non-Profit 50% room discount.
- 19.0.9.32–36 — Certified-roster labor/gratuity into Event Costs; bookkeeper
  report; green certified buttons; coordinator email; portal reopen.
- 19.0.9.24–31 — Department call-out roster (assign by name, start/end, rate,
  gratuity) + certified PDF; manager role by employee flag.
