import unittest

from scripts.register_campaign_routine import (
    build_action,
    build_schedule_trigger,
)
from src.campaign_autopilot.workiq_email import (
    augmented_instructions,
    email_instruction,
)


class ScheduleTriggerTests(unittest.TestCase):
    def test_schedule_trigger_shape(self):
        trigger = build_schedule_trigger("0 6 * * 1", "UTC")
        # Exactly one trigger entry, of type 'schedule', with cron + time zone.
        self.assertEqual(len(trigger), 1)
        (entry,) = trigger.values()
        self.assertEqual(entry["type"], "schedule")
        self.assertEqual(entry["cron_expression"], "0 6 * * 1")
        self.assertEqual(entry["time_zone"], "UTC")

    def test_custom_timezone(self):
        trigger = build_schedule_trigger("0 7 * * 1-5", "Europe/Berlin")
        (entry,) = trigger.values()
        self.assertEqual(entry["time_zone"], "Europe/Berlin")


class ActionTests(unittest.TestCase):
    def test_responses_api_action(self):
        action = build_action("campaign-agent")
        self.assertEqual(action["type"], "invoke_agent_responses_api")
        self.assertEqual(action["agent_name"], "campaign-agent")


class WorkIQInstructionTests(unittest.TestCase):
    def test_email_instruction_lists_recipients(self):
        text = email_instruction(["a@x.com", "b@x.com"], "Weekly Briefing")
        self.assertIn("Work IQ Outlook Mail", text)
        self.assertIn("a@x.com, b@x.com", text)
        self.assertIn("Weekly Briefing", text)
        # Guardrail: never email raw procurement cost.
        self.assertIn("Do not include raw procurement cost", text)

    def test_augmented_instructions_appends(self):
        base = "You are the Campaign Planning Agent."
        out = augmented_instructions(base, ["m@x.com"], "Briefing")
        self.assertTrue(out.startswith(base))
        self.assertIn("DELIVERY INSTRUCTION (Work IQ Mail)", out)
        self.assertIn("m@x.com", out)


if __name__ == "__main__":
    unittest.main()
