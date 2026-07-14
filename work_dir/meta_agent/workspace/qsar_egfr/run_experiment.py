"""EGFR inhibitor binary classification: 5 comparable iterative rounds.

Fixed stratified held-out test set (random_state=42, 20%) kept identical across
all 5 rounds. Same 5 metrics reported every round: ROC-AUC, PR-AUC, balanced
accuracy, F1, MCC.

Round 1: RDKit physicochemical descriptors + logistic regression (baseline)
Round 2: Morgan/ECFP4 fingerprints (2048 bits) + logistic regression
Round 3: Random forest on fingerprints
Round 4: Gradient boosting (HistGradientBoosting) + CV hyperparameter tuning on train only
Round 5: Best model + class imbalance handling + feature augmentation (fp + descriptors)
"""
import json
import os
import warnings

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from rdkit import Chem, RDLogger
from rdkit.Chem import Descriptors
from rdkit.Chem import rdMolDescriptors
from rdkit.DataStructs import ConvertToNumpyArray

from sklearn.model_selection import train_test_split, StratifiedKFold, GridSearchCV
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    roc_auc_score, average_precision_score, balanced_accuracy_score,
    f1_score, matthews_corrcoef, roc_curve, precision_recall_curve,
    confusion_matrix,
)

RDLogger.DisableLog("rdApp.*")
warnings.filterwarnings("ignore")

BASE = "/Users/wentaozhang/workspace/RA/AgentEvolver/work_dir/meta_agent/workspace/qsar_egfr"
DATA = os.path.join(BASE, "data", "egfr_dataset.csv")
RESULTS = os.path.join(BASE, "results")
FIGURES = os.path.join(BASE, "figures")
os.makedirs(RESULTS, exist_ok=True)
os.makedirs(FIGURES, exist_ok=True)
SEED = 42

print("Loading data...")
df = pd.read_csv(DATA)
smiles_col = "canonical_smiles" if "canonical_smiles" in df.columns else "smiles"
df = df.dropna(subset=[smiles_col, "label"]).reset_index(drop=True)

# Parse molecules once
mols = []
valid_idx = []
for i, smi in enumerate(df[smiles_col].values):
    m = Chem.MolFromSmiles(smi)
    if m is not None:
        mols.append(m)
        valid_idx.append(i)
df = df.iloc[valid_idx].reset_index(drop=True)
y = df["label"].astype(int).values
print(f"Valid molecules: {len(mols)}  positives: {int(y.sum())}  negatives: {int((1-y).sum())}")

# ---- Feature builders ----
DESC_FUNCS = {
    "MolWt": Descriptors.MolWt,
    "MolLogP": Descriptors.MolLogP,
    "TPSA": Descriptors.TPSA,
    "NumHDonors": Descriptors.NumHDonors,
    "NumHAcceptors": Descriptors.NumHAcceptors,
    "NumRotatableBonds": Descriptors.NumRotatableBonds,
    "NumAromaticRings": Descriptors.NumAromaticRings,
    "FractionCSP3": Descriptors.FractionCSP3,
    "NumHeavyAtoms": lambda m: m.GetNumHeavyAtoms(),
    "RingCount": Descriptors.RingCount,
}
DESC_NAMES = list(DESC_FUNCS.keys())

def build_descriptors(mols):
    X = np.zeros((len(mols), len(DESC_NAMES)), dtype=float)
    for i, m in enumerate(mols):
        for j, name in enumerate(DESC_NAMES):
            try:
                X[i, j] = DESC_FUNCS[name](m)
            except Exception:
                X[i, j] = np.nan
    return X

def build_morgan(mols, n_bits=2048, radius=2):
    X = np.zeros((len(mols), n_bits), dtype=np.int8)
    for i, m in enumerate(mols):
        fp = rdMolDescriptors.GetMorganFingerprintAsBitVect(m, radius, nBits=n_bits)
        arr = np.zeros((n_bits,), dtype=np.int8)
        ConvertToNumpyArray(fp, arr)
        X[i] = arr
    return X

