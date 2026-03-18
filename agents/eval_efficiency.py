"""Efficiency Eval Agent: API call counting and banned tool detection."""

from agents.base import EvalResult

MAX_API_CALLS_EVAL = 4
BANNED_TOOLS = ["fetch"]


class EfficiencyEval:
    def check(
        self,
        api_calls: int,
        tool_names: list[str],
        max_api_calls: int = MAX_API_CALLS_EVAL,
        banned_tools: list[str] | None = None,
    ) -> EvalResult:
        """Check API call count and banned tools.

        Returns EvalResult with pass/fail and list of issues.
        """
        if banned_tools is None:
            banned_tools = BANNED_TOOLS

        issues = []
        if api_calls > max_api_calls:
            issues.append(f"too many API calls: {api_calls} (max {max_api_calls})")

        for banned in banned_tools:
            if banned in tool_names:
                issues.append(f"used banned tool '{banned}'")

        return EvalResult(
            passed=len(issues) == 0,
            dimension="efficiency",
            details="; ".join(issues) if issues else "OK",
            data={"api_calls": api_calls, "tool_names": tool_names, "issues": issues},
        )
