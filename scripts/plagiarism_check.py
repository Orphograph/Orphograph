#!/usr/bin/env python3
"""
Plagiarism check: compare Orphograph landing copy against competitors.
Extracts text from web/index.html and compares key phrases against competitor snippets.

Usage: python3 scripts/plagiarism_check.py
"""

import re
import sys
from pathlib import Path

# Competitor landing page snippets (manually collected as baseline)
COMPETITORS = {
    "OpenTimestamps": [
        "The standard for cryptographically verifying information and data",
        "prove that data existed at a particular time",
        "Bitcoin calendars",
        "open source verification",
    ],
    "OriginStamp": [
        "Digital Timestamps",
        "legally compliant timestamping",
        "EIDAS qualified",
        "proof of existence",
    ],
    "WordProof": [
        "Proof of publication",
        "WordPress content",
        "blockchain timestamps",
        "content verification",
    ],
    "Signl": [
        "Web Integrity Proofs",
        "verify website content",
        "blockchain anchored",
        "immutable records",
    ],
}

def extract_text_from_html(html_path):
    """Extract all text content from HTML file (no tags)."""
    with open(html_path, "r") as f:
        html = f.read()

    # Remove script and style tags
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)

    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", html)

    # Clean up whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text

def normalize_phrase(phrase):
    """Normalize phrase for comparison (lowercase, remove punctuation)."""
    phrase = phrase.lower()
    phrase = re.sub(r"[^\w\s]", "", phrase)
    return phrase.strip()

def check_plagiarism(orphograph_text, min_phrase_length=4):
    """
    Check for unattributed paraphrases.
    Returns list of (competitor, matched_snippet, orphograph_snippet).
    """
    results = []

    # Split Orphograph text into sentences/phrases
    orphograph_phrases = [
        s.strip() for s in re.split(r"[.!?]", orphograph_text)
        if len(s.strip()) > 20
    ]

    for competitor, snippets in COMPETITORS.items():
        for competitor_snippet in snippets:
            norm_comp = normalize_phrase(competitor_snippet)
            words_comp = set(norm_comp.split())

            for orpho_phrase in orphograph_phrases:
                norm_orpho = normalize_phrase(orpho_phrase)
                words_orpho = set(norm_orpho.split())

                # Calculate Jaccard similarity
                if words_comp and words_orpho:
                    intersection = len(words_comp & words_orpho)
                    union = len(words_comp | words_orpho)
                    similarity = intersection / union if union > 0 else 0

                    # Flag if >50% word overlap
                    if similarity > 0.5 and intersection >= min_phrase_length:
                        results.append({
                            "competitor": competitor,
                            "competitor_snippet": competitor_snippet,
                            "orphograph_snippet": orpho_phrase[:100],
                            "similarity": similarity,
                        })

    return results

def main():
    orphograph_dir = Path(__file__).parent.parent
    html_path = orphograph_dir / "web" / "index.html"

    if not html_path.exists():
        print(f"Error: {html_path} not found")
        sys.exit(1)

    print(f"Extracting text from {html_path}...")
    orphograph_text = extract_text_from_html(html_path)

    print(f"Running plagiarism check against {len(COMPETITORS)} competitors...")
    matches = check_plagiarism(orphograph_text)

    if not matches:
        print("\n✅ NO PLAGIARISM DETECTED")
        print(f"\nSample of Orphograph copy (first 500 chars):")
        print(f"  {orphograph_text[:500]}...")
        print("\nComparison against:")
        for comp_name in COMPETITORS:
            print(f"  - {comp_name}")
        print("\nVerdict: Copy appears original. No unattributed paraphrases found.")
        return 0
    else:
        print(f"\n⚠️ POTENTIAL MATCHES FOUND ({len(matches)} flags)")
        print("\nReview these carefully:")
        for match in sorted(matches, key=lambda x: x["similarity"], reverse=True):
            print(f"\n  {match['competitor']} (similarity: {match['similarity']:.1%})")
            print(f"    Their snippet: {match['competitor_snippet']}")
            print(f"    Your snippet: {match['orphograph_snippet']}")
        print("\nAction: If genuine overlap, add citation or rewrite.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
