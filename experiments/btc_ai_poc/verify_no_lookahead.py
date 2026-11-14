#!/usr/bin/env python3
"""防未来函数自检：
1) 截断不变性：用前 k 根数据算出的特征/信号，必须与用全量数据算出的前 k 根完全一致；
2) 标签隔离：Triple-Barrier 的出场位置必须严格晚于入场位置，且不超过 horizon；
3) 阳性对照：故意把未来收益塞进特征，walk-forward AUC 必须暴涨（证明管线能检测到泄漏）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_experiment import Config, atr, build_features, load_data, primary_signal, triple_barrier, walk_forward  # noqa: E402


def main() -> int:
    cfg = Config(timeframe="4h")
    df = load_data(cfg)
    full_f, full_s = build_features(df), primary_signal(df)
    ok = True
    for k in (3000, 9000, 15000, len(df) - 50):
        part = df.iloc[:k].copy()
        pf, ps = build_features(part), primary_signal(part)
        diff = (pf - full_f.iloc[:k]).abs().max().max()
        same_sig = bool((ps == full_s.iloc[:k]).all())
        print(f"truncate k={k:6d}: max|feature diff|={diff:.2e}  signal identical={same_sig}")
        ok &= diff < 1e-9 and same_sig

    tb = triple_barrier(df, atr(df), cfg)
    v = tb[tb.exit_idx >= 0]
    gap = v.exit_idx.to_numpy() - v.index.to_numpy()
    print(f"triple-barrier: min exit gap={gap.min()} max={gap.max()} (horizon={cfg.horizon})")
    ok &= gap.min() >= 1 and gap.max() <= cfg.horizon

    # 阳性对照：把「未来 6 根收益」作为特征泄漏进去
    X = full_f.copy()
    X["LEAK_fwd_ret_6"] = np.log(df["close"].shift(-6) / df["close"])
    rng = np.random.default_rng(0)
    proba, folds, _ = walk_forward(X, tb["label"], tb["exit_idx"].to_numpy(), X.notna().all(axis=1).to_numpy(), df["ts"], cfg, rng)
    auc = pd.DataFrame(folds).auc.mean()
    print(f"positive control (leaked future return): mean fold AUC = {auc:.3f}  (honest pipeline ≈ 0.52)")
    ok &= auc > 0.75
    print("ALL CHECKS PASSED" if ok else "CHECK FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
