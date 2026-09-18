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

- 19.0.9.74 — Printable W-9 and I-9 (Section 1) PDFs filled from the 1099
  application, for the paper folder — "Print W-9" / "Print I-9" buttons on the
  applicant (and in its Print menu). I-9 Section 2 is left for the employer to
  complete in person against the uploaded documents.
- 19.0.9.73 — 1099 onboarding completeness: added W-9 federal tax classification
  + backup-withholding certification to the form/applicant. "Create 1099
  Employee" now sets Contract Type = 1099 Contractor, employee_type =
  freelance, copies the SSN to the employee's SSN field, logs the W-9 details,
  and leaves the wage blank (event pay comes from the timecard). Free-text
  roster names are matched and linked to the new employee record.
- 19.0.9.72 — New employees' attendance/POS PIN defaults to the last 4 digits of
  their phone (work → mobile → private), or 0000 when there's no phone (only
  when a PIN isn't given). Migration backfills employees with a blank PIN.
- 19.0.9.71 — 1099 onboarding uploads now also attach to the application as
  regular (viewable/downloadable) attachments in the sidebar, not just binary
  fields; convert-to-employee copies them onto the employee.
- 19.0.9.70 — Fix: 1099 onboarding submit crashed with "Invalid field 'name' in
  'hr.applicant'" — Odoo 19 has no name field on hr.applicant; use partner_name.
- 19.0.9.69 — Event (call-out) rate now PAYS 1099 contractors only; W-2
  employees are paid their normal wage for event hours (event pay = $0 for
  them). The P&L billed-vs-actual and cost-line true-up value actuals as 1099 @
  call-out rate, W-2 @ wage. The "Discount" is now an ADDITIONAL courtesy
  discount (reason required, room/profit only) — the automatic Member /
  Non-Profit 50% and Elks-event $0 room are the room's price, not counted as a
  discount. Timecard gains a "PAYMENT SUMMARY — who to pay & how much" block
  (regular hours, event pay, gratuity, coordinator fee, cash to pay).
  (Requires elksattendance 19.0.5.16.)
- 19.0.9.68 — Re-send button hidden once a call-out is certified (only the amber
  "Resend (changes)" shows if a certified plan later changes). The "Updated
  Since You Were Sent This" old→new diff now sits at the TOP of the manager's
  portal call-out form (prominent), in addition to the inline highlights.
- 19.0.9.67 — Labor billed-vs-actual in the P&L: certified-roster Event Costs
  lines now compute "Actual Labor" from the clocked event pay (hours worked on
  the event x the call-out rate), lighting up the "Billed − Actual" true-up.
  Financials gains an "Event Labor: Billed vs Actual (clocked)" summary
  (billed planned vs actual clocked, with variance).
