#!/usr/bin/env python3
"""
Temper Benchmark Grader
Compares Claude Code responses with and without Temper.
Produces a detailed Markdown report.
"""

import json
import os
import argparse
from pathlib import Path
from datetime import datetime


def load_test_cases(path):
    with open(path) as f:
        return json.load(f)


def load_response(directory, test_id):
    txt_path = os.path.join(directory, f"{test_id}.txt")
    json_path = os.path.join(directory, f"{test_id}.json")
    raw_path = os.path.join(directory, f"{test_id}.raw")

    text = ""
    if os.path.exists(txt_path):
        text = open(txt_path).read()
    elif os.path.exists(raw_path):
        text = open(raw_path).read()

    metadata = {}
    if os.path.exists(json_path):
        try:
            metadata = json.load(open(json_path))
        except:
            pass

    return text, metadata


def grade_keyword_coverage(response, test_case):
    """Grade based on how many expected keywords appear in the response."""
    keywords = test_case.get("expected_keywords", [])
    if not keywords:
        return None, "No keywords to check"

    response_lower = response.lower()
    found = []
    missing = []

    for kw in keywords:
        if kw.lower() in response_lower:
            found.append(kw)
        else:
            missing.append(kw)

    score = len(found) / len(keywords) if keywords else 0

    # Check expected files
    files = test_case.get("expected_files_mentioned", [])
    files_found = [f for f in files if f.lower() in response_lower]
    files_missing = [f for f in files if f.lower() not in response_lower]

    # Check anti-patterns
    anti = test_case.get("anti_patterns", [])
    anti_found = [a for a in anti if a.lower() in response_lower]

    detail = f"Keywords: {len(found)}/{len(keywords)}"
    if files:
        detail += f", Files: {len(files_found)}/{len(files)}"
    if anti_found:
        detail += f", Anti-patterns found: {anti_found}"
        score *= 0.5  # penalty

    if missing:
        detail += f"\n    Missing: {', '.join(missing)}"
    if files_missing:
        detail += f"\n    Files missing: {', '.join(files_missing)}"

    return score, detail


def grade_constraint_check(response, test_case):
    """Grade whether Claude refused/warned about a constraint violation."""
    response_lower = response.lower()

    # Signs that Claude is aware of the constraint
    awareness_signals = [
        "should not", "must not", "don't", "do not", "cannot", "shouldn't",
        "not recommended", "avoid", "warning", "constraint", "risk",
        "issue", "problem", "careful", "caution", "instead",
        "bypass", "violation", "concern",
        # Chinese signals
        "不应该", "不能", "不要", "约束", "风险", "问题", "注意",
    ]

    # Signs that Claude just did it without questioning
    compliance_signals = [
        "here's the code", "here is the", "i've made the change",
        "updated the file", "here's the implementation",
    ]

    aware = any(s in response_lower for s in awareness_signals)
    just_did_it = any(s in response_lower for s in compliance_signals) and not aware

    constraint = test_case.get("constraint_violated", "")
    # Check if the specific constraint is mentioned
    constraint_keywords = [w.lower() for w in constraint.split() if len(w) > 4]
    constraint_mentioned = sum(1 for kw in constraint_keywords if kw in response_lower)
    constraint_score = constraint_mentioned / len(constraint_keywords) if constraint_keywords else 0

    if aware and constraint_score > 0.3:
        score = 1.0
        detail = "Correctly warned about constraint"
    elif aware:
        score = 0.7
        detail = "Showed awareness but didn't cite specific constraint"
    elif just_did_it:
        score = 0.0
        detail = "Made the change without warning about constraint"
    else:
        score = 0.3
        detail = "Unclear response"

    if constraint:
        detail += f"\n    Expected constraint: {constraint[:80]}"

    return score, detail


def grade_impact_completeness(response, test_case):
    """Grade whether all impacted files were identified."""
    expected = test_case.get("expected_files", [])
    if not expected:
        return None, "No expected files"

    response_lower = response.lower()
    found = [f for f in expected if f.lower() in response_lower]
    missing = [f for f in expected if f.lower() not in response_lower]

    score = len(found) / len(expected)

    critical_missing = test_case.get("unexpected_files_missed", [])
    critical_missed = [f for f in critical_missing if f.lower() not in response_lower]

    detail = f"Files identified: {len(found)}/{len(expected)}"
    if missing:
        detail += f"\n    Missing: {', '.join(missing)}"
    if critical_missed:
        detail += f"\n    CRITICAL missed: {', '.join(critical_missed)}"
        score *= 0.5

    return score, detail


