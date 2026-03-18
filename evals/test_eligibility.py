"""Deterministic eligibility eval suite.

Computes expected Medicaid eligibility from FPL tables and state thresholds,
then runs each seed patient through the agent and verifies the determination.

No LLM calls needed for the rule computation — only for the agent run.
Run with: python evals/test_eligibility.py
"""

import asyncio
import sys
import os
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eligibility import compute_eligibility, parse_determination

# ---------------------------------------------------------------------------
# Seed patients (mirrors seed_db.py)
# ---------------------------------------------------------------------------
PATIENTS = [
    {
        "id": 1, "first_name": "Maria", "last_name": "Garcia",
        "age": 28, "state": "CA", "household_size": 3,
        "annual_income": 18000.0, "is_pregnant": True,
        "has_disability": False, "is_us_citizen": True,
    },
    {
        "id": 2, "first_name": "James", "last_name": "Wilson",
        "age": 45, "state": "TX", "household_size": 1,
        "annual_income": 14000.0, "is_pregnant": False,
        "has_disability": False, "is_us_citizen": True,
    },
    {
        "id": 3, "first_name": "Sarah", "last_name": "Johnson",
        "age": 7, "state": "FL", "household_size": 4,
        "annual_income": 35000.0, "is_pregnant": False,
        "has_disability": False, "is_us_citizen": True,
    },
    {
        "id": 4, "first_name": "Robert", "last_name": "Chen",
        "age": 70, "state": "NY", "household_size": 2,
        "annual_income": 22000.0, "is_pregnant": False,
        "has_disability": False, "is_us_citizen": True,
    },
    {
        "id": 5, "first_name": "Aisha", "last_name": "Patel",
        "age": 32, "state": "OH", "household_size": 5,
        "annual_income": 42000.0, "is_pregnant": False,
        "has_disability": False, "is_us_citizen": True,
    },
    {
        "id": 6, "first_name": "David", "last_name": "Thompson",
        "age": 55, "state": "GA", "household_size": 1,
        "annual_income": 8000.0, "is_pregnant": False,
        "has_disability": True, "is_us_citizen": True,
    },
    {
        "id": 7, "first_name": "Lisa", "last_name": "Martinez",
        "age": 19, "state": "WA", "household_size": 2,
        "annual_income": 25000.0, "is_pregnant": False,
        "has_disability": False, "is_us_citizen": True,
    },
    {
        "id": 8, "first_name": "Michael", "last_name": "Brown",
        "age": 40, "state": "AL", "household_size": 3,
        "annual_income": 12000.0, "is_pregnant": False,
        "has_disability": False, "is_us_citizen": True,
    },
    # --- Border / edge cases ---
    {   # Income exactly at threshold (CA adult, 138% of $15,650 = $21,597)
        "id": 9, "first_name": "Elena", "last_name": "Ruiz",
        "age": 36, "state": "CA", "household_size": 1,
        "annual_income": 21597.0, "is_pregnant": False,
        "has_disability": False, "is_us_citizen": True,
    },
    {   # Income $1 over threshold (OH adult, 138% of $15,650 = $21,597 → $21,598)
        "id": 10, "first_name": "Kevin", "last_name": "Park",
        "age": 38, "state": "OH", "household_size": 1,
        "annual_income": 21598.0, "is_pregnant": False,
        "has_disability": False, "is_us_citizen": True,
    },
    {   # Non-US citizen — should be ineligible regardless of income
        "id": 11, "first_name": "Yuki", "last_name": "Tanaka",
        "age": 31, "state": "NY", "household_size": 2,
        "annual_income": 10000.0, "is_pregnant": False,
        "has_disability": False, "is_us_citizen": False,
    },
    {   # Age 18 — child→adult boundary (loses higher child threshold)
        "id": 12, "first_name": "Jordan", "last_name": "Lee",
        "age": 18, "state": "FL", "household_size": 3,
        "annual_income": 20000.0, "is_pregnant": False,
        "has_disability": False, "is_us_citizen": True,
    },
    {   # Age 65 — adult→elderly boundary in non-expansion state
        "id": 13, "first_name": "Margaret", "last_name": "Davis",
        "age": 65, "state": "TX", "household_size": 1,
        "annual_income": 2000.0, "is_pregnant": False,
        "has_disability": False, "is_us_citizen": True,
    },
    {   # Pregnant in non-expansion state (higher pregnant threshold applies)
        "id": 14, "first_name": "Tamika", "last_name": "Williams",
        "age": 27, "state": "GA", "household_size": 2,
        "annual_income": 40000.0, "is_pregnant": True,
        "has_disability": False, "is_us_citizen": True,
    },
    {   # Alaska — different FPL table ($19,560 for HH=1)
        "id": 15, "first_name": "John", "last_name": "Whitehorse",
        "age": 41, "state": "AK", "household_size": 1,
        "annual_income": 26000.0, "is_pregnant": False,
        "has_disability": False, "is_us_citizen": True,
    },
    {   # Hawaii, household size 8 — FPL table boundary
        "id": 16, "first_name": "Leilani", "last_name": "Kealoha",
        "age": 34, "state": "HI", "household_size": 8,
        "annual_income": 85000.0, "is_pregnant": False,
        "has_disability": False, "is_us_citizen": True,
    },
]


