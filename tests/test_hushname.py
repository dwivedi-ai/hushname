"""Offline tests - no network. Run: python3 -m unittest discover tests"""
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from hushname import cli, porkbun, registry, storage
from hushname.scoring import score


class BlockSignal(unittest.TestCase):
    def test_real_registry_messages(self):
        self.assertEqual(registry.block_signal("solar.dev blocked by BSA"), "blocked")
        self.assertEqual(registry.block_signal(
            "This domain has been reserved by the registry, and is not available "
            "for registration."), "reserved")

    def test_plain_not_found_is_not_a_block(self):
        for text in ["retrieval.dev not found", "Object not found", "No match for \"X.COM\".",
                     "Domain not found.", "Copyright. All rights reserved."]:
            self.assertIsNone(registry.block_signal(text), text)


def _http_404(body: dict):
    return urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(json.dumps(body).encode()))


class RdapLookup(unittest.TestCase):
    def setUp(self):
        self.throttle = registry.HostThrottle(0)

    @mock.patch("urllib.request.urlopen")
    def test_record_means_taken(self, urlopen):
        urlopen.return_value.__enter__.return_value = io.BytesIO(b"{}")
        self.assertEqual(registry.rdap_lookup("a.com", "https://x/", self.throttle)[0], registry.TAKEN)

    @mock.patch("urllib.request.urlopen")
    def test_404_is_unregistered_not_available(self, urlopen):
        urlopen.side_effect = _http_404({"description": ["a.dev not found"], "title": "Not Found"})
        self.assertEqual(registry.rdap_lookup("a.dev", "https://x/", self.throttle)[0],
                         registry.UNREGISTERED)

    @mock.patch("urllib.request.urlopen")
    def test_404_with_block_message_is_reserved(self, urlopen):
        urlopen.side_effect = _http_404({"description": ["solar.dev blocked by BSA"], "title": "Not Found"})
        self.assertEqual(registry.rdap_lookup("solar.dev", "https://x/", self.throttle)[0],
                         registry.RESERVED)


class WhoisSecondOpinion(unittest.TestCase):
    def check(self, text):
        with mock.patch.object(registry, "whois_query", return_value=text):
            return registry.whois_second_opinion("x.xyz", "whois.example", registry.HostThrottle(0))

    def test_reserved(self):
        self.assertEqual(self.check("This domain has been reserved by the registry")[0],
                         registry.RESERVED)

    def test_not_found_adds_nothing(self):
        self.assertIsNone(self.check("The queried object does not exist: DOMAIN NOT FOUND"))

    def test_record_means_taken(self):
        self.assertEqual(self.check("Domain Name: X.XYZ\nRegistrar: Foo Inc\n")[0], registry.TAKEN)


class Porkbun(unittest.TestCase):
    def test_interpret(self):
        base = {"avail": "yes", "premium": "no", "price": "9.73", "regularPrice": "11.08",
                "minDuration": 1, "additional": {"renewal": {"price": "11.08"}}}
        r = porkbun.interpret(base)
        self.assertEqual((r["status"], r["renew_usd"]), (registry.AVAILABLE, 11.08))
        self.assertEqual(porkbun.interpret({**base, "premium": "yes"})["status"], registry.PREMIUM)
        self.assertEqual(porkbun.interpret({**base, "avail": "no"})["status"], registry.UNAVAILABLE)

    def test_verification_updates_rows(self):
        rows = [{"domain": "a.dev", "status": "unregistered", "detail": "", "renew_usd": 16,
                 "in_budget": "yes"},
                {"domain": "b.dev", "status": "unregistered", "detail": "", "renew_usd": 16,
                 "in_budget": "yes"}]
        answers = {"a.dev": {"status": registry.UNAVAILABLE, "detail": "porkbun: not available",
                             "reg_usd": "", "renew_usd": "", "min_years": ""},
                   "b.dev": {"status": registry.PREMIUM, "detail": "porkbun: premium price",
                             "reg_usd": 400.0, "renew_usd": 16.0, "min_years": 1}}
        with mock.patch.object(porkbun, "credentials", return_value=("k", "s")), \
             mock.patch.object(porkbun, "check", return_value=answers):
            self.assertEqual(cli.apply_verification(rows, 30), "porkbun")
        self.assertEqual(rows[0]["status"], registry.UNAVAILABLE)
        self.assertEqual((rows[1]["status"], rows[1]["in_budget"]), (registry.PREMIUM, "no"))

    def test_no_keys_means_off(self):
        with mock.patch.object(porkbun, "credentials", return_value=None):
            self.assertEqual(cli.apply_verification([], 30), "off")


class Scoring(unittest.TestCase):
    def test_shorter_beats_longer(self):
        self.assertGreater(score("zap"), score("zapperific"))

    def test_hyphen_and_digits_hurt(self):
        self.assertGreater(score("gpuhub"), score("gpu-hub"))
        self.assertGreater(score("gpuhub"), score("gpuhub2"))

    def test_bounds(self):
        self.assertLessEqual(score("a", 50), 100)
        self.assertGreaterEqual(score("x" * 60, -50), 0)


class Inputs(unittest.TestCase):
    def test_read_labels(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "w.txt"
            p.write_text("# comment\nFoo bar,baz\nfoo.com  # dup\nbad_name\n-x\n")
            good, bad = storage.read_labels([str(p)], "extra")
        self.assertEqual(good, ["foo", "bar", "baz", "extra"])
        self.assertEqual(bad, ["bad_name", "-x"])


class Outputs(unittest.TestCase):
    def test_run_round_trip(self):
        rows = [{"domain": "a.si", "status": "unregistered", "score": 60, "in_budget": "yes",
                 "reg_usd": 13, "renew_usd": 15, "min_years": 1, "price_source": "estimate",
                 "detail": "rdap: not found"}]
        meta = {"started": "now", "inputs": ["w.txt"], "tlds": ["si"], "budget": 30,
                "verification": "off"}
        with tempfile.TemporaryDirectory() as d:
            run = storage.new_run_dir(d, "test")
            storage.write_run(run, rows, meta)
            back, meta2 = storage.read_run(run)
            report = (run / "report.md").read_text()
        self.assertEqual(back[0]["domain"], "a.si")
        self.assertEqual(meta2["tlds"], ["si"])
        self.assertIn("Registrar verification was off", report)


if __name__ == "__main__":
    unittest.main()
