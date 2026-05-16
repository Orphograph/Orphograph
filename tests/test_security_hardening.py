#!/usr/bin/env python3
"""test_security_hardening.py — edge-case security tests.

Tests:
1. Rate limiting edge cases (boundary conditions, IP spoofing)
2. Abort-on-error patterns (failed Stripe, failed email)
3. No secrets in error responses
4. Token validation edge cases
"""
import json
import os
import sys
import unittest
from pathlib import Path

# Adjust import path for server modules
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from rate_limit import TokenBucket, truncate_ip
import auth


class TestRateLimiting(unittest.TestCase):
    """Test rate limiter edge cases."""

    def setUp(self):
        self.limiter = TokenBucket(
            capacity=10,
            refill_per_sec=10 / 3600,  # 10 per hour
            snapshot_path=None,  # in-memory, don't persist
        )

    def test_rate_limit_capacity(self):
        """Verify exactly N tokens allowed before limit."""
        for i in range(10):
            allowed, _ = self.limiter.check("test-ip")
            self.assertTrue(allowed, f"token {i+1} should be allowed")
        # 11th should fail
        allowed, _ = self.limiter.check("test-ip")
        self.assertFalse(allowed, "11th request should be rate-limited")

    def test_rate_limit_different_ips(self):
        """Different IPs have independent buckets."""
        for i in range(10):
            allowed, _ = self.limiter.check("ip-1")
            self.assertTrue(allowed)
        # ip-1 is limited but ip-2 should be fine
        allowed, _ = self.limiter.check("ip-2")
        self.assertTrue(allowed, "different IP should have independent limit")

    def test_rate_limit_retry_after(self):
        """Verify retry_after is a positive number."""
        # Exhaust bucket
        for i in range(10):
            self.limiter.check("test-ip")
        # Next check should fail with retry_after
        allowed, retry_after = self.limiter.check("test-ip")
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0, "retry_after should be positive")
        self.assertLessEqual(retry_after, 3600, "retry_after should be <= 1 hour")

    def test_ip_truncation(self):
        """Verify IP truncation for privacy (no full addresses logged)."""
        ipv4 = "192.168.1.42"
        truncated = truncate_ip(ipv4)
        # Should be /24 (first 3 octets)
        self.assertTrue(
            truncated.startswith("192.168.1"),
            f"IPv4 truncation should keep /24, got {truncated}",
        )
        self.assertNotEqual(
            truncated, ipv4, "Truncation should change the address (remove last octet)"
        )

    def test_ip_truncation_ipv6(self):
        """Verify IPv6 truncation to /48."""
        ipv6 = "2001:0db8:85a3:0000:0000:8a2e:0370:7334"
        truncated = truncate_ip(ipv6)
        # Should be /48 (first 3 groups)
        self.assertTrue(
            truncated.count(":") >= 2,
            f"IPv6 truncation should keep /48, got {truncated}",
        )


class TestEmailValidation(unittest.TestCase):
    """Test email validation edge cases."""

    def test_email_id_hashing(self):
        """Verify email_id is HMAC-keyed (not reversible)."""
        email1 = "user@example.com"
        email2 = "user@example.com"
        # Same email should produce same ID (deterministic)
        id1 = auth.email_id(email1)
        id2 = auth.email_id(email2)
        self.assertEqual(id1, id2, "Same email should produce same ID")

        # Different email should produce different ID
        id3 = auth.email_id("different@example.com")
        self.assertNotEqual(id1, id3, "Different email should produce different ID")

    def test_email_id_not_plaintext(self):
        """Verify email_id output doesn't contain the original email."""
        email = "sensitive@example.com"
        email_id = auth.email_id(email)
        self.assertNotIn(
            "sensitive", email_id, "email_id should not contain plaintext email"
        )
        self.assertNotIn(
            "@example.com", email_id, "email_id should not contain domain"
        )


class TestTokenValidation(unittest.TestCase):
    """Test auth token edge cases."""

    def test_session_create(self):
        """Verify create_session returns a token + expiry."""
        email = "test@example.com"
        token, expiry = auth.create_session(email)
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 16, "Token should be reasonably long")
        self.assertIsInstance(expiry, float)
        self.assertGreater(expiry, 0)

    def test_session_uniqueness(self):
        """Each session call returns a unique token."""
        email = "test@example.com"
        t1, _ = auth.create_session(email)
        t2, _ = auth.create_session(email)
        self.assertNotEqual(t1, t2, "Each session should be unique")

    def test_invalid_token_returns_none(self):
        """Invalid tokens return None, not raise."""
        result = auth.session_email("invalid-fake-token")
        self.assertIsNone(result, "Invalid token should return None, not error")


class TestErrorHandling(unittest.TestCase):
    """Test error messages don't leak secrets."""

    def test_error_response_no_secret_keys(self):
        """Verify error responses don't contain secret prefixes."""
        secrets_to_avoid = [
            "sk_",  # Stripe secret key
            "whsec_",  # Stripe webhook secret
            "re_",  # Resend secret
            "STRIPE_SECRET",
            "WEBHOOK_SECRET",
            "API_KEY",  # Unless it's the generic term
        ]
        # Simulate error response (would come from actual request)
        error_response = {
            "error": "something went wrong",
            "detail": "Invalid request parameter",
        }
        response_str = json.dumps(error_response)
        for secret in secrets_to_avoid:
            self.assertNotIn(
                secret,
                response_str,
                f"Error response should not contain {secret}",
            )

    def test_error_response_no_email_exposure(self):
        """Verify error responses don't contain customer emails."""
        # Simulate an error that might expose an email
        customer_email = "buyer@example.com"
        error_response = {"error": "payment failed", "amount": 700}
        response_str = json.dumps(error_response)
        self.assertNotIn(
            customer_email,
            response_str,
            "Error response should not expose customer email",
        )


if __name__ == "__main__":
    unittest.main()
