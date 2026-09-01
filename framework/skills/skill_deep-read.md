---
name: academic-search-v2-deep-read
description: >
  academic-search-v2 的深读子技能:把一篇论文的 PDF 解析成 markdown 正文,再按用户问题定位阅读。
  当你已经用 /v1/serp/scholar 定位到某篇论文、而用户的问题超出 snippet(问方法细节、实验设置、
  损失函数、某一节讲了什么、要看图表公式)时读本文件。
---

# 深读(academic-search-v2)

**深读是贵层**:要下载 PDF、要跑解析、要等几十秒到几分钟。
默认不深读——SERP 的 snippet 能回答的问题就别深读。

## 什么时候才深读

**该深读:**
- 用户明确问正文细节:"第 3 节怎么做的""用了什么损失函数""实验在哪个数据集上跑的"
  "超参数是多少""结论的前提条件是什么"。
- 用户要复现/评估某个方法,snippet 那一两句根本不够。
- 用户要看论文里的某张图/某个公式。
- 你要基于这篇下结论,而 snippet 里的信息**自相矛盾或明显被截断**。

**不该深读:**
- 用户只想知道"有哪些相关工作"——检索结果本身就是答案。
- 用户问的是领域概况、趋势——多篇 snippet 聚合起来更合适,深读一篇反而以偏概全。
- 一次要读五篇以上。→ **无人值守时没人可确认**:自己按 `cited_by` 降序 + 有开放 PDF 挑
  3–5 篇,一次一篇,并在产物里写明挑选依据与被跳过的篇目。

## 怎么读

```bash
SK=<本 skill 目录>                                # 见主 SKILL.md「零」
DEEP=<深读产物目录>                               # ← 必填，别用默认的 ./tmp/

"$SK/scripts/deepread.sh" "<SERP 结果里的 pdf 直链>" "$DEEP/<md5>"   # 主路径
"$SK/scripts/deepread.sh" <32位md5>              "$DEEP/<md5>"   # 确知处理过时
"$SK/scripts/deepread.sh" ./local.pdf            "$DEEP/local"   # 用户自己给的 PDF
```
正文 → `<输出目录>/result.md`,元信息 → 同目录 `meta.json`。
**第二个参数不传会落进 `./tmp/deepread/`**,那只够手工试跑(流水线里跨 step 会丢)。