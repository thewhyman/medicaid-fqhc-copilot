"""Deterministic eligibility eval suite.

Computes expected Medicaid eligibility from FPL tables and state thresholds,
then runs each seed patient through the agent and verifies the determination.

No LLM calls needed for the rule computation — only for the agent run.
Run with: python evals/test_eligibility.py
"""

import asyncio
import json
import re
import sys
import os
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompts import FPL, FPL_ALASKA, FPL_HAWAII, STATE_THRESHOLDS

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
]


def get_fpl(state: str, household_size: int) -> int:
    """Get the FPL amount for a state and household size."""
    if state == "AK":
        return FPL_ALASKA.get(household_size, FPL_ALASKA[8])
    elif state == "HI":
        return FPL_HAWAII.get(household_size, FPL_HAWAII[8])
    else:
        return FPL.get(household_size, FPL[8])


def determine_category(patient: dict) -> str:
    """Determine which Medicaid eligibility category applies."""
    if patient["age"] < 19:
        return "child"
    if patient["is_pregnant"]:
        return "pregnant"
    if patient["has_disability"]:
        return "disabled"
    if patient["age"] >= 65:
        return "elderly"
    return "adult"


def compute_expected(patient: dict) -> dict:
    """Compute expected eligibility using the same rules the agent should use."""
    state = patient["state"]
    hh_size = patient["household_size"]
    income = patient["annual_income"]
    category = determine_category(patient)

    fpl = get_fpl(state, hh_size)
    thresholds = STATE_THRESHOLDS.get(state)
    if not thresholds:
        return {"eligible": None, "category": category, "reason": "state not found"}

    income_pct = (income / fpl) * 100

    # Determine applicable threshold percentage
    if category == "child":
        threshold_pct = thresholds["child_pct"]
    elif category == "pregnant":
        threshold_pct = thresholds["pregnant_pct"]
    elif category == "disabled":
        # Disabled may qualify through SSI pathway — use adult threshold as baseline
        # but note this is ambiguous (SSI has its own criteria)
        threshold_pct = thresholds["adult_pct"]
    elif category == "elderly":
        # Elderly may qualify through aged/blind/disabled category
        threshold_pct = thresholds["adult_pct"]
    else:  # adult
        threshold_pct = thresholds["adult_pct"]

    threshold_amount = fpl * threshold_pct / 100
    eligible = income <= threshold_amount

    return {
        "eligible": eligible,
        "category": category,
        "fpl": fpl,
        "income_pct": round(income_pct, 1),
        "threshold_pct": threshold_pct,
        "threshold_amount": round(threshold_amount, 2),
        "expansion": thresholds["expansion"],
    }


def parse_determination(response: str) -> bool | None:
    """Extract ELIGIBLE or NOT ELIGIBLE from agent response text."""
    text = response.upper()
    # Look for explicit "NOT ELIGIBLE" first (more specific)
    if re.search(r'\bNOT\s+ELIGIBLE\b', text):
        return False
    if re.search(r'\bINELIGIBLE\b', text):
        return False
    if re.search(r'\bELIGIBLE\b', text):
        return True
    return None


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
# Agent eval (requires running server with DB)
# ---------------------------------------------------------------------------
async def run_agent_evals(base_url: str = "http://localhost:8000"):
    """Run agent evals against a live server. Requires OpenAI API calls."""
    import httpx

    deterministic = run_deterministic_evals()

    print("\n=== Agent Eligibility Evals ===\n")
    passed = 0
    failed = 0
    errors = []

    async with httpx.AsyncClient(timeout=120) as client:
        for expected in deterministic:
            pid = expected["patient_id"]
            name = expected["name"]
            session_id = f"eval-{pid}-{int(time.time())}"

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
                agent_eligible = parse_determination(agent_text)

                if agent_eligible == expected["expected_eligible"]:
                    print(f"  ✓ #{pid} {name}: agent={agent_eligible}, expected={expected['expected_eligible']}")
                    passed += 1
                elif agent_eligible is None:
                    print(f"  ? #{pid} {name}: could not parse agent response")
                    errors.append({"patient_id": pid, "error": "unparseable", "response": agent_text[:200]})
                    failed += 1
                else:
                    print(f"  ✗ #{pid} {name}: agent={agent_eligible}, expected={expected['expected_eligible']}")
                    errors.append({"patient_id": pid, "error": "wrong_determination", "agent": agent_eligible, "expected": expected["expected_eligible"]})
                    failed += 1

            except Exception as e:
                print(f"  ✗ #{pid} {name}: ERROR {e}")
                errors.append({"patient_id": pid, "error": str(e)})
                failed += 1

    print(f"\n  Results: {passed} passed, {failed} failed out of {len(deterministic)}")
    if errors:
        print(f"\n  Failures:")
        for err in errors:
            print(f"    #{err['patient_id']}: {err['error']}")

    return passed, failed, errors


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
