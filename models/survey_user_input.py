# -*- coding: utf-8 -*-
"""Tie a customer feedback survey response back to its event.

HUMAN
-----
When an event moves to After Action, the customer is emailed a feedback
survey (see project_task._send_customer_survey). This links the response to
the event and, the moment the customer finishes it, drops a to-do activity on
the event for the coordinator and emails them so they can read the feedback.

AI
--
- Adds `x_event_id` (-> project.task) to `survey.user_input`.
- `_mark_done` override fires `_elks_notify_event_coordinator` once.
"""
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    x_event_id = fields.Many2one(
        'project.task', string="Event", index=True, copy=False,
        help="The event this feedback survey was sent out for.",
    )

    def _mark_done(self):
        res = super()._mark_done()
        for ui in self:
            if ui.x_event_id:
                ui._elks_notify_event_coordinator()
        return res

    def _elks_notify_event_coordinator(self):
        """Activity + email to the coordinator when feedback comes in."""
        self.ensure_one()
        event = self.x_event_id
        if not event:
            return
        try:
            user = event._after_action_user()
            event.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=("Customer feedback received: %s"
                         % (event.name or 'Event')),
                note=("The customer completed the feedback survey for this "
                      "event. Review their responses."),
                user_id=user.id)
            template = self.env.ref(
                'elksevent.mail_template_survey_done_coordinator',
                raise_if_not_found=False)
            if template and user.email:
                template.sudo().with_context(
                    coordinator_email=user.email).send_mail(
                        event.id, force_send=True)
        except Exception as e:  # noqa: BLE001 - never block survey submission
            _logger.warning(
                "Coordinator survey-done notice failed for event %s: %s",
                event.id, e)
