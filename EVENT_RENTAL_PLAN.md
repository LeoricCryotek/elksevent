# Event Rental: Quote → Single-Line Invoice — Architecture Plan (v2)

Updated after reviewing the Facility Usage Agreement, current/proposed rate sheets,
the existing handwritten quote form, and the sibling modules
(`elkspurchase`, `elks_calendar_publisher`, `elksfrs`, `payment_clover`).

**Spine:** Odoo **Sales (`sale.order`)** is the staff-only itemized quote engine;
`project.task` stays as the operational + approval hub; the customer invoice is a
custom **single summary line**. Maximize native Odoo + reuse existing Elks modules.

---

## 0. Pending YOUR verification before any code changes

- **Product accounting fields not visible.** You'll confirm what's hiding the
  Accounting tab / GL fields on `product.template`. Plan assumes we add an explicit
  **FRS GL field** (`x_elks_income_account_id` → `elks.account`, type `income`) on
  event products, mirroring how `elkspurchase` puts `x_elks_account_id` on PO lines —
  rather than fighting the hidden native `property_account_income_id`. Confirm before I build.
- **Balance-due timing.** New policy says balance due **3 weeks prior**; the signed
  contract says **day prior**. Using 3 weeks (configurable). Confirm.
- **Tax rate to display.** You want a tax line **shown** even though it's $0 today
  (UBI may change that). Confirm the displayed rate (0% now, configurable per product).

---

## 1. Records and how they link

| Record | Role | Source |
|---|---|---|
| `res.partner` | Customer, matched/created from form email; `x_is_member` + member no. | Native |
| `project.task` | The **Event** — approval workflow, stages, date, start/end times, deposit | Native (extended) |
| `sale.order` | The **Quote** — itemized, **staff-only, never sent** | Native (Sales) |
| `sale.order.line` | Itemized lines; each line's product carries the FRS GL | Native |
| `product.product` | Room, Bar, Bartender, Cleaning, Garbage, Linen, Catering, Coordinator, etc. — all in an **EVENT** category, each with an FRS income GL | Native (extended) |
| `account.move` | **Two invoices** — Deposit + Final, each one summary line, Terms link | Native (custom builder) |
| `calendar.event` | Placeholder booking (greyed) → confirmed on approval | `elks_calendar_publisher` |
| `elks.account` | FRS GL accounts (income type) selected on products | `elksfrs` |
| `elks.lodge.settings` | Backend defaults (ratios, products, discount %, Terms URL, etc.) | `elksfrs` (extended) |

Links to add: `task.x_sale_order_id` ⇄ `sale.order.x_event_task_id`;
`task.x_calendar_event_id` → `calendar.event`; `account.move.x_event_id` (exists).

Single source of truth: `x_event_date`, `x_event_start_time`, `x_event_end_time`,
`x_guest_count`, member flag live on the **task**; the quote reads them.

Promo links: `x_facebook_event_url` + `x_related_url` captured on the form, shown on the
event, and available to the published calendar (`elks_calendar_publisher`).

---

## 1A. Event types & flags (drives billing + tax reporting)

| Flag | Meaning | Quote/charges? | Deposit/holds? | UBI? |
|---|---|---|---|---|
| `x_is_elks_event` | Lodge's **own** event, tagged from the Events module | **No** | **No** | No |
| `x_is_member` | Renter is an **Elks member** (gets member discount) | Yes | Yes | No |
| *(neither)* | **Non-member public rental** | Yes | Yes | **Yes** |

- **Elks Events** run the **same pipeline** (checklist, subtasks, board/floor approval,
  calendar) but **skip the quote, invoices, and deposit holds** — purely operational.
- `x_is_ubi` is **computed**: `not x_is_elks_event and not x_is_member and net_income != 0`.
- One workflow for all; the flags only gate the financial pieces and the tax report (§8A).

---

## 2. Approval workflow — mirror `elkspurchase` (Board → Floor)

Replace the single `x_board_status` with the proven `elkspurchase` pattern:

```
x_approval_state:  draft → board → floor → approved → (booked)   |  rejected
```

