# 接口参考

本 skill 只调一个服务。**地址与令牌来自环境变量** `SCHOLAR_API_BASE` / `SCHOLAR_API_TOKEN`
(也认 `SCHOLAR_TOKEN`)——没有配置文件。除 `/v1/health` 外所有请求带
`Authorization: Bearer $SCHOLAR_API_TOKEN`。

服务端怎么实现的(用哪个检索供应商、哪套解析后端、文件存哪)与你无关,也不要向用户转述。

| 端点 | 用途 |
|---|---|
| `POST /v1/serp/scholar` | 检索 |
| `GET /v1/health` | 探活(免鉴权) |
| `POST /v1/ocr/resolve` | Google 学术 `result_id` → md5,判断哪些能**免下载**深读 |
| `GET /v1/ocr/image/{img_md5}` | 取正文里的图片字节 |
| `POST /v1/ocr/pdf` · `GET /v1/ocr/{md5}` · `POST /v1/ocr/submit` | 深读底层,**由 `scripts/deepread.sh` 代劳**,一般不用直接调 |

---

## POST `/v1/serp/scholar` — 检索

```json
{ "q": "graph neural network drug discovery",
  "num": 10, "page": 1, "year_from": 2022, "hl": "en" }
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `q` | 必填 | Google 学术查询串。语法见 [query-cookbook.md](query-cookbook.md) |
| `num` | 10 | 每页条数,**1..20**(硬上限,越界 422) |
| `page` | 1 | 页码。**每页 = 一次付费调用** |
| `year_from` | null | 只要该年及以后。是 Google 学术原生的年份过滤,可靠 |
| `hl` | `en` | 界面语言。中文查询给 `zh-CN` |

**响应:**
```json
{ "query": "…", "total_results": 33400,
  "page": 1, "num": 10, "returned": 10, "has_next": true,
  "related_searches": ["…"],
  "data": [ {
    "position": 1,
    "title": "Point transformer",
    "link": "https://openaccess.thecvf.com/…",
    "type": "paper",
    "authors": "H Zhao, L Jiang, J Jia, PHS Torr",
    "year": 2021,
    "publication": "H Zhao… - Proceedings of the …, 2021 - openaccess.thecvf.com",
    "snippet": "… We show that Point Transformers are remarkably effective …",
    "cited_by": 4483,
    "versions": 11,
    "pdf": "https://arxiv.org/pdf/2012.09164",
    "identifiers": { "arxiv_id": "2012.09164", "result_id": "G2UyBewqnKQJ",
                     "cluster_id": "11861402711774881051", "cites_id": "11861402711774881051" }
  } ],
  "markdown": "## Google 学术 · 10 条(共约 33400) · page 1 · 有下一页 …" }
```

**字段可靠性分档**(决定你能不能拿它做判断):

| 档 | 字段 | 说明 |
|---|---|---|
| 几乎必有 | `title` `link` `position` `type` | 直接用 |
| 常有 | `publication` `snippet` `cited_by` `versions` | `snippet` 只有一两句且带省略号,**不是完整 abstract** |
| **看运气** | `pdf` `authors` `year` `identifiers.doi` | **`pdf` 有没有,直接决定这条能不能深读**;`year` 是从 `publication` 里抠的,抠不到就是 null |

- `pdf` 是 Google 学术给的资源链接。arxiv / openaccess.thecvf / aclanthology / bioRxiv / 机构库
  基本能下;**Elsevier / Springer / Wiley / IEEE / OUP 直链大概率 403**(付费墙+反爬)。
  能不能真下到,由 `deepread.sh` 验 `%PDF-` 魔数说了算,**别看链接猜**。
- `identifiers.cluster_id` 是"同一篇的所有版本"的聚类 id,`cites_id` 是被引列表 id。
  本接口不解析它们,记下来供人工去 scholar.google.com 追。
- `markdown` 已排好版(编号/标题/作者·年份/被引·版本/链接/📄 PDF/snippet),条数不多时
  **直接读、直接呈现**,不必自己从 `data` 再拼一遍。

---

## GET `/v1/health` — 探活(免鉴权)

```json
{ "available": { "serp": true, "ocr": true },
  "detail":    { "serp": "…", "ocr": "…" } }
```

- **`available.serp`** = 能不能检索。**`available.ocr`** = 能不能深读。两者独立。
- **`available.gs_map`** = id→md5 映射是否可用。为 false 时深读没坏,只是**少了免下载那条捷径**,
  得退回到"下载 PDF 再上传",付费墙那批就读不到了。
- 为 false 时 `detail` 里是**服务端给出的原因**。那是服务端侧的问题:
  **原样转达给用户/管理员,别重试、别自己猜。**
- 响应里还会有 `es`、`degradation` 等字段,那是同一服务上另一条检索路径的状态,
  **与本 skill 无关,忽略**。

`scripts/healthcheck.sh` 就是包了这个端点 + 一次令牌校验,开工前跑它就够。
令牌校验用的是 `GET /v1/ocr/{一个必然不存在的 md5}`:**不花钱**(纯查表),401 与 200 一样
分得清。**别改成打 `POST /v1/serp/scholar` 试探** —— 那是按次计费的真检索。

---

## POST `/v1/ocr/resolve` — 哪几篇能免下载深读

我方爬过的原件都按 Google 学术 id 登记着(1.3 亿条,覆盖 **70%+**)。命中就说明原件已在库里,
**直接按 md5 深读、不用下载 PDF**——付费墙、反爬、出版社 403 那一整类问题全部绕开。

```json
{ "result_ids": ["5Gohgn6QFikJ", "4ZRunyZN6eIJ", "BwvwD2whQ64J"] }
```
一次最多 50 个(超了 400)。**免费、毫秒级、不占检索额度。**

```json
{ "resolved": { "5Gohgn6QFikJ": {"md5": "7a6bb1fc…", "file_type": "pdf"},
                "BwvwD2whQ64J": {"md5": "8535fb4b…", "file_type": "pdf"} },
  "missing": ["4ZRunyZN6eIJ"],
  "requested": 3, "hit": 2,
  "agent_hint": "2/3 命中：这些可直接用 md5 提交深读、无需下载 PDF。…" }
```

**典型用法**:SERP 拿到一页结果
…[truncated]