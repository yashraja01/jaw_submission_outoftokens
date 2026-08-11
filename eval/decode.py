"""Read a split-probe result off the leaderboard and print what survives.

The probe's hypothesis table holds, for every (computation, direction-of-error) pair, the score
band that pair would produce. Decoding is an intersection: keep the hypotheses whose band
overlaps the reported score's own rounding interval.

A reported score matching *nothing* is informative rather than a failure -- it means the loss is
not a single wrong computation, and the surviving deviation says how far off that assumption is.

    python eval/decode.py --score 65.052
    python eval/decode.py --score 65.052 --out build/surviving.json
"""
import argparse
import json
import pathlib
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent.parent
N = 333


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--score", type=float, required=True, help="score the probe actually got")
    ap.add_argument("--hyp", default="build/hypotheses.json")
    ap.add_argument("--out", default="build/surviving.json")
    a = ap.parse_args()

    H = json.load(open(ROOT / a.hyp))
    lo, hi = a.score - 0.0005, a.score + 0.0005
    alive = [h for h in H["hypotheses"].values() if h["score_hi"] >= lo and h["score_lo"] <= hi]

    print(f"probe {H['probe']} reported {a.score}  (true score in [{lo}, {hi}])\n")
    if not alive:
        near = min(H["hypotheses"].values(),
                   key=lambda h: min(abs(h["score_lo"] - a.score), abs(h["score_hi"] - a.score)))
        print("NO single-computation hypothesis fits.")
        print(f"  closest band {near['score_lo']}-{near['score_hi']} ({near['group']})")
        print("  => the residual loss is spread over more than one computation.")
        print("     Re-run split_probe.py; the same table decodes pairs, it just needs a round")
        print("     that separates them.")
        return 1

    groups = sorted({h["group"] for h in alive})
    signs = sorted({h["sign"] for h in alive})
    print(f"{len(alive)} hypotheses survive, spanning {len(groups)} computations"
          f"  direction={'overshot' if signs == [1] else 'undershot' if signs == [-1] else 'either'}")

    # Each surviving hypothesis implies an L. The deviation is measured against the score the
    # probe would have returned if every candidate were already exact -- not against the
    # baseline, which the perturbation has deliberately moved away from.
    Ls = []
    dev = N * (H["clean_score"] - a.score) / 100
    for h in alive:
        d, c = h["level"]
        f = (1 + c) if d == "up" else (1 - c)
        Ls.append(abs(dev) / f)
    print(f"implied residual loss L in [{min(Ls):.6f}, {max(Ls):.6f}]"
          f"   (was [{H['loss_interval'][0]:.6f}, {H['loss_interval'][1]:.6f}])\n")

    for h in sorted(alive, key=lambda h: h["group"]):
        shape, client, val = h["group"].split("|")
        print(f"  {'OVER ' if h['sign'] > 0 else 'UNDER'}  {shape:20s} n={h['n']}  "
              f"{client[:38]:38s} {float(val):>16,.2f}")

    json.dump(groups, open(ROOT / a.out, "w"), indent=1)
    print(f"\nwrote {ROOT / a.out}  ({len(groups)} computations)")
    print(f"next: python eval/split_probe.py --score {H['baseline_score']} "
          f"--restrict {a.out} --out build/probeH.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