- 19.0.9.66 — Event-rate pay on the timecard: hr.attendance carries x_event_rate
  / x_event_pay (the department manager's call-out rate x the hours clocked for
  that event). The elksattendance timecard's event-pay area now reads those
  clocked event shifts as a second area on the same card (falling back to the
  planned roster when there's no clock-in), labelled by pay category — "1099
  Event Pay" for contractors, "Event Pay (call-out rate)" for hourly employees.
  (Requires elksattendance 19.0.5.15.)
- 19.0.9.65 — /my portal always shows the Department Call-Outs card (empty for
  users without access). 1099 contractor onboarding: a published "Join as a
  1099" careers position (/jobs) links to a mobile I-9/W-9 form that collects
  identity, taxpayer ID, work authorization, attestation, and document uploads
  (photo ID + SSN/work-auth doc) and files a recruitment applicant. Once
  approved, "Create 1099 Employee" makes the employee with Pay Category = 1099
  and copies the documents. Adds hr_recruitment + website_hr_recruitment as
  dependencies; employees carry a Pay Category (W-2 / 1099 / Contract).
- 19.0.9.64 — Call-out change highlights now baseline on certify and via a
  migration for existing call-outs, so managers see what changed after
  certifying. Added always-available per-department "Re-send" buttons to notify
  a manager of an update on demand (without un-certifying). New Gratuity
  Disbursement Excel worksheet for the bookkeeper: per payee, 1099 vs W-2, pay
  (hours x rate), tips (gratuity share), and check total, 1099 payees first.
- 19.0.9.63 — After-Action Event Sales Income (net) now flows into the Event
  P&L report: on-site net sales appear as their own income lines and add to
  Total Income and Net Profit. Blank categories are excluded.
- 19.0.9.59 — Event Sales Income moved to the After Action tab and re-worded to
  NET (Clover accounts for cost of goods). Removed the stray Currency selector
  on the Timesheets tab. Reworded the change-warning to point at the amber
  "Resend" button. Documents-tab call-out list reordered/optional so headers
  aren't cut off (Updated column). Manager portal now highlights each changed
  request/schedule value inline (new value highlighted, old shown struck), so
  managers see what changed whether or not a resend was requested.
- 19.0.9.58 — Per-person gratuity: managers can disburse the department pool
  across their roster (a Gratuity column on the call-out sheet, with "split
  evenly" and pool/assigned/remaining tracking), so payroll knows each person's
  share even if the manager is out. The Gratuity Distribution report now reads
  those actual per-person shares by department. Coordinator PDF: fixed the
  dropped "ff" ligature (Buffet) and em-dash/middle-dot mojibake, laid the
  schedule out in two columns, and added a gratuity column to the certified
  rosters. Documents-tab call-out buttons now spread horizontally (wrap only on
  narrow screens).
- 19.0.9.57 — Added an "Antlers Service" checkbox under Setup & Service; when
  ticked it adds an Antlers Service line ($200 default, configurable in Lodge
  Settings) to the Event Costs (Event Services category). Includes a migration
  to seed the cost type and default fee on existing databases.
- 19.0.9.56 — /my/callouts list upgraded: coordinators/officers see every
  call-out, department managers see theirs; grouped by event, soonest first,
  past events hidden by default; a sort dropdown (soonest/furthest/name/status),
  a filter (upcoming/needs staffing/certified/all incl. past), a search box
  (event/manager/department), and click-to-sort column headers within each
  event.
- 19.0.9.55 — /my portal "Department Call-Outs" card now appears for any user
  who has a call-out assigned, matching them across their portal contact, their
  employee work-contact, or a partner with the same email (not just their login
  partner). Same broadened matching applies to the call-out list and detail
  access.
- 19.0.9.54 — Financials: new "Event Sales Income" section (drink / food / entry
  / other on-site sales, entered by hand or later pulled from Clover) that adds
  a "Net incl. Event Sales" line to the P&L summary and rows to the bookkeeper
  report.
- 19.0.9.53 — Coordinator day-of sheet rebuilt: a single chronological SCHEDULE
  (entry, clean-prior, setup, every certified staff shift in/out, event
  start/end, access windows, cleanup), certified call-out rosters with shift
  times and gratuity, per-department customer requests (drinks, menu, cleaning),
  caterer, committee, Elks/Non-Profit flags, and wedding contacts. Call-out
  change tracking: sending a call-out snapshots the requested plan; if the
  coordinator later edits the shared schedule or a department's request, an
  onchange prompt appears, the Documents tab shows an amber Resend button with a
  highlighted old->new diff, and the manager's portal shows "Originally vs Now"
  until it's resent (benign edits can be left).
- 19.0.9.52 — Certified call-out roster is now the ONLY labor source: the old
  checkbox/plan "… (labor)" estimate lines are no longer created. Each certified
  roster posts one Event Costs line billing the inflated labor (rate + overhead,
  rounded UP to $5) with the raw labor as COGS, plus its gratuity line. Portal
  call-out: clear-line and add-person controls, employees already committed on
  another department's call-out for the same event are hidden, cleanup deadlines
  and request context show only where relevant per department, and the overhead
  rounding is no longer surfaced to managers. Backend call-out buttons spaced.
- 19.0.9.51 — Food section reworked: Catering Details (how it's served), Menu
  (items to fulfill), and a "Catered By" picker filtered to catering companies
  (new "Is Catering" flag on business contacts) defaulting to the Lodge Kitchen,
  which drives the in-house kitchen call-out. Bar/kitchen gratuity moved off the
  event to the call-out sheets (fed to the event on certify; shown under
  Timesheets, posted to Event Costs). Call-out staff rate now bills at rate +
  lodge overhead (default $5, configurable) rounded to the nearest $5 (+ pre/post
  migration so prior "Food by Us" events point at the Lodge Kitchen).
- 19.0.9.49–50 — Removed Event Workers; tab order (Rooms after Event Details,
  Timesheets before Event Costs).
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
