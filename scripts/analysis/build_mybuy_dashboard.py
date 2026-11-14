#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 mybuy-data.json + mybuy-curves.json 内联进模板，生成可离线打开的看板。"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE.parents[1] / "apps/chanlun-web/analysis"
TPL = WEB / "mybuy.template.html"
OUT = WEB / "mybuy.html"


def main() -> None:
    data = json.loads((WEB / "mybuy-data.json").read_text())
    if (WEB / "mybuy-curves.json").exists():
        data["curves"] = json.loads((WEB / "mybuy-curves.json").read_text())
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = TPL.read_text().replace("/*__DATA__*/", payload)
    OUT.write_text(html)
    print(f"{OUT.relative_to(HERE.parents[2])}  {len(html)/1024:.1f} KB  curves={len(data['curves'])}")


if __name__ == "__main__":
    main()