def grade_test(test_case, response):
    """Grade a single test case response."""
    grading = test_case.get("grading", "keyword_coverage")

    if grading == "keyword_coverage":
        return grade_keyword_coverage(response, test_case)
    elif grading == "constraint_check":
        return grade_constraint_check(response, test_case)
    elif grading == "impact_completeness":
        return grade_impact_completeness(response, test_case)
    else:
        return None, f"Unknown grading method: {grading}"


def generate_report(test_cases, without_dir, with_dir):
    """Generate the full comparison report."""
    lines = []
    lines.append("# Temper Benchmark Report")
    lines.append(f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    results = []

    for tc in test_cases:
        tid = tc["id"]
        category = tc["category"]
        difficulty = tc.get("difficulty", "?")

        resp_without, meta_without = load_response(without_dir, tid)
        resp_with, meta_with = load_response(with_dir, tid)

        score_without, detail_without = grade_test(tc, resp_without)
        score_with, detail_with = grade_test(tc, resp_with)

        dur_without = meta_without.get("duration_ms", 0)
        dur_with = meta_with.get("duration_ms", 0)

        results.append({
            "id": tid,
            "category": category,
            "difficulty": difficulty,
            "score_without": score_without,
            "score_with": score_with,
            "detail_without": detail_without,
            "detail_with": detail_with,
            "duration_without": dur_without,
            "duration_with": dur_with,
            "response_len_without": len(resp_without),
            "response_len_with": len(resp_with),
            "meta_without": meta_without,
            "meta_with": meta_with,
        })

    # --- Summary table ---
    lines.append("## Summary\n")
    lines.append("| Test | Category | Difficulty | Without Temper | With Temper | Delta |")
    lines.append("|------|----------|-----------|---------------|------------|-------|")

    total_without = 0
    total_with = 0
    count = 0

    for r in results:
        sw = r["score_without"]
        sa = r["score_with"]
        if sw is not None and sa is not None:
            delta = sa - sw
            delta_str = f"+{delta:.0%}" if delta >= 0 else f"{delta:.0%}"
            delta_emoji = "🟢" if delta > 0 else ("🔴" if delta < 0 else "⚪")
            lines.append(
                f"| {r['id']} | {r['category']} | {r['difficulty']} | "
                f"{sw:.0%} | {sa:.0%} | {delta_emoji} {delta_str} |"
            )
            total_without += sw
            total_with += sa
            count += 1

    if count > 0:
        avg_without = total_without / count
        avg_with = total_with / count
        avg_delta = avg_with - avg_without
        lines.append(
            f"| **Average** | | | **{avg_without:.0%}** | **{avg_with:.0%}** | "
            f"**{'+' if avg_delta >= 0 else ''}{avg_delta:.0%}** |"
        )

    # --- Timing & cost comparison ---
    lines.append("\n## Performance Metrics\n")
    lines.append("| Test | Mode | Duration (ms) | API (ms) | Turns | Input Tok | Output Tok | Cost ($) |")
    lines.append("|------|------|--------------|---------|-------|-----------|-----------|----------|")
    for r in results:
        mw = r.get("meta_without", {})
        ma = r.get("meta_with", {})
        if mw:
            lines.append(
                f"| {r['id']} | without | {mw.get('duration_ms','-')} | {mw.get('duration_api_ms','-')} | "
                f"{mw.get('num_turns','-')} | {mw.get('input_tokens','-')} | {mw.get('output_tokens','-')} | "
                f"{mw.get('total_cost_usd',0):.4f} |"
            )
        if ma:
            lines.append(
                f"| {r['id']} | **with** | **{ma.get('duration_ms','-')}** | **{ma.get('duration_api_ms','-')}** | "
                f"**{ma.get('num_turns','-')}** | **{ma.get('input_tokens','-')}** | **{ma.get('output_tokens','-')}** | "
                f"**{ma.get('total_cost_usd',0):.4f}** |"
            )

    # --- Totals ---
    total_cost_without = sum(r.get("meta_without", {}).get("total_cost_usd", 0) for r in results)
    total_cost_with = sum(r.get("meta_with", {}).get("total_cost_usd", 0) for r in results)
    total_turns_without = sum(r.get("meta_without", {}).get("num_turns", 0) for r in results)
    total_turns_with = sum(r.get("meta_with", {}).get("num_turns", 0) for r in results)
    lines.append(f"\n**Total cost:** without=${total_cost_without:.4f}, with=${total_cost_with:.4f}, "
                 f"delta=${total_cost_with - total_cost_without:+.4f}")
    lines.append(f"**Total turns:** without={total_turns_without}, with={total_turns_with}, "
                 f"delta={total_turns_with - total_turns_without:+d}")

    # --- Detail per test ---
    lines.append("\n## Detailed Results\n")

    for r in results:
        lines.append(f"### {r['id']} — {r['category']} ({r['difficulty']})\n")

        # Load the actual prompts
        tc = next(t for t in test_cases if t["id"] == r["id"])
        lines.append(f"**Prompt:** {tc['prompt']}\n")

        lines.append(f"**Without Temper** (score: {r['score_without']:.0%}, {r['response_len_without']} chars)")
        lines.append(f"  - {r['detail_without']}")

        lines.append(f"\n**With Temper** (score: {r['score_with']:.0%}, {r['response_len_with']} chars)")
        lines.append(f"  - {r['detail_with']}")

        lines.append("")

    # --- Key findings ---
    lines.append("\n## Key Findings\n")

    # Token saving
    constraint_tests = [r for r in results if r["category"] == "Constraint Awareness"]
    constraint_without = [r["score_without"] for r in constraint_tests if r["score_without"] is not None]
    constraint_with = [r["score_with"] for r in constraint_tests if r["score_with"] is not None]
    if constraint_without and constraint_with:
        avg_cw = sum(constraint_without) / len(constraint_without)
        avg_ca = sum(constraint_with) / len(constraint_with)
        lines.append(f"### 1. Constraint Awareness (hidden constraints NOT in code)")
        lines.append(f"- Without Temper: **{avg_cw:.0%}** average")
        lines.append(f"- With Temper: **{avg_ca:.0%}** average")
        lines.append(f"- **Improvement: {avg_ca - avg_cw:+.0%}**")
        if avg_ca > avg_cw:
            lines.append(f"- Temper's memory prevented Claude from making {sum(1 for s in constraint_with if s >= 0.7)}/{len(constraint_with)} constraint violations")
        lines.append("")

    # Token/turn savings
    lines.append(f"### 2. Token & Turn Savings")
    lines.append(f"- Total cost: without=${total_cost_without:.4f}, with=${total_cost_with:.4f}")
    lines.append(f"- Total turns: without={total_turns_without}, with={total_turns_with}")
    if total_turns_without > 0:
        lines.append(f"- Turn reduction: {(total_turns_without - total_turns_with) / total_turns_without:.0%}" if total_turns_without > total_turns_with else "")
    lines.append("")

    # Fast localization
    local_tests = [r for r in results if r["category"] == "Fast Localization"]
    if local_tests:
        lines.append(f"### 3. Fast Problem Localization")
        for r in local_tests:
            lines.append(f"- {r['id']}: without={r['score_without']:.0%}, with={r['score_with']:.0%}")
        lines.append("")

    # Category summary ---
    lines.append("\n## By Category\n")
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"without": [], "with": []}
        if r["score_without"] is not None:
            categories[cat]["without"].append(r["score_without"])
        if r["score_with"] is not None:
            categories[cat]["with"].append(r["score_with"])

    lines.append("| Category | Tests | Avg Without | Avg With | Improvement |")
    lines.append("|----------|-------|------------|---------|-------------|")
    for cat, scores in categories.items():
        n = len(scores["without"])
        avg_w = sum(scores["without"]) / n if n else 0
        avg_a = sum(scores["with"]) / n if n else 0
        imp = avg_a - avg_w
        lines.append(
            f"| {cat} | {n} | {avg_w:.0%} | {avg_a:.0%} | "
            f"{'+' if imp >= 0 else ''}{imp:.0%} |"
        )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Grade Temper benchmark results")
    parser.add_argument("--test-cases", required=True)
    parser.add_argument("--without-temper", required=True)
    parser.add_argument("--with-temper", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    test_cases = load_test_cases(args.test_cases)
    report = generate_report(test_cases, args.without_temper, args.with_temper)

    with open(args.output, "w") as f:
        f.write(report)

    print(f"Report written to {args.output}")


if __name__ == "__main__":
    main()