- `action_submit` — staff submit the event; `draft → board`.
- `action_board_approve` — Board passes; `board → floor`.
- `action_board_reject` — opens a **reject wizard** (reason); `→ rejected`.
- `action_floor_approve` — opens a **Floor Vote wizard** (motion #, votes for/against,
  meeting date, notes) exactly like `elkspurchase.floor.vote.wizard`; on approve
  `floor → approved`, post audit to chatter, **confirm the SO**, and **promote the
  calendar placeholder to confirmed**.
- `action_floor_reject` — reject wizard from the floor; `→ rejected`.
- `action_reset_to_draft` — from `rejected`.

Groups (reuse/parallel the `elkspurchase` privilege rows):
- **Event Coordinator** (requester) — create/submit, edit quote, deposits, invoice.
- **Event Board Member** — board approve/reject.
- **Event Floor Recorder** (Secretary) — record the floor vote.
- **Event Budget Override** (existing) — bypass double-booking + budget guards.

**Gating (same mechanism as `elkspurchase`):** the Board and Floor actions are
**restricted to their approver groups** — a Coordinator can *submit* but cannot
self-approve, the board buttons only render/run for **Event Board Member**, and the floor
vote only for **Event Floor Recorder**. You assign the designated approvers to these groups
under **Settings → Users** (exactly like the purchasing approvers). Buttons are hidden by
group on the form and re-checked server-side in each action (`has_group`), so the gate holds
even if someone reaches the action directly.

The existing task stages map to this: `New/Contacted` = draft, `Submitted` = board,
(floor) , `Approved/Booked` = approved, `Rejected` = rejected.

---

## 3. Products & pricing (EVENT category, each with an FRS GL)

**"Tagged as EVENT" = a product category** `Event Rentals`. The category is the tag,
**and** carries the default FRS income account; per-product override allowed. This is
how every quote line maps to a GL for the AP breakout.

**Room products** (Service type; cost usually $0; price = rate):

| Room | Current | Proposed | Notes |
|---|---|---|---|
| Dining Room | 900 | 1200 | includes dance floor today |
| Lodge Room | 1500 | 1500 | no change |
| Seaport | 600 | 750 | |
| Riverview | 600 | 750 | |
| Seaport/Riverview combo | 1000 | 1200 | |
| VIP (below bar) / VIP+Bar area | 500 | 750 | "before the bar itself is open" |
| Dance Floor / Stage | — | 150 | **new** separate rental |

> Proposed direction: roll cleaning/garbage/AV **into** the room rate so the customer
> sees fewer compounded lines. Those costs still post to their own GLs internally via
> the AP breakout — they just aren't separate customer lines.

**Fee / service products** (each with its own FRS GL):

- **Bar Fee** — proposed **$1,000 flat** (main bar open any duration, both wells, up to
  4 bartenders/day).
- **Extra Bartender** — **$150** each (parties over 100 guests). Guest-driven qty.
- **Cleaning Fee** — **$100**, non-refundable, due at signing.
- **Garbage Overage** — **$75** (parties over 50 guests; dumpster use).
- **Setup/Takedown Day** — **25%** of room rental per extra day.
- **Overtime** — **$100/hour** beyond allowed window (event must end by 12:00 AM).
- **Liability Insurance** — line item (certificate required on file pre-event).
- **Corkage Fee** — configurable (wine brought in per bylaws).
- **Food / Kitchen Use**, **Linen Service**, **Event Service Fee**, **Coordinator Fee**,
  **Catering (per plate)** — products with cost + price for margin.
- **Facility Usage and Rental** — the single customer-facing invoice product.

---

## 3A. Event Checklist → form questions, quote lines, and staff subtasks

The **"Check List for Events"** drives **three layers**, and the **website form is where the
customer's answers originate**:

1. **Form field (customer answer).** Every checklist item is asked as a **visible field on
   the public form**. On submit, each answer is written to a field on the event **and posted
   to the chatter** — a full audit of exactly what the customer requested.
2. **Quote line.** Priced items auto-add their product to the staff quote.
3. **Staff subtask.** Each *applicable* item becomes a **sub-task** (`project.task` child)
   for staff to **complete/arrange**, assigned by role and checked off — these are the
   operational to-dos, verified in the **After-Action** stage.

