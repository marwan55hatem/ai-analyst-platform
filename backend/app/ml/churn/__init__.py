"""Churn risk: label window and cutoffs (labels), per-customer features (features), models (model).

Leakage rule: every feature is computed from purchases on or before a cutoff date only; the label
looks at the window after it. The same SQL builder serves training, evaluation and scoring.
"""
