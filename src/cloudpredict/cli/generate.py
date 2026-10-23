"""``cloudpredict generate``: write the synthetic Zipfian access log."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from cloudpredict.infrastructure.generator import GeneratorParams, generate, write_outputs


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("generate", help="generate a synthetic Zipfian access log")
    parser.add_argument("--out", type=Path, default=Path("data/synthetic"))
    parser.add_argument("--objects", type=int, default=5000)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--alpha", type=float, default=1.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    params = GeneratorParams(
        n_objects=args.objects,
        days=args.days,
        alpha=args.alpha,
        seed=args.seed,
    )
    objects, accesses = generate(params)
    manifest = write_outputs(args.out, objects, accesses, params)
    fraction = float(manifest["positive_fraction"])  # type: ignore[arg-type]
    print(
        f"generated {manifest['objects']} Objects, {manifest['access_events']} access events, "
        f"{fraction:.1%} accessed in the final {params.horizon_days} days -> {args.out}"
    )
    return 0