| # | Item | Form field (customer answer) | Quote line | Staff subtask |
|---|---|---|---|---|
| 1 | Tables | # tables | — | Set up tables |
| 2 | Chairs | # chairs | — | Set up chairs |
| 3 | Podium | yes/no | — | Place podium |
| 4 | Sound System | yes/no | AV Fee | Set up sound |
| 5 | Microphone | yes/no | AV Fee | Provide mic |
| 6 | Linen | yes/no + color + qty | Linen Rental | Set up linens |
| 7 | Insurance | yes/no (certificate) | Event Insurance | Collect insurance cert |
| 8 | Use of Kitchen | yes/no | (kitchen fee) | Prep kitchen |
| 9 | Garbage | yes/no | Garbage | Garbage removal |
| 10 | Number of Bars | # bars | Bar Fee | Staff the bars |
| 11 | Beer Tub | yes/no | Beer Tub | Stock beer tub |
| 12 | Decorations | yes/no + notes | — | Decorations |
| 13 | Takedown Decorations | us/them | — | Take down decorations (if us) |
| 14 | Setup | us/them | labor hrs (if us) | Event setup (if us) |
| 15 | Takedown | us/them | labor hrs (if us) | Event takedown (if us) |
| 16 | Gratuity | — (staff) | Event Service Fee | — |
| 17 | Food by Us | yes/no + menu | Event Food | Kitchen / food prep |
| 18 | Food Catered By | caterer + license/ins | — | Verify caterer license & insurance |
| 19 | Rooms Needed | room picker | Room line(s) | — |
| 20 | Extras Supplied | text | misc line | Provide extras |

**Template (yes — new events auto-build their subtasks):** new event tasks are created from
an **Event Checklist Template** (a small config model: lines = item, role/assignee,
applies-when). On create (web or manual), the event spawns with the right **subtasks**
already attached — staff don't build them by hand. Editing the template (no code) changes
what future events generate. This is the "create the project with the right subtasks and
all" you asked about.

Notes:
- Subtasks are generated **only for items that apply** (from the answers), so an event isn't
  cluttered with 20 empty tasks. Drive them from the configurable **checklist template**.
- Plus promo fields: **Facebook event URL** + **related website URL(s)** captured on the
  form, shown on the event, and available to the published calendar.
- All form questions are **visible fields** on the website form, mirrored to event fields and
  **logged in the chatter** on submission.
- **Setup/Takedown = Us** (14/15) triggers the **event-staff hours** the coordinator-fee
  button checks for (§12), so labor is never omitted.
- Required-before-booking items (insurance certificate, caterer license/insurance per
  Rules 4 & 13) can **gate Floor approval**.

---

## 4. Settings to add (`elks.lodge.settings`)

```
# guest-driven defaults
x_guests_per_bartender     Integer   default 100   # contract: extra bartender > 100
x_per_plate_cost           Monetary               # internal food cost / guest
x_per_plate_charge         Monetary               # billed / plate
# fees / policy
x_member_discount_pct      Float                  # member discount %
x_deposit_pct              Float     default 50.0  # deposit to secure date
x_balance_due_days_before  Integer   default 21    # balance due N days before event
x_coordinator_fee_pct      Float                  # % of room rental space occupied
x_event_tax_rate_id        m2o account.tax        # shown even if 0% (UBI later)
# documents
x_event_terms_url          Char                   # downloadable Rental Use Agreement
x_event_terms_label        Char                   # link text
# default product mappings (the items auto-added to every event quote)
x_facility_product_id, x_bar_product_id, x_bartender_product_id,
x_cleaning_product_id, x_garbage_product_id, x_linen_product_id,
x_catering_product_id, x_coordinator_product_id, x_insurance_product_id,
x_default_quote_product_ids (m2m)
```

---

## 5. Deposit & payment — two charges, two Clover products

You're charging this as **two payments** — a **deposit**, then the **event balance** — and
adding **two new Clover products** to match. Cleanest Odoo mapping = **two single-line
invoices**, each line matching one Clover product:

1. **Deposit invoice** — single line **"Event Deposit"**, amount = `x_deposit_pct` (50%) of
   the net total. Due **immediately at signing**. → new Clover **"Event Deposit"** item.
   Include the non-refundable **$100 cleaning fee** here (due at signing).
2. **Final invoice** — single line **"Facility Usage and Rental"** (a.k.a. "Event Balance"),
   amount = remaining balance. Due **3 weeks prior** (configurable). → new Clover
   **"Event"** item.

