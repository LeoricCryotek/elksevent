/** @odoo-module **/

import { FormStatusIndicator } from "@web/views/form/form_status_indicator/form_status_indicator";
import { FormCompiler } from "@web/views/form/form_compiler";
import { Notebook } from "@web/core/notebook/notebook";
import { patch } from "@web/core/utils/patch";
import { useBus } from "@web/core/utils/hooks";

const REVEAL_INVALID = "ELKSEVENT:REVEAL_INVALID";
const afterRender = () => new Promise((resolve) =>
    requestAnimationFrame(() => requestAnimationFrame(resolve)));

// Odoo tracks invalid fields per notebook page. Include the fields in the
// nested Financials notebook in its containing page's field list as well.
patch(FormCompiler.prototype, {
    compileNotebook(el, params) {
        const parentFields = params.notebookPageFields;
        const isEventFinancials = el.closest("page[name='event_financials']");
        const result = super.compileNotebook(...arguments);
        if (isEventFinancials && parentFields) {
            for (const field of el.querySelectorAll("field[name]")) {
                if (!field.parentElement.closest("field")) {
                    const name = field.getAttribute("name");
                    if (!parentFields.includes(name)) {
                        parentFields.push(name);
                    }
                }
            }
            params.notebookPageFields = parentFields;
        }
        return result;
    },
});

patch(Notebook.prototype, {
    setup() {
        super.setup(...arguments);
        // The model bus and record identity keep this scoped to the event form,
        // including forms in dialogs. Newly mounted nested notebooks participate
        // on the next pass, after their containing page has opened.
        if (this.env.model) {
            useBus(this.env.model.bus, REVEAL_INVALID, ({ detail }) => {
                if (this.env.model.root !== detail.record) {
                    return;
                }
                this.computeInvalidPages();
                const page = this.navItems.find(([id]) =>
                    this.invalidPages.has(id) && !this.disabledPages.includes(id));
                if (page) {
                    detail.pending.push(this.activatePage(page[0]));
                }
            });
        }
    },
});

patch(FormStatusIndicator.prototype, {
    async revealEventIssue(ev) {
        const record = this.props.model.root;
        if (record.resModel !== "project.task" || !record.data.x_is_event) {
            return;
        }
        const form = ev.currentTarget.closest(".o_form_view");
        // Validate and display Odoo's field list, without attempting to save.
        await record.checkValidity({ displayNotification: true });
        for (let depth = 0; depth < 4; depth++) {
            const request = { record, pending: [] };
            this.props.model.bus.trigger(REVEAL_INVALID, request);
            await Promise.all(request.pending);
            await afterRender();
            if (!form?.isConnected) {
                return;
            }
            const invalid = [...form.querySelectorAll(".o_field_invalid")]
                .find((element) => element.getClientRects().length);
            if (invalid) {
                invalid.scrollIntoView({ block: "center", behavior: "smooth" });
                const input = invalid.matches("input, textarea, select, [contenteditable='true']")
                    ? invalid
                    : invalid.querySelector(
                        "input:not([disabled]):not([type='hidden']), textarea:not([disabled]), select:not([disabled]), [contenteditable='true']");
                input?.focus({ preventScroll: true });
                return;
            }
        }
    },
});