def compute_expected(patient: dict) -> dict:
    """Compute expected eligibility using the shared deterministic engine."""
    return compute_eligibility(patient)


# ---------------------------------------------------------------------------
# Deterministic-only eval (no LLM needed)
# ---------------------------------------------------------------------------
def run_deterministic_evals():
    """Run pure rule-based evals — no OpenAI calls, instant results."""
    print("\n=== Deterministic Eligibility Evals ===\n")
    passed = 0
    failed = 0
    results = []

    for patient in PATIENTS:
        expected = compute_expected(patient)
        name = f"{patient['first_name']} {patient['last_name']}"
        pid = patient["id"]

        status = "ELIGIBLE" if expected["eligible"] else "NOT ELIGIBLE"
        detail = (
            f"#{pid} {name}: {status} "
            f"({expected['category']}, {patient['state']}, "
            f"${patient['annual_income']:,.0f}, "
            f"{expected['income_pct']}% FPL, "
            f"threshold {expected['threshold_pct']}%)"
        )

        results.append({
            "patient_id": pid,
            "name": name,
            "expected_eligible": expected["eligible"],
            "ambiguous": expected["ambiguous"],
            "category": expected["category"],
            "income_pct_fpl": expected["income_pct"],
            "threshold_pct": expected["threshold_pct"],
        })

        print(f"  {'✓' if expected['eligible'] is not None else '?'} {detail}")
        if expected["eligible"] is not None:
            passed += 1
        else:
            failed += 1

    print(f"\n  Computed: {passed} determinations, {failed} ambiguous")
    print(f"\n  Expected outcomes:")
    for r in results:
        e = "ELIGIBLE" if r["expected_eligible"] else "NOT ELIGIBLE"
        print(f"    Patient #{r['patient_id']} ({r['name']}): {e} [{r['category']}]")

    return results