Deposit + Final = net total, so income isn't double-counted; both link to the same event/quote.

**How `payment_clover` matches (verified in the module):** for an invoice charge it builds
**one Clover order line per Odoo invoice product line**, using the line **name** + amount
(`_clover_build_line_items`); the `clover.item` ↔ `product_id` map drives the **terminal
picker**. So name each single line to match its Clover item, and map the two Odoo products
to the two new Clover items via `clover.item.product_id` for clean terminal/item reporting.

- **Refundable** deposit; **cancellation within 7 days forfeits 100%** → Clover refund /
  credit note (the module supports `_send_refund_request`).
- Pay online (inline form) or at the **Clover terminal**.

> Alternative (one invoice): a single invoice paid by two Clover charges. But both charges
> would then describe the *same* line — to show two different Clover items you'd have to use
> the terminal and pick the item each time. **Two invoices maps automatically; recommended.**

---

## 6. Member discount

- On the event/quote: `x_is_member` (Boolean), `x_member_number` (Char),
  `x_member_discount_pct` (default from settings).
- Apply it as a **discount on the single facility line** (Odoo line `discount` %), so the
  line's net `price_total` is what Clover charges — **one clean positive line** (a separate
  negative line would make Clover emit a confusing negative order line).
- The discount and **member number** still **show** on the quote and both invoices (discount
  column + member number in the line description / narration). Splits proportionally across
  the Deposit and Final invoices.
- Your Clover catalog already has **Member / Non-Member** item pairs (Room Fee, Bar Fee,
  Event Food, …) for à-la-carte **terminal** sales; the event summary invoice uses the
  discount-on-line approach instead of swapping items.

---

## 7. Tax / UBI

- Show a **tax line on every document even at $0** via a configurable `account.tax`
  (`x_event_tax_rate_id`). Today 0%; if UBI/sales-tax applies later, change the rate in
  settings — no code change.
- `x_is_ubi` is **computed** from the §1A flags (non-member, non-Elks event, has net income)
  — it powers the Assessor report (§8A). (Confirm treatment with your accountant.)

---

## 8. Documents & reports (three distinct outputs)

1. **AP GL Breakout (internal, to Accounts Payable/Treasurer).** Itemized quote grouped
   by **`elks.account`** (income GL) so AP sees the incoming money by budget line.
   Source = the sale-order lines + each product's FRS GL.
2. **Customer Contract / Quote PDF** = the **Facility Usage Agreement** layout (lodge
   header, room(s), date, times, guests, the 14 Rules of Usage, room/fee buckets, member
   discount, deposit schedule, signature + club-manager lines). This is what the customer
   signs.
3. **Customer Invoices (two)** = the **Deposit** and **Final** single-line invoices from §5
   (member discount applied on the line, shown tax + insurance), each with **Balance Due**
   and the **download hyperlink** to the Rental Use Agreement (from settings).

Sync rule (Option 2, unchanged): each invoice is a snapshot; **Refresh from Quote** while
draft; if posted, **cancel → re-create**.

---

## 8A. Property-tax (UBI) report for the Assessor

A one-click report, pulled straight from the **Events / Projects module**, listing the
**UBI events** — every event that is **not** an Elks event and **not** a member rental and
has income — with **NET income** per event and a grand total.

- **Source:** `project.task` where `x_is_ubi = True` over a date range (a wizard like the P&L,
  defaulting to the assessment/calendar year).
- **Columns:** date, event name, room(s), customer (non-member), **gross income**, **costs**,
  **net income**.
- **Output:** on-screen list **and** a printable **PDF** with the lodge header (assessor-ready).
- **Access:** `Events → Reports → Assessor / UBI Income`, **plus** a saved filter
  **"UBI / Taxable"** and a **"Group by: Event Type / Member"** on the events list so you can
  eyeball or export the same set from the Projects views anytime.
- Member rentals and Elks events are **excluded by definition**, so the list is assessor-ready
  without manual filtering.

---

## 9. Signed agreement

- Provide the Rental Use Agreement as a **downloadable PDF link** (the `x_event_terms_url`),
  surfaced on the invoice and customer portal. (E-signature via Odoo Sign is a later option,
  not required now.)

