"""对已完成 ABSENT 的任务, 用修复后的 verifier 在已有 workspace 上重新判分 (零成本)。"""
import json
import sys
from pathlib import Path

PROJ = Path("/data1/xiao/SkillProbing")
sys.path.insert(0, str(PROJ / "src"))
from skillprobing.tasks.verifier import run_verifier

gt_path = PROJ / "outputs" / "gold_standard" / "gt.json"
gt = json.load(open(gt_path))


def gt_of(w_pass, wo_pass):
    if wo_pass:
        return "BUILTIN"
    if w_pass:
        return "NOT_BUILTIN"
    return "ABSENT"


for tid, v in sorted(gt.items()):
    if v["verdict"] != "ABSENT":
        continue
    base = PROJ / "outputs" / "gold_standard" / tid
    w = run_verifier(base / "with_skill", base / "with_skill" / "workspace")
    wo = run_verifier(base / "without_skill", base / "without_skill" / "workspace")
    new = gt_of(w.passed, wo.passed)
    old = v["verdict"]
    if new != old:
        v["with"].update(passed=w.passed, score=w.score, detail=w.detail[-300:])
        v["without"].update(passed=wo.passed, score=wo.score, detail=wo.detail[-300:])
        v["verdict"] = new
        print(f"{tid}: {old} -> {new}")
    else:
        print(f"{tid}: {old} (不变)")

json.dump(gt, open(gt_path, "w"), ensure_ascii=False, indent=2)
