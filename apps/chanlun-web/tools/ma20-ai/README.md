# MA20 AI preference pilot

This is a local, BTC-1D-only preference-screening experiment, not a profitability model. The first pilot failed its recall gate and is advisory. Default review hides model scores until requested.

The feature extractor `../../js/13-ma20-ai-engine.js` is shared by training and browser inference. There are 26 causal price/structure/volume/momentum features. Annotation reasons, hand-drawn ranges and future outcomes are not predictor inputs.

## Reproduce / update

From repository root, with Python numpy and scikit-learn plus Node available:

```sh
python3 apps/chanlun-web/tools/ma20-ai/train.py --annotations '/Users/zhangzheyu/Downloads/annotations-BTC-1D.json'
python3 apps/chanlun-web/tools/ma20-ai/build_report.py
```

After new feedback is exported from the chart:

```sh
python3 apps/chanlun-web/tools/ma20-ai/train.py --feedback '/absolute/path/to/new-export.json'
python3 apps/chanlun-web/tools/ma20-ai/build_report.py
```

Feedback must be BTC/1D. AI reviews take priority over older V3 reviews; a contradiction with a confirmed manual positive remains unknown for explicit resolution. Skip/null reviews and archived/draft manual records never inherit a legacy negative. Unknown bars are not synthesized as negative labels. Legacy unconflicted negatives are weighted 0.35. New explicit AI/V3 reviews have full weight.

Only two fixed baseline configurations are tried: regularized logistic regression and depth-2 gradient boosting. Imputation/scaling fit on training only. The highest validation threshold retaining at least90% of positives is used; the validation precision selects the model. No test tuning or return optimization. Fits stop at2021-06-30, validation2022-01-01–2023-06-30, checks from2024-01-01. Roughly180-day boundary gaps separate nearby chart windows. The check remains retrospective and relies mainly on provisional old negative labels.

Generated files:

- `js/13-ma20-ai-model.js`: frozen coefficients/trees, preprocessing, threshold, reference provenance and report.
- `docs/ma20-ai-2026-11-04.html`: standalone reviewable report.
- `reports/ma20-ai-2026-11-04/`: report, audit, full Python scoring parity fixture, immutable model snapshots indexed by content fingerprint.

The model ID includes annotation, feedback, market, feature code and training code fingerprints. Retraining is explicit; there is no hidden network upload or automatic browser training. Human labels retain the same `ma20_ai_v1` source across model versions; each marked entry exports the exact model version, score and feature snapshot at judgment time.

## Verify

After training:

```sh
python3 apps/chanlun-web/tools/ma20-ai/test_labels.py
node apps/chanlun-web/tools/ma20-ai/verify.cjs
node --test apps/chanlun-web/tests/manual-storage.test.mjs apps/chanlun-web/tests/manual-evidence.test.mjs
```

Browser integration uses an isolated QA profile and local HTTP origin (defaults8878, CDP9349):

```sh
CDP_PORT=9349 COVER67_TEST_URL=http://127.0.0.1:8878 node apps/chanlun-web/tools/ma20-ai/verify_browser.mjs
```

It backs up/restores annotation storage, checks canonical-source separation, save/export/import, chart-picked anchors, blind markers, reviewed-item exclusion, complete V3 candidate access, and BTC-only scope. Do not target a user's regular browser profile.

## V2: corrected labels, structure ablation, group-blind check

V2 uses the original file plus the first completed20 feedback export. It does not overwrite the V1 artifact.

```sh
python3 apps/chanlun-web/tools/ma20-ai/train_v2.py --feedback '/Users/zhangzheyu/Downloads/annotations-BTC-1D (1).json'
python3 apps/chanlun-web/tools/ma20-ai/build_report_v2.py
python3 apps/chanlun-web/tools/ma20-ai/test_v2.py
node apps/chanlun-web/tools/ma20-ai/verify_v2.cjs
node apps/chanlun-web/tools/ma20-ai/verify_browser_v2.mjs
```

There are three fixed experiments: original labels /26 features, updated labels /26 features, updated labels /33 features. Both small learner configurations remain fixed. Seven additional causal context values come from historical extrema and pre-signal TR, not the manual ranges. The added features did not improve development forward checks, so the browser's selected scoring model still uses26 features through explicit projection. It displays the seven context values as research diagnostics, plus optional historical reference markers.

Labels/candidates linked by gaps<=21calendar days form an episode. Whole episodes go into forward fit/validation/check. Blind groups are removed before all experiments. New blind candidates have>=120 bars context. Reservation prefers episodes without strong labels, with fallback to additional episodes containing old manual positives when needed; no episode from the first20 feedback is reserved. All labels of any reserved episode are excluded, including old weak labels.

Final scores are refit on non-reserved development labels. The deployment threshold is the median of validation-only thresholds; its alignment with final refit scores is not assumed proven. There is no automatic filtering or promotion.

The default UI is `ma20_ai_v2`, with a frozen20 blind-review queue. Scores, contribution text, colors and high/low filtering are locked for an unjudged reserved point. First decisions and model/score evidence are saved separately from subsequent revisions. A first `skip` stays unknown for the blind evaluation even if later revised, while latest explicit decisions can be considered separately for future development.

After receiving the V2 export, **evaluate before any retraining**:

```sh
python3 apps/chanlun-web/tools/ma20-ai/evaluate_blind.py --feedback '/path/to/v2-export.json'
```

The evaluator uses first blind decisions and frozen predictions, flags model/score mismatches and never trains or promotes a model. `train_v2.py` refuses V2 blind decisions to prevent accidentally training on its own check answers. A future training generation must be explicit. Immutable snapshots under `reports/ma20-ai-v2-2026-11-04/snapshots/<model-version>/` retain the model, parity fixture, inputs and labels. Use `--snapshot` with the evaluator if checking an older generation.

The optional annotation form can now capture an advance's start/end candles via chart clicks. Derived price/time ratios are exported as annotation evidence; they are not automatically fed into live predictors. Importing V1 labels is safe and does not synthesize V2 judgments.