# ---------------------------------------------------------------------------
# Response quality: required keywords per patient
# ---------------------------------------------------------------------------
# Each keyword list uses alternatives (any match counts).
# Format: list of (keyword_group, [alt1, alt2, ...]) — at least one alt must appear.
REQUIRED_KEYWORDS = {
    1: {"keyword_groups": [["pregnant", "pregnancy"], ["213", "213%"]], "state_alts": ["CA", "california"]},
    2: {"keyword_groups": [["non-expansion", "not expanded", "has not expanded"], ["14%", "14 %", "14 percent"]], "state_alts": ["TX", "texas"]},
    3: {"keyword_groups": [["child", "children", "minor", "under 19"]], "state_alts": ["FL", "florida"]},
    4: {"keyword_groups": [["elderly", "aged", "senior", "65", "over 64"]], "state_alts": ["NY", "new york"]},
    5: {"keyword_groups": [["adult"], ["138%", "138 %", "138 percent"]], "state_alts": ["OH", "ohio"]},
    6: {"keyword_groups": [["disab", "disability", "disabled", "ssi"]], "state_alts": ["GA", "georgia"]},
    7: {"keyword_groups": [["adult"], ["138%", "138 %", "138 percent"]], "state_alts": ["WA", "washington"]},
    8: {"keyword_groups": [["non-expansion", "not expanded", "has not expanded"], ["18%", "18 %", "18 percent"]], "state_alts": ["AL", "alabama"]},
    9: {"keyword_groups": [["adult"], ["138%", "138 %", "138 percent"]], "state_alts": ["CA", "california"]},
    10: {"keyword_groups": [["adult"], ["138%", "138 %", "138 percent"]], "state_alts": ["OH", "ohio"]},
    11: {"keyword_groups": [["citizen", "citizenship", "immigration", "non-citizen"]], "state_alts": ["NY", "new york"]},
    12: {"keyword_groups": [["adult", "18"]], "state_alts": ["FL", "florida"]},
    13: {"keyword_groups": [["elderly", "aged", "senior", "65"]], "state_alts": ["TX", "texas"]},
    14: {"keyword_groups": [["pregnant", "pregnancy"], ["220", "220%"]], "state_alts": ["GA", "georgia"]},
    15: {"keyword_groups": [["adult", "alaska"], ["138%", "138 %", "138 percent"]], "state_alts": ["AK", "alaska"]},
    16: {"keyword_groups": [["adult"], ["138%", "138 %", "138 percent"]], "state_alts": ["HI", "hawaii"]},
}

MAX_API_CALLS = 4  # 1 initial + 1 tool execution + 1 final response + 1 QA review
BANNED_TOOLS = ["fetch"]


def check_response_quality(pid: int, response: str) -> list[str]:
    """Check agent response contains required keywords. Returns list of issues."""
    issues = []
    reqs = REQUIRED_KEYWORDS.get(pid)
    if not reqs:
        return issues
    text_lower = response.lower()

    # Check keyword groups — at least one alternative in each group must appear
    for group in reqs["keyword_groups"]:
        if not any(alt.lower() in text_lower for alt in group):
            issues.append(f"missing one of {group}")

    # Check state — accept any alternative
    if not any(alt.lower() in text_lower for alt in reqs["state_alts"]):
        issues.append(f"missing state {reqs['state_alts']}")
    return issues


def check_tool_efficiency(metrics: dict) -> list[str]:
    """Check API call count and banned tools. Returns list of issues."""
    issues = []
    api_calls = metrics.get("llm_api_calls", metrics.get("api_calls", 0))
    tool_names = metrics.get("tool_names", [])

    if api_calls > MAX_API_CALLS:
        issues.append(f"too many API calls: {api_calls} (max {MAX_API_CALLS})")

    for banned in BANNED_TOOLS:
        if banned in tool_names:
            issues.append(f"used banned tool '{banned}'")

    return issues


