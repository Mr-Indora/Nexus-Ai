"""Run the synthetic deterministic extraction evaluation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.extractor import rule_extract
from evaluations.dataset import SCENARIOS


def _binary_metrics(expected: list[bool], actual: list[bool]) -> dict:
    tp = sum(e and a for e, a in zip(expected, actual))
    tn = sum(not e and not a for e, a in zip(expected, actual))
    fp = sum(not e and a for e, a in zip(expected, actual))
    fn = sum(e and not a for e, a in zip(expected, actual))
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    return {
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(2 * precision * recall / (precision + recall), 3)
        if precision + recall else 0,
        "falsePositiveRate": round(fp / (fp + tn), 3) if fp + tn else 0,
    }


def evaluate() -> dict:
    results = []
    for scenario in SCENARIOS:
        extraction = rule_extract(**{
            "subject": scenario["email"]["subject"],
            "body": scenario["email"]["body"],
            "sender": scenario["email"]["sender"],
            "received_at": scenario["email"]["received_at"],
        })
        expected = scenario["expected"]
        results.append({
            "name": scenario["name"],
            "actionableExpected": expected["actionable"],
            "actionableActual": extraction.actionable,
            "deadlineExpected": expected["deadline"],
            "deadlineActual": extraction.deadline is not None,
            "actionableCorrect": extraction.actionable == expected["actionable"],
            "deadlineCorrect": (extraction.deadline is not None) == expected["deadline"],
        })
    return {
        "scenarioCount": len(results),
        "actionable": _binary_metrics(
            [r["actionableExpected"] for r in results],
            [r["actionableActual"] for r in results],
        ),
        "deadline": _binary_metrics(
            [r["deadlineExpected"] for r in results],
            [r["deadlineActual"] for r in results],
        ),
        "exactScenarioMatches": sum(
            r["actionableCorrect"] and r["deadlineCorrect"] for r in results
        ),
        "failures": [r for r in results if not (r["actionableCorrect"] and r["deadlineCorrect"])],
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), indent=2, default=str))
