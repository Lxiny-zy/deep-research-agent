# Google 学术查询手册(写好 `q` 是检索这一步的全部价值)

只有一个检索入口,查询串写得好不好直接决定结果质量。**改写查询永远比多发查询划算**——
检索走的是按次计费的外部服务,额度卡得严(见 [api.md](api.md))。

## 原生语法(Google 学术支持的)

| 语法 | 例子 | 效果 |
|---|---|---|
| 引号短语 | `"contrastive learning"` | 整串精确匹配,**定位已知论文标题的首选** |
| `author:` | `author:"J Dean"` | 限定作者。姓名缩写形式更容易命中(Google 学术自己就这么存) |
| `source:` | `source:"Nature"` | 限定期刊/会议名 |
| `OR` | `survey OR review` | 或(必须大写) |
| `-` | `transformer -vision` | 排除词 |
| `intitle:` | `intitle:diffusion` | 只在标题里找,**收窄噪声很有效** |
| `*` | `"graph * networks"` | 通配一个词 |

年份不用写进 `q`——用请求体的 `year_from`(映射到原生 `as_ylo`,可靠)。

## 常用模板

**主题探索**——先宽后窄,别一上来堆五个限定词
```json
{"q": "graph neural network drug discovery", "num": 10}
```

**定位一篇已知论文**——整串加引号,命中即停,别翻页
```json
{"q": "\"Attention Is All You Need\"", "num": 5}
```

**综述/领域梳理**
```json
{"q": "protein structure prediction survey OR review", "num": 15, "year_from": 2022}
```
> `num` 上限 20;要更多只能翻 `page`,而每页都是一次付费调用。

**追某个人的工作**
```json
{"q": "author:\"Y LeCun\" self-supervised learning", "num": 10, "year_from": 2021}
```

**只看最新进展**(Google 学术是实时全网,没有入库延迟——这是本 skill 的强项)
```json
{"q": "large language model agent memory", "year_from": 2025, "num": 10}
```

**找可复现的实现**
```json
{"q": "diffusion policy robot manipulation github OR \"code available\"", "num": 10}
```

**收窄噪声**——主题词太泛、结果全是不相关的综述时
```json
{"q": "intitle:retrieval intitle:augmented generation evaluation benchmark", "num": 10}
```

## 中文查询

Google 学术的中文语料**远弱于英文**。用户用中文提问时:
1. 把核心概念译成英文再搜(效果差一个量级),并向用户说明"用英文检索覆盖更好"。
2. 确实要搜中文文献时,`hl` 给 `zh-CN`,`q` 用中文关键词。
3. 中英混排(`q: "知识图谱 knowledge graph 医疗"`)通常不如纯英文,不推荐。

## 零结果 / 结果不相关时的降级顺序

```
1. 去掉 year_from（年份卡太死是最常见原因）
2. 去掉最具体的那个方法词（"用 XX-Net 做 YY" → 只留 YY）
3. 引号拆开（精确短语没人这么写 → 换成普通词组）
4. 用响应里的 related_searches 换一组词重发   ← Google 给的相邻查询，比你自己猜准
5. 还是零 → 如实告诉用户搜不到，并列出你已经试过的每一条查询
```

## 挑哪条去深读

拿到 `data[]` 后,**第一件事是把所有 `identifiers.result_id` 丢给 `POST /v1/ocr/resolve`**
(免费、毫秒级),一次就知道哪几篇原件在库里、能免下载深读——覆盖约 70%,而且**不挑出版社**,
付费墙的也照读。

命中的直接 `deepread.sh <result_id>`。剩下 `missing` 的才看下面这套 PDF 直链优先级:
1. 有 `pdf` 字段 **且** 域名是 arxiv / openaccess.thecvf / aclanthology / bioRxiv / medRxiv /
   proceedings.mlr.press / papers.nips.cc / 机构 `.edu` 库 → 基本下得到。
2. 有 `pdf` 但域名是 sciencedirect / springer / wiley / ieeexplore / oup / jstor →
   **大概率 403**,可以试一次,失败就换人,别反复重试。
3. 没有 `pdf` → 深读不了。若 `identifiers.arxiv_id` 存在,可以手动拼
   `https://arxiv.org/pdf/{arxiv_id}` 试试——很多时候 Google 没给 resources 但 arxiv 上有。
4. 同主题多篇都能深读时,优先 `cited_by` 高的那篇(信息密度通常更好)。