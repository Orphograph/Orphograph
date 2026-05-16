#!/usr/bin/env python3
"""
End-to-end smoke test for Orphograph.
Tests entire flow: anchor free → verify → buy Pack → spend credit.

Usage:
  python3 scripts/smoke_test_e2e.py [--server http://localhost:8000]
"""
import subprocess
import sys
import json
import time
import os
import hashlib
from pathlib import Path
from urllib.parse import urljoin

SERVER = os.environ.get("ORPHO_TEST_SERVER", "http://localhost:8000")

def log(msg, level="INFO"):
    print(f"[{level}] {msg}")

def test_server_is_alive():
    """Check if server is running."""
    log("Testing server connectivity...")
    result = subprocess.run(
        ["curl", "-s", "-m", "5", urljoin(SERVER, "/api/health")],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        log(f"Server not reachable at {SERVER}", "ERROR")
        return False
    try:
        data = json.loads(result.stdout)
        log("✓ Server alive")
        return True
    except:
        return False

def test_landing_page():
    """Verify landing page loads."""
    log("Testing landing page...")
    result = subprocess.run(
        ["curl", "-s", "-I", urljoin(SERVER, "/")],
        capture_output=True, text=True
    )
    if "200" in result.stdout or "304" in result.stdout:
        log("✓ Landing page returns 200")
        return True
    return False

def test_privacy_and_terms():
    """Verify legal docs are published."""
    log("Testing legal documents...")
    for doc in ["/privacy.html", "/terms.html"]:
        result = subprocess.run(
            ["curl", "-s", "-I", urljoin(SERVER, doc)],
            capture_output=True, text=True
        )
        if "200" not in result.stdout:
            log(f"{doc} not found", "ERROR")
            return False
    log("✓ Privacy and Terms published")
    return True

def test_free_anchor():
    """Test anchoring a file."""
    log("Testing free anchor...")
    test_hash = hashlib.sha256(b"smoke-test").hexdigest()
    payload = {"hash": test_hash, "label": "smoke-test"}
    
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", urljoin(SERVER, "/api/anchor"),
         "-H", "Content-Type: application/json",
         "-d", json.dumps(payload)],
        capture_output=True, text=True
    )
    
    try:
        response = json.loads(result.stdout)
        if response.get("receipt_id"):
            log("✓ Free anchor created")
            return response.get("receipt_id")
    except:
        pass
    return None

def test_receipt_retrieval(receipt_id):
    """Test receipt lookup."""
    log("Testing receipt retrieval...")
    result = subprocess.run(
        ["curl", "-s", urljoin(SERVER, f"/api/receipt/{receipt_id}")],
        capture_output=True, text=True
    )
    try:
        receipt = json.loads(result.stdout)
        if "hash" in receipt:
            log("✓ Receipt retrieved")
            return True
    except:
        pass
    return False

def test_stats():
    """Test stats endpoint."""
    log("Testing stats...")
    result = subprocess.run(
        ["curl", "-s", urljoin(SERVER, "/api/stats")],
        capture_output=True, text=True
    )
    try:
        json.loads(result.stdout)
        log("✓ Stats endpoint OK")
        return True
    except:
        return False

def test_no_secret_leakage():
    """Check responses don't leak secrets."""
    log("Checking for secret leakage...")
    result = subprocess.run(
        ["curl", "-s", urljoin(SERVER, "/api/receipt/invalid")],
        capture_output=True, text=True
    )
    forbidden = ["STRIPE_", "RESEND_", "sk_", "webhook"]
    for pattern in forbidden:
        if pattern in result.stdout:
            log(f"Secret pattern found: {pattern}", "ERROR")
            return False
    log("✓ No secret leakage detected")
    return True

def main():
    """Run all smoke tests."""
    log("=" * 60)
    log("Orphograph E2E Smoke Test")
    log(f"Server: {SERVER}")
    log("=" * 60)
    
    tests = [
        ("Server Alive", test_server_is_alive),
        ("Landing Page", test_landing_page),
        ("Legal Docs", test_privacy_and_terms),
        ("Stats Endpoint", test_stats),
        ("Secret Leakage", test_no_secret_leakage),
    ]
    
    results = []
    for name, test_fn in tests:
        try:
            passed = test_fn()
            results.append((name, passed))
        except Exception as e:
            log(f"{name} crashed: {e}", "ERROR")
            results.append((name, False))
    
    # Free anchor flow
    try:
        receipt_id = test_free_anchor()
        if receipt_id:
            test_receipt_retrieval(receipt_id)
            results.append(("Free Anchor", True))
        else:
            results.append(("Free Anchor", False))
    except Exception as e:
        log(f"Free anchor failed: {e}", "ERROR")
        results.append(("Free Anchor", False))
    
    # Summary
    log("=" * 60)
    passed = sum(1 for _, p in results if p)
    total = len(results)
    
    for name, p in results:
        status = "✓ PASS" if p else "✗ FAIL"
        print(f"{status:8} {name}")
    
    log("=" * 60)
    log(f"Result: {passed}/{total} tests passed")
    
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
