"""物料构建: 从 skillsbench 原始仓库(101 任务)提取 gold trace + 同构反事实。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SKILLS_ROOT = Path("/data1/xiao/SkillProbing/skills_tasks")   # 旧 23 任务子集
SB_ROOT = Path("/data1/xiao/skillsbench")                     # 原始仓库 87+14


@dataclass
class ProbeMaterial:
    task_id: str
    skill_name: str
    skill_md: str
    task_text: str
    gold_trace: str
    identifiers: list[str] = field(default_factory=list)
    key_spans: list[tuple[int, int]] = field(default_factory=list)


def _strip_frontmatter(text: str) -> str:
    parts = text.split("---")
    return "---".join(parts[2:]) if len(parts) >= 3 else text


def _pick_gold_trace(skill_md: str) -> str:
    blocks = re.findall(r"```(\w*)\n(.*?)```", skill_md, re.S)
    py = [b for lang, b in blocks if lang in ("python", "bash", "") and len(b.strip()) > 40]
    if py:
        return max(py, key=len).strip()
    steps = re.findall(r"^\s*\d+\.\s+(.+)$", skill_md, re.M)
    if steps:
        return "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps[:12]))
    return ""


_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{3,})\b")
_GENERIC = {"import", "from", "sys", "path", "append", "print", "self", "this",
            "return", "with", "open", "True", "False", "None", "str", "int",
            "float", "list", "dict", "len", "for", "in", "if", "else", "def",
            "class", "text", "file", "data", "value", "result", "report", "args",
            "Step", "Usage", "Note", "Example"}


def _is_real_identifier(w: str) -> bool:
    if w in _GENERIC:
        return False
    if "_" in w:
        return len(w) >= 6 and not w.isupper()
    if w[0].isupper():
        return bool(re.search(r"[a-z][A-Z]", w))
    return False


def extract_identifiers(trace: str) -> tuple[list[str], list[tuple[int, int]]]:
    """真标识符(跳过注释行), 避免把普通英文词当标识符。"""
    idents, spans = [], []
    for line_match in re.finditer(r"[^\n]*(?:\n|$)", trace):
        line = line_match.group(0)
        off = line_match.start()
        if line.lstrip().startswith("#"):
            continue
        for m in _IDENT_RE.finditer(line):
            w = m.group(1)
            if not _is_real_identifier(w):
                continue
            idents.append(w)
            spans.append((off + m.start(), off + m.end()))
    return idents, spans


def iter_sb_task_dirs() -> list[Path]:
    tasks = sorted(p for p in (SB_ROOT / "tasks").iterdir() if p.is_dir())
    names = {p.name for p in tasks}
    extra = sorted(p for p in (SB_ROOT / "tasks-extra").iterdir()
                   if p.is_dir() and p.name not in names)
    return tasks + extra


def build_materials(root: Path | None = None) -> list[ProbeMaterial]:
    """root=None 时枚举 skillsbench 全部任务(tasks+tasks-extra 去重)。"""
    task_dirs = iter_sb_task_dirs() if root is None else sorted(p for p in root.iterdir() if p.is_dir())
    out = []
    for task_dir in task_dirs:
        skills_dir = task_dir / "environment" / "skills"
        if not skills_dir.is_dir():
            continue
        skdirs = sorted(p for p in skills_dir.iterdir() if p.is_dir())
        if not skdirs:
            continue
        sk = skdirs[0]
        skmd_path = sk / "SKILL.md"
        if not skmd_path.exists():
            continue
        skmd = skmd_path.read_text(encoding="utf-8", errors="ignore")
        task_md = task_dir / "task.md"
        task_text = _strip_frontmatter(
            task_md.read_text(encoding="utf-8", errors="ignore")) if task_md.exists() else ""
        gold = _pick_gold_trace(skmd)
        idents, spans = extract_identifiers(gold)
        out.append(ProbeMaterial(
            task_id=task_dir.name, skill_name=sk.name, skill_md=skmd,
            task_text=task_text, gold_trace=gold,
            identifiers=idents, key_spans=spans,
        ))
    return out


def make_counterfactual(gold: str, key_spans: list[tuple[int, int]],
                        donor_identifiers: list[str], rng) -> str:
    if not key_spans or not donor_identifiers:
        return gold
    out, prev, used = [], 0, set()
    n = len(donor_identifiers)
    for i, (s, e) in enumerate(key_spans):
        w = gold[s:e]
        cands = [d for d in donor_identifiers
                 if d not in used and d != w
                 and (d[0].isupper()) == (w[0].isupper())]
        if not cands:
            cands = [d for d in donor_identifiers if d != w] or donor_identifiers
        repl = rng.choice(cands) if hasattr(rng, "choice") else cands[i % len(cands)]
        used.add(repl)
        out.append(gold[prev:s])
        out.append(repl)
        prev = e
    out.append(gold[prev:])
    return "".join(out)


def global_identifier_pool(materials: list[ProbeMaterial]) -> list[str]:
    pool = set()
    for m in materials:
        pool.update(m.identifiers)
    return sorted(pool)
