"""Offline regressions for the payloads observed during live MCP testing."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolkit"))

from htb_agent.http import ApiError, ApiResponse, HtbApiClient, StateError
from htb_agent.services.challenges import challenge_match_rank
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


if __name__ == "__main__":
    unittest.main()