print("Building descriptors...")
X_desc = build_descriptors(mols)
print("Building Morgan fingerprints...")
X_fp = build_morgan(mols)

# ---- Fixed stratified split (identical across all rounds) ----
idx_all = np.arange(len(mols))
train_idx, test_idx = train_test_split(
    idx_all, test_size=0.2, random_state=SEED, stratify=y
)
print(f"Train: {len(train_idx)}  Test: {len(test_idx)}")

def metrics(y_true, y_prob):
    y_pred = (y_prob >= 0.5).astype(int)
    return {
        "ROC_AUC": roc_auc_score(y_true, y_prob),
        "PR_AUC": average_precision_score(y_true, y_prob),
        "Balanced_Accuracy": balanced_accuracy_score(y_true, y_pred),
        "F1": f1_score(y_true, y_pred),
        "MCC": matthews_corrcoef(y_true, y_pred),
    }

y_train, y_test = y[train_idx], y[test_idx]
rounds = []
roc_data = {}
pr_data = {}
best_extra = {}

# ---- Round 1: descriptors + logistic regression ----
print("\nRound 1: descriptors + LR")
pipe1 = Pipeline([
    ("imp", SimpleImputer(strategy="median")),
    ("sc", StandardScaler()),
    ("clf", LogisticRegression(max_iter=2000, random_state=SEED)),
])
pipe1.fit(X_desc[train_idx], y_train)
p1 = pipe1.predict_proba(X_desc[test_idx])[:, 1]
m1 = metrics(y_test, p1)
m1["change"] = "Baseline: 10 RDKit physicochemical descriptors + logistic regression"
rounds.append(("Round 1", m1)); roc_data["Round 1"] = p1; pr_data["Round 1"] = p1

# ---- Round 2: Morgan FP + logistic regression ----
print("Round 2: Morgan FP + LR")
pipe2 = Pipeline([
    ("clf", LogisticRegression(max_iter=2000, random_state=SEED, C=1.0)),
])
pipe2.fit(X_fp[train_idx], y_train)
p2 = pipe2.predict_proba(X_fp[test_idx])[:, 1]
m2 = metrics(y_test, p2)
m2["change"] = "Switched to Morgan/ECFP4 fingerprints (2048 bits) + logistic regression"
rounds.append(("Round 2", m2)); roc_data["Round 2"] = p2; pr_data["Round 2"] = p2

# ---- Round 3: Random forest on FP ----
print("Round 3: RF on FP")
rf = RandomForestClassifier(n_estimators=300, random_state=SEED, n_jobs=-1)
rf.fit(X_fp[train_idx], y_train)
p3 = rf.predict_proba(X_fp[test_idx])[:, 1]
m3 = metrics(y_test, p3)
m3["change"] = "Random forest (300 trees) on Morgan fingerprints"
rounds.append(("Round 3", m3)); roc_data["Round 3"] = p3; pr_data["Round 3"] = p3

# ---- Round 4: HistGradientBoosting + CV tuning on train only ----
print("Round 4: HGB + CV tuning")
hgb = HistGradientBoostingClassifier(random_state=SEED)
param_grid = {
    "learning_rate": [0.05, 0.1],
    "max_iter": [200, 400],
    "max_leaf_nodes": [31, 63],
}
cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=SEED)
gs = GridSearchCV(hgb, param_grid, scoring="roc_auc", cv=cv, n_jobs=-1)
gs.fit(X_fp[train_idx], y_train)
p4 = gs.predict_proba(X_fp[test_idx])[:, 1]
m4 = metrics(y_test, p4)
m4["change"] = f"Gradient boosting (HistGB) + 3-fold CV tuning on train only. Best params: {gs.best_params_}"
rounds.append(("Round 4", m4)); roc_data["Round 4"] = p4; pr_data["Round 4"] = p4
best_params4 = gs.best_params_

