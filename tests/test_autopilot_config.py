import unittest

from src.campaign_autopilot.config import AutopilotConfig, _split_recipients


class SplitRecipientsTests(unittest.TestCase):
    def test_handles_commas_and_semicolons(self):
        self.assertEqual(
            _split_recipients("a@x.com, b@x.com; c@x.com"),
            ["a@x.com", "b@x.com", "c@x.com"],
        )

    def test_drops_empties_and_whitespace(self):
        self.assertEqual(_split_recipients(" , a@x.com ,, "), ["a@x.com"])

    def test_empty_string(self):
        self.assertEqual(_split_recipients(""), [])


class ValidateTests(unittest.TestCase):
    def _base(self, **overrides) -> AutopilotConfig:
        cfg = AutopilotConfig()
        for key, value in overrides.items():
            setattr(cfg, key, value)
        return cfg

    def test_file_provider_always_valid(self):
        # No recipients / sender needed for the file (dry-run) provider.
        self._base(provider="file").validate()

    def test_unknown_provider_rejected(self):
        with self.assertRaises(ValueError):
            self._base(provider="carrier-pigeon").validate()

    def test_acs_requires_recipients_sender_and_target(self):
        with self.assertRaises(ValueError):
            self._base(provider="acs").validate()
        with self.assertRaises(ValueError):
            self._base(provider="acs", recipients=["a@x.com"]).validate()
        with self.assertRaises(ValueError):
            self._base(
                provider="acs", recipients=["a@x.com"], sender_address="from@x.com"
            ).validate()
        # Valid once an ACS target is supplied.
        self._base(
            provider="acs",
            recipients=["a@x.com"],
            sender_address="from@x.com",
            acs_endpoint="https://x.communication.azure.com",
        ).validate()

    def test_smtp_requires_host(self):
        with self.assertRaises(ValueError):
            self._base(
                provider="smtp", recipients=["a@x.com"], sender_address="from@x.com"
            ).validate()
        self._base(
            provider="smtp",
            recipients=["a@x.com"],
            sender_address="from@x.com",
            smtp_host="smtp.example.com",
        ).validate()


if __name__ == "__main__":
    unittest.main()
