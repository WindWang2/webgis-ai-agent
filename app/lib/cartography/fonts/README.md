# app/lib/cartography/fonts — vendored CJK 字体（ADR-0157 P3/P6）

- 文件：`NotoSansSC-Regular-subset.ttf`（2.3MB，wght=400 静态实例 + 子集）
- 许可：SIL Open Font License 1.1（`OFL.txt`）—— 可随仓再分发，web/embedding 均允许。
- 来源：google/fonts `ofl/notosanssc/NotoSansSC[wght].ttf`（2026-09 主干）。

## 子集构建（可复现）

```bash
# 1) 实例化可变字体为静态 wght=400
python -m fontTools.varLib.instancer NotoSansSC[wght].ttf wght=400 -o noto-static.ttf
# 2) 子集字符集 = ASCII 0x20-0x7E + CJK 常用标点 + GB2312 全表（6763 汉字，共 7545 字符）
python -m fontTools.subset noto-static.ttf --text-file=charset.txt -o NotoSansSC-Regular-subset.ttf
```

字符集生成器与构建命令由 ac-08 线维护（任务书 §8 记录）；子集含 7777 字形、
cmap 7547 码位，出版导出的标题/图例/署名等中文文本层全覆盖。

## 消费方

1. **前端**：`frontend/public/fonts/` 同字节副本 —— jsPDF `addFileToVFS` 嵌入
   PDF 真实文本层（`frontend/lib/export/pdf-font.ts`）。
2. **后端**：本目录 —— `pdf_renderer.py`（matplotlib `font_manager.addfont`）
   与 report/publication HTML 的 `@font-face`（file URL，经 url_fetcher
   白名单放行）。

> 同一份构建产物双面部署：前端容器与后端容器文件系统独立，各自需要一份。
> 修订字体时在两侧同步替换并更新本说明。