# ---- Round 5: best model + class imbalance + feature augmentation ----
print("Round 5: augmented features + class weights")
X_aug = np.hstack([X_fp, X_desc]).astype(float)
# impute + scale descriptors portion via full pipeline; use RF with class_weight balanced
from sklearn.compose import ColumnTransformer
n_fp = X_fp.shape[1]
aug_pipe = Pipeline([
    ("imp", SimpleImputer(strategy="median")),
    ("clf", RandomForestClassifier(n_estimators=500, random_state=SEED,
                                    n_jobs=-1, class_weight="balanced")),
])
aug_pipe.fit(X_aug[train_idx], y_train)
p5 = aug_pipe.predict_proba(X_aug[test_idx])[:, 1]
m5 = metrics(y_test, p5)
m5["change"] = "Feature augmentation (fingerprints + descriptors) + RF(500) with class_weight='balanced'"
rounds.append(("Round 5", m5)); roc_data["Round 5"] = p5; pr_data["Round 5"] = p5

# ---- Save metrics csv ----
metric_keys = ["ROC_AUC", "PR_AUC", "Balanced_Accuracy", "F1", "MCC"]
rows = []
for name, m in rounds:
    row = {"round": name}
    for k in metric_keys:
        row[k] = round(m[k], 4)
    row["change"] = m["change"]
    rows.append(row)
metrics_df = pd.DataFrame(rows)
metrics_df.to_csv(os.path.join(RESULTS, "round_metrics.csv"), index=False)

# ---- Determine best round by ROC-AUC ----
best_i = int(np.argmax([m["ROC_AUC"] for _, m in rounds]))
best_name = rounds[best_i][0]
print(f"\nBest round: {best_name}")

# ---- Figures ----
plt.rcParams.update({"figure.dpi": 130, "font.size": 11})
colors = plt.cm.viridis(np.linspace(0.1, 0.85, 5))

# Fig 1: grouped bar chart
fig, ax = plt.subplots(figsize=(11, 6))
x = np.arange(len(metric_keys))
w = 0.15
for i, (name, m) in enumerate(rounds):
    ax.bar(x + i*w, [m[k] for k in metric_keys], w, label=name, color=colors[i])
ax.set_xticks(x + 2*w)
ax.set_xticklabels(metric_keys, rotation=15)
ax.set_ylabel("Score"); ax.set_ylim(0, 1.05)
ax.set_title("EGFR classification metrics across 5 rounds")
ax.legend(ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.08))
ax.grid(axis="y", alpha=0.3)
fig.tight_layout()
fig.savefig(os.path.join(FIGURES, "metrics_barchart.png"), bbox_inches="tight")
plt.close(fig)

# Fig 2: ROC curves
fig, ax = plt.subplots(figsize=(8, 7))
for i, (name, m) in enumerate(rounds):
    fpr, tpr, _ = roc_curve(y_test, roc_data[name])
    ax.plot(fpr, tpr, color=colors[i], lw=2,
            label=f"{name} (AUC={m['ROC_AUC']:.3f})")
ax.plot([0, 1], [0, 1], "k--", alpha=0.4)
ax.set_xlabel("False Positive Rate"); ax.set_ylabel("True Positive Rate")
ax.set_title("ROC curves — all 5 rounds"); ax.legend(loc="lower right")
ax.grid(alpha=0.3); fig.tight_layout()
fig.savefig(os.path.join(FIGURES, "roc_curves.png"), bbox_inches="tight")
plt.close(fig)

# Fig 3: confusion matrix best round
best_prob = roc_data[best_name]
cm = confusion_matrix(y_test, (best_prob >= 0.5).astype(int))
fig, ax = plt.subplots(figsize=(6, 5.5))
im = ax.imshow(cm, cmap="Blues")
for (r, c), v in np.ndenumerate(cm):
    ax.text(c, r, str(v), ha="center", va="center",
            color="white" if v > cm.max()/2 else "black", fontsize=14)
ax.set_xticks([0, 1]); ax.set_yticks([0, 1])
ax.set_xticklabels(["Inactive", "Active"]); ax.set_yticklabels(["Inactive", "Active"])
ax.set_xlabel("Predicted"); ax.set_ylabel("True")
ax.set_title(f"Confusion matrix — best round ({best_name})")
fig.colorbar(im, fraction=0.046)
fig.tight_layout()
fig.savefig(os.path.join(FIGURES, "confusion_matrix_best.png"), bbox_inches="tight")
plt.close(fig)

# Fig 4: PR curve comparison
fig, ax = plt.subplots(figsize=(8, 7))
for i, (name, m) in enumerate(rounds):
    prec, rec, _ = precision_recall_curve(y_test, pr_data[name])
    ax.plot(rec, prec, color=colors[i], lw=2,
            label=f"{name} (PR-AUC={m['PR_AUC']:.3f})")
baseline = y_test.mean()
ax.axhline(baseline, ls="--", color="gray", alpha=0.5, label=f"baseline={baseline:.2f}")
ax.set_xlabel("Recall"); ax.set_ylabel("Precision")
ax.set_title("Precision-Recall curves — all 5 rounds"); ax.legend(loc="lower left")
ax.grid(alpha=0.3); fig.tight_layout()
fig.savefig(os.path.join(FIGURES, "pr_curves.png"), bbox_inches="tight")
plt.close(fig)

# ---- Summary JSON ----
summary = {
    "dataset": DATA,
    "n_molecules": len(mols),
    "n_positive": int(y.sum()),
    "n_negative": int((1 - y).sum()),
    "split": {"type": "stratified", "test_size": 0.2, "random_state": SEED,
              "n_train": int(len(train_idx)), "n_test": int(len(test_idx))},
    "metrics_reported": metric_keys,
    "rounds": [],
}
round_configs = {
    "Round 1": {"features": "10 RDKit physicochemical descriptors", "model": "LogisticRegression"},
    "Round 2": {"features": "Morgan/ECFP4 2048-bit", "model": "LogisticRegression"},
    "Round 3": {"features": "Morgan/ECFP4 2048-bit", "model": "RandomForest(300)"},
    "Round 4": {"features": "Morgan/ECFP4 2048-bit", "model": "HistGradientBoosting", "tuned_params": best_params4},
    "Round 5": {"features": "Morgan FP + 10 descriptors", "model": "RandomForest(500, class_weight=balanced)"},
}
for name, m in rounds:
    summary["rounds"].append({
        "round": name,
        "config": round_configs[name],
        "metrics": {k: round(m[k], 4) for k in metric_keys},
        "change": m["change"],
    })
best_m = rounds[best_i][1]
prev_best = max([rounds[j][1]["ROC_AUC"] for j in range(len(rounds)) if j != best_i])
summary["best_round"] = {
    "round": best_name,
    "ROC_AUC": round(best_m["ROC_AUC"], 4),
    "margin_over_next_best_ROC_AUC": round(best_m["ROC_AUC"] - prev_best, 4),
}
with open(os.path.join(RESULTS, "experiment_summary.json"), "w") as f:
    json.dump(summary, f, indent=2)

# ---- Final table ----
print("\n" + "="*90)
print("FINAL METRICS TABLE")
print("="*90)
print(metrics_df[["round"] + metric_keys].to_string(index=False))
print("="*90)
print(f"BEST ROUND: {best_name}  ROC-AUC={best_m['ROC_AUC']:.4f}")
print(f"Margin over next-best ROC-AUC: +{best_m['ROC_AUC']-prev_best:.4f}")
print("="*90)
print("Done. Outputs in results/ and figures/")