---

## 10. Calendar integration (`elks_calendar_publisher` / `calendar.event`)

- On event **creation**, create a native **`calendar.event`** as a **greyed placeholder**
  (tentative — e.g. name prefixed "TENTATIVE", `show_as='free'`, a muted banner style),
  dated from the **task `date_deadline`** with the **customer-entered start/end times**.
- On **Board + Floor approval**, promote it to **confirmed** (drop the tentative marker,
  set the real banner/graphic) so it appears as a real booking on the published calendar.
- Store `task.x_calendar_event_id` for the link; update times if the event date changes.

> Exact "greyed" mechanism (tentative flag vs. banner style vs. a dedicated calendar) to be
> finalized against the publisher's renderer — flagged as an implementation detail.

---

## 11. Double-booking prevention

- Use the task's `x_event_date` + `x_event_start_time`/`x_event_end_time` + the room(s).
- A `@api.constrains` blocks a **non-cancelled** event from overlapping the same room/date/time.
- **Override** allowed for **Event Coordinator / Event Budget Override** groups (records who
  overrode + reason), so staff can intentionally double-book/turn rooms.
- Capacity check: **skipped** per your instruction.

---

## 12. Coordinator fee — button (don't let staff forget it)

- `action_add_coordinator_fee` button adds/refreshes the **Coordinator Fee** line.
- Fee = **% of room rental space occupied** (sum of room-rental line subtotals ×
  `x_coordinator_fee_pct`), configurable.
- Also **validates event-staff hours are present** (Setup / Event / Cleanup labor lines);
  warns if missing so labor isn't omitted from the quote.

---

## 13. Email templates (one per step)

Native `mail.template` for: (1) request received → customer; (2) submitted to Board →
secretary/board; (3) Floor vote scheduled/recorded; (4) **approved → customer** (booking
confirmed + deposit instructions + agreement link); (5) **denied → customer**; (6) deposit
received / receipt; (7) **balance-due reminder** (auto, 3 weeks prior, via cron);
(8) invoice sent; (9) post-event / damage-deposit refund notice.

---

## 14. Online payment & portal

- **Clover** via `payment_clover` (already installed) for deposit + balance, online or
  terminal.
- **Portal carefully scoped:** customer sees the **invoice** and the **agreement download**
  only. The **sale-order quote is never portal-shared** — verify with an access rule /
  record rule so portal/public users cannot read `sale.order` for events.

---

## 15. Build order (phases)

1. **Approval workflow** — port the `elkspurchase` board/floor pattern (states, floor-vote
   + reject wizards, **group-gated** approvers) onto `project.task`.
2. **Event types & flags** — `x_is_elks_event`, `x_is_member`, computed `x_is_ubi` (§1A),
   gating the financial pieces.
3. **Checklist template + subtasks** — config template that spawns the right subtasks on
   event create (§3A).
4. **Products + EVENT category + FRS GL** — *after you confirm the product accounting
   fields*. Seed room/fee products with current (or proposed) rates.
5. **Settings** — add all fields in §4 (incl. checklist template, promo-URL fields); expose
   on the Lodge Settings form.
6. **Quote engine** — `task ⇄ sale.order` link, smart button, auto-add default products,
   guest-driven bartender/catering build, coordinator-fee button.
7. **Double-booking** constraint + override.
8. **Invoice builder** — two single-line invoices + member discount + shown tax + Terms link;
   Refresh-from-quote (draft); Clover-matched products.
9. **Documents** — AP GL breakout, customer contract PDF, invoice PDFs, **Assessor/UBI report (§8A)**.
10. **Calendar** — placeholder on create, promote on approval.
11. **Emails** — the nine templates + balance-due cron.
12. **Website intake** — partner find/create + task (+ subtasks) + draft quote + placeholder
    calendar + promo URLs + chatter logging.
13. **Portal/security** review; **P&L** repointed to sale-order lines.

---

## 16. Open items to confirm (besides §0)

- Roll minor fees into room rate (customer-simple) vs. show them — confirm per fee.
- Adopt **proposed** new rates now, or seed **current** rates and switch later?
- Member discount: percentage value, and does it stack with the in-house-caterer
  100-guest discount (contract rule 14)?
- Insurance: fixed amount, or entered per event from the certificate?
