"""全库完整性扫描（validate_all）——扫描所有知识包 + 攻略库。

用法：python -m app.validate_all [--report report.json]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # app/ 的上一层 = 仓库根
sys.path.insert(0, str(ROOT))

from ingest.compiler.validate_graph import validate
from runtime.knowledge_loader import KnowledgePackage

GUIDES = ROOT / "knowledge" / "guides" / "maps"
# 不在 import 期对 knowledge/source.iterdir()——
# 该目录缺失时 iterdir 直接抛 FileNotFoundError 崩栈。
# 目录存在性检查下移到 scan()，缺失时给可见错误 + 非零退出码。
PKG_ROOT = ROOT / "knowledge" / "source"


def scan():
    report = {"knowledge_packages": {}, "guides": {}, "errors": [], "ok": True}
    if not PKG_ROOT.exists():
        # 目录缺失——工具自身应当优雅失败（非零退出 + 明确文案），不崩栈
        report["errors"].append(
            f"knowledge/source 目录不存在: {PKG_ROOT}\n"
            f"  修复：完整拉取仓库（git clone / git pull），或确认未被 gitignore 排除")
        report["ok"] = False
    else:
        for pdir in sorted(d for d in PKG_ROOT.iterdir() if d.is_dir()):
            try:
                pkg = KnowledgePackage(pdir)
                errors, warnings = validate(pkg, verbose=False)
                report["knowledge_packages"][pdir.name] = {
                    "status": "FAIL" if errors else "PASS",
                    "errors": errors[:10], "warnings": warnings[:5],
                    "environment": pkg.environment}
                if errors:
                    report["ok"] = False
            except Exception as e:
                report["knowledge_packages"][pdir.name] = {
                    "status": "ERROR",
                    "errors": [f"{type(e).__name__}: {e}"],
                    "environment": None}
                report["ok"] = False
    if GUIDES.exists():
        for md in sorted(GUIDES.iterdir()):
            if not md.is_dir():
                continue
            area_files = list((md / "areas").glob("*.json")) \
                if (md / "areas").exists() else []
            point_files = list((md / "points").glob("*.json")) \
                if (md / "points").exists() else []
            problems = []
            if not (md / "map.json").exists():
                problems.append("缺 map.json")
            if not area_files:
                problems.append("无区域文件")
            for f in point_files:
                if f.name == "points_meta.json":
                    continue
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if not isinstance(data, list):
                        problems.append(f"{f.name}: 非列表")
                except Exception as e:
                    problems.append(f"{f.name}: 损坏 {type(e).__name__}")
            report["guides"][md.name] = {
                "status": "FAIL" if problems else "PASS",
                "areas": len(area_files), "point_files": len(point_files),
                "problems": problems[:10]}
            if problems:
                report["ok"] = False
    return report


def main():
    report = scan()
    print(f"== validate_all: {'PASS' if report['ok'] else 'FAIL'} ==")
    # F-04 修复：顶层错误优先可见（目录缺失等工具级错误）
    for err in report.get("errors", []):
        print(f"  [ERROR] {err}")
    for name, info in report["knowledge_packages"].items():
        print(f"  [{info['status']}] 知识包 {name} (env={info.get('environment')})")
    for name, info in report["guides"].items():
        print(f"  [{info['status']}] 地图 {name} (areas={info['areas']}, points={info['point_files']})")
    if "--report" in sys.argv:
        i = sys.argv.index("--report")
        if i + 1 >= len(sys.argv):
            print("用法: python -m app.validate_all --report <输出路径>")
            return 2
        out = Path(sys.argv[i + 1])
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"报告: {out}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
