"""Offline regressions for the payloads observed during live MCP testing."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolkit"))

from htb_agent.http import ApiError, ApiResponse, HtbApiClient, StateError
from htb_agent.parser import build_parser
from htb_agent.services.challenges import ChallengeService, challenge_match_rank
from htb_agent.services.machines import MachineService
from htb_agent.services.search import machine_match_rank


class SearchTests(unittest.TestCase):
    def test_machine_terms_span_fields(self):
        row = {"id": 987, "name": "Touch", "os": "Windows", "difficultyText": "Easy"}
        for query in ("windows easy", "  EASY\tWindows  "):
            with self.subTest(query=query):
                self.assertIsNotNone(machine_match_rank(row, query))
        self.assertIsNone(machine_match_rank(row, "windows hard"))
        self.assertIsNone(machine_match_rank(row, "  "))

    def test_challenge_terms_span_fields(self):
        row = {"id": 250, "name": "Protein Cookies", "challenge_category_id": 1, "difficulty": "Easy"}
        categories = {1: "Crypto"}
        self.assertIsNotNone(challenge_match_rank(row, "crypto easy", categories))
        self.assertIsNotNone(challenge_match_rank(row, "cookies crypto", categories))
        self.assertIsNone(challenge_match_rank(row, "web easy", categories))
        self.assertIsNone(challenge_match_rank(row, " ", categories))

    def test_exact_multiword_names_keep_best_rank(self):
        self.assertEqual(machine_match_rank({"name": "Two Words"}, "two words"), 0)
        self.assertEqual(challenge_match_rank({"name": "Weak RSA"}, "weak rsa"), 0)
        self.assertEqual(machine_match_rank({"id": 987, "name": "Touch"}, "987"), 3)


class MachineTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.service = MachineService(self.client, use_cache=False)

    def test_start_conflict_preserves_same_provisioning_assignment(self):
        info = {"id": 351, "name": "Cap", "ip": None, "isSpawning": True}
        self.client.get.return_value = {"info": info}
        for status in (200, 403, 409):
            with self.subTest(status=status):
                self.client.post.reset_mock()
                self.client.post.side_effect = ApiError(status, "You already have an active instance")
                result = self.service.start("351", "auto")
                self.assertEqual(result["info"], info)
                self.assertEqual(self.client.post.call_count, 1)
                self.assertNotIn("/vm/terminate", [c.args[0] for c in self.client.post.call_args_list])

    def test_start_conflict_never_replaces_another_machine(self):
        self.client.post.side_effect = ApiError(409, "You already have an active instance")
        self.client.get.return_value = {"info": {"id": 987, "name": "Touch", "ip": None}}
        with self.assertRaisesRegex(ApiError, "Touch"):
            self.service.start_with_retry("351", "auto", retry_for=1, interval=1)
        self.assertEqual(self.client.post.call_count, 1)

    def test_poll_waits_for_provisioning_to_finish_even_with_ip(self):
        self.client.get.side_effect = [
            {"info": {"id": 351, "ip": "10.10.10.1", "isSpawning": True}},
            {"info": {"id": 351, "ip": "10.10.10.1", "isSpawning": False}},
        ]
        with patch("htb_agent.services.machines.time.sleep") as sleep:
            info = self.service.wait_for_active_ip(351, timeout=3, interval=1)
        self.assertFalse(info["isSpawning"])
        sleep.assert_called_once()

    def test_timeout_reports_observed_state_and_uses_full_budget(self):
        self.client.get.return_value = {"info": {"id": 351, "ip": None, "isSpawning": True}}
        now = [0.0]
        def advance(seconds):
            now[0] += seconds
        with patch("htb_agent.services.machines.time.monotonic", side_effect=lambda: now[0]), \
             patch("htb_agent.services.machines.time.sleep", side_effect=advance):
            with self.assertRaisesRegex(StateError, "isSpawning=true"):
                self.service.wait_for_active_ip(351, timeout=2, interval=10)
        self.assertEqual(now[0], 2)
        self.client.post.assert_not_called()

    def test_disappeared_assignment_is_a_state_error(self):
        self.client.get.side_effect = [{"info": {"id": 351, "ip": None}}, {"info": None}]
        with patch("htb_agent.services.machines.time.sleep"):
            with self.assertRaisesRegex(StateError, "no machine is active"):
                self.service.wait_for_active_ip(351, timeout=3, interval=1)

    def test_summary_preserves_live_provisioning_over_profile(self):
        self.client.get.side_effect = [
            {"info": {"id": 351, "ip": None, "isSpawning": True}},
            {"info": {"id": 351, "ip": "stale", "playInfo": {"isActive": True, "isSpawned": True}}},
        ]
        info = self.service.active_summary()["info"]
        self.assertIsNone(info["ip"])
        self.assertTrue(info["spawning"])
        self.assertFalse(info["spawned"])

    def test_reset_refuses_a_provisioning_or_different_assignment(self):
        for info in ({"id": 351, "isSpawning": True}, {"id": 987}, None):
            with self.subTest(info=info):
                self.client.get.return_value = {"info": info}
                with self.assertRaises(StateError):
                    self.service.reset("351")
        self.client.post.assert_not_called()

    def test_reset_ready_machine_preserves_request_contract(self):
        self.client.get.return_value = {"info": {"id": 351, "ip": "10.10.10.1", "isSpawning": False}}
        self.client.post.return_value = {"success": True}
        self.assertEqual(self.service.reset(), {"success": True})
        self.client.post.assert_called_once_with("/vm/reset", data={"machine_id": 351})

    def test_machine_submit_omits_unrequested_rating(self):
        self.client.post.return_value = {"success": True}
        self.service.submit_flag("351", "HTB{sample}")
        self.client.post.assert_called_once_with(
            "/machine/own", data={"id": 351, "flag": "HTB{sample}"}, version="v5"
        )
        self.client.post.reset_mock()
        self.service.submit_flag("351", "HTB{sample}", 40)
        self.client.post.assert_called_once_with(
            "/machine/own", data={"id": 351, "flag": "HTB{sample}", "difficulty": 40}, version="v5"
        )

    def test_machine_submit_cli_accepts_omitted_rating(self):
        args = build_parser().parse_args(["machine", "submit", "351", "HTB{sample}"])
        self.assertIsNone(args.difficulty)


class ApiTests(unittest.TestCase):
    def test_http_200_explicit_failures_raise_application_error(self):
        client = HtbApiClient("http://localhost/api/v4", "test-secret")
        for body in ({"success": False}, {"status": False}, {"error": True}):
            with self.subTest(body=body):
                body = {**body, "message": "Rejected test-secret"}
                with patch.object(client, "request", return_value=ApiResponse(200, "application/json", json.dumps(body).encode())):
                    with self.assertRaises(ApiError) as raised:
                        client.post("/vm/spawn", {"machine_id": 351})
                self.assertEqual(raised.exception.status, 200)
                self.assertNotIn("test-secret", str(raised.exception))

    def test_false_data_values_are_not_application_failures(self):
        client = HtbApiClient("http://localhost/api/v4", "test-secret")
        body = {"info": {"active": False, "spawned": False}, "success": True}
        with patch.object(client, "request", return_value=ApiResponse(200, "application/json", json.dumps(body).encode())):
            self.assertEqual(client.get("/machine/active"), body)

    def test_get_status_false_can_be_a_legitimate_state(self):
        client = HtbApiClient("http://localhost/api/v4", "test-secret")
        body = {"status": False}
        with patch.object(client, "request", return_value=ApiResponse(200, "application/json", json.dumps(body).encode())):
            self.assertEqual(client.get("/connection/status"), body)


class ChallengeSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.service = ChallengeService(self.client, use_cache=False)
        self.client.get.return_value = {"challenge": {"id": 250, "difficulty": "Easy"}}
        self.client.post.return_value = {"message": "Congratulations!"}

    def test_omitted_rating_uses_listed_difficulty(self):
        for label, rating in (("Very Easy", 10), ("Easy", 20), ("Medium", 30), ("Hard", 40), ("Insane", 50)):
            with self.subTest(label=label):
                self.client.get.return_value = {"challenge": {"id": 250, "difficulty": label}}
                self.client.get.reset_mock()
                self.client.post.reset_mock()
                self.service.submit_flag("250", "HTB{sample}")
                self.client.get.assert_called_once_with("/challenge/info/250")
                self.client.post.assert_called_once_with(
                    "/challenge/own", data={"challenge_id": 250, "flag": "HTB{sample}", "difficulty": rating}
                )

    def test_explicit_rating_skips_difficulty_lookup(self):
        self.service.submit_flag("250", "HTB{sample}", 70)
        self.client.get.assert_not_called()
        self.client.post.assert_called_once_with(
            "/challenge/own", data={"challenge_id": 250, "flag": "HTB{sample}", "difficulty": 70}
        )

    def test_unknown_difficulty_stops_before_submission(self):
        self.client.get.return_value = {"challenge": {"id": 250, "difficulty": "Unrated"}}
        with self.assertRaisesRegex(ValueError, "no recognized listed difficulty"):
            self.service.submit_flag("250", "HTB{sample}")
        self.client.post.assert_not_called()

    def test_challenge_submit_cli_accepts_omitted_rating(self):
        args = build_parser().parse_args(["challenge", "submit", "250", "HTB{sample}"])
        self.assertIsNone(args.difficulty)


if __name__ == "__main__":
    unittest.main()