# ---------------------------------------------------------------------------
# Agent eval (requires running server with DB)
# ---------------------------------------------------------------------------
async def run_agent_evals(base_url: str = "http://localhost:8000"):
    """Run agent evals against a live server. Requires OpenAI API calls.

    Checks three things per patient:
      1. Correctness: ELIGIBLE/NOT ELIGIBLE matches expected
      2. Efficiency: ≤3 API calls, no fetch tool used
      3. Quality: response contains required keywords (category, state, threshold)
    """
    import httpx

    deterministic = run_deterministic_evals()

    print("\n=== Agent Evals (correctness + efficiency + quality) ===\n")
    correctness_pass = 0
    correctness_fail = 0
    efficiency_pass = 0
    efficiency_fail = 0
    quality_pass = 0
    quality_fail = 0
    all_issues = []

    async with httpx.AsyncClient(timeout=120) as client:
        for expected in deterministic:
            pid = expected["patient_id"]
            name = expected["name"]
            session_id = f"eval-{pid}-{int(time.time())}"
            patient_issues = []

            try:
                resp = await client.post(
                    f"{base_url}/check",
                    json={
                        "query": f"Check Medicaid eligibility for patient ID {pid}",
                        "session_id": session_id,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                agent_text = data.get("determination", "")
                metrics = data.get("metrics", {})

                # --- Eval 1: Correctness ---
                agent_eligible = parse_determination(agent_text)
                if expected.get("ambiguous"):
                    # Ambiguous cases (e.g. disabled in non-expansion state) — accept either answer
                    correctness_pass += 1
                    c_icon = "~"
                elif agent_eligible == expected["expected_eligible"]:
                    correctness_pass += 1
                    c_icon = "✓"
                elif agent_eligible is None:
                    correctness_fail += 1
                    c_icon = "?"
                    patient_issues.append("could not parse determination")
                else:
                    correctness_fail += 1
                    c_icon = "✗"
                    patient_issues.append(f"wrong: agent={agent_eligible}, expected={expected['expected_eligible']}")

                # --- Eval 2: Efficiency ---
                eff_issues = check_tool_efficiency(metrics)
                if eff_issues:
                    efficiency_fail += 1
                    e_icon = "✗"
                    patient_issues.extend(eff_issues)
                else:
                    efficiency_pass += 1
                    e_icon = "✓"

                # --- Eval 3: Response Quality ---
                qual_issues = check_response_quality(pid, agent_text)
                if qual_issues:
                    quality_fail += 1
                    q_icon = "✗"
                    patient_issues.extend(qual_issues)
                else:
                    quality_pass += 1
                    q_icon = "✓"

                llm_calls = metrics.get("llm_api_calls", metrics.get("api_calls", "?"))
                tool_count = metrics.get("tool_call_count", len(metrics.get("tool_names", [])))
                tools = ", ".join(metrics.get("tool_names", []))
                print(f"  [{c_icon}{e_icon}{q_icon}] #{pid} {name} | llm_calls={llm_calls} tool_calls={tool_count} tools=[{tools}]")
                if patient_issues:
                    for issue in patient_issues:
                        print(f"       ↳ {issue}")

            except Exception as e:
                print(f"  [✗✗✗] #{pid} {name}: ERROR {e}")
                correctness_fail += 1
                efficiency_fail += 1
                quality_fail += 1
                patient_issues.append(str(e))

            if patient_issues:
                all_issues.append({"patient_id": pid, "name": name, "issues": patient_issues})

    total = len(deterministic)
    print(f"\n  === Summary ===")
    print(f"  Correctness: {correctness_pass}/{total} passed")
    print(f"  Efficiency:  {efficiency_pass}/{total} passed (max {MAX_API_CALLS} calls, no {BANNED_TOOLS})")
    print(f"  Quality:     {quality_pass}/{total} passed (required keywords present)")

    total_fail = correctness_fail + efficiency_fail + quality_fail
    if all_issues:
        print(f"\n  Issues:")
        for item in all_issues:
            print(f"    #{item['patient_id']} {item['name']}:")
            for issue in item["issues"]:
                print(f"      - {issue}")

    return correctness_pass, total_fail, all_issues


if __name__ == "__main__":
    if "--agent" in sys.argv:
        # Full agent eval (requires running server + OpenAI API)
        try:
            import httpx
        except ImportError:
            print("Install httpx: pip install httpx")
            sys.exit(1)
        passed, failed, _ = asyncio.run(run_agent_evals())
        sys.exit(1 if failed > 0 else 0)
    else:
        # Deterministic only (no API calls, instant)
        run_deterministic_evals()
