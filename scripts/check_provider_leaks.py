#!/usr/bin/env python
"""检查 Provider 名称是否错误泄漏到 Spatial Fabric 核心领域代码。

Provider 名字可以出现在 adapters/providers、配置、测试和文档中；不应成为 Core Domain
模型字段或业务服务依赖。该检查故意简单，它的作用是让可疑耦合尽早进入代码审查。
"""

from __future__ import annotations
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "backend" / "spatial_fabric"
PROVIDER_WORDS = ("geoserver", "martin", "titiler", "keycloak", "openfga", "kubernetes", "minio")
# 注释中解释“禁止依赖某 Provider”是允许的，因此只检查明显字段/符号风格片段。
SUSPICIOUS = tuple(
    item
    for word in PROVIDER_WORDS
    for item in (f"{word}_id", f"{word}_url", f"{word}_workspace", f"{word}_source")
)


def main() -> int:
    findings: list[str] = []
    for path in CORE.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for token in SUSPICIOUS:
            if token in text:
                findings.append(f"{path.relative_to(ROOT)}: suspicious provider token '{token}'")
    if findings:
        print("\n".join(findings))
        return 1
    print("Provider leakage check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
