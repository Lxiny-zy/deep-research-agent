"""临时诊断：测试多种 HTTP 客户端能否穿过上游中转站前面的 Cloudflare。
用法（容器内跑，复用容器 env，无需贴密钥）：
    git pull
    docker compose cp diag_llm.py api:/tmp/diag.py
    docker compose exec -T api python /tmp/diag.py
诊断完可删：git rm diag_llm.py && git commit -m 'drop diag'
"""

import http.client
import json
import os
import urllib.parse

KEY = os.environ["LLM_API_KEY"]
BASE = os.environ["LLM_BASE_URL"]
MODEL = os.environ["LLM_MODEL"]
MSGS = [{"role": "user", "content": "ping"}]
BODY = {"model": MODEL, "messages": MSGS, "max_tokens": 10}

print(f"base={BASE}  model={MODEL}\n")


def via_openai(label, **kw):
    try:
        from openai import OpenAI

        c = OpenAI(api_key=KEY, base_url=BASE, timeout=30, **kw)
        r = c.chat.completions.create(model=MODEL, messages=MSGS, max_tokens=10)
        print(f"[{label}] OK -> {r.choices[0].message.content!r}")
    except Exception as e:
        print(f"[{label}] FAIL -> {type(e).__name__}: {str(e)[:160]}")


def via_httpclient(label, ua=None):
    try:
        u = urllib.parse.urlparse(BASE)
        h = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}
        if ua:
            h["User-Agent"] = ua
        conn = http.client.HTTPSConnection(u.netloc, timeout=30)
        conn.request("POST", u.path.rstrip("/") + "/chat/completions", json.dumps(BODY), h)
        resp = conn.getresponse()
        print(f"[{label}] HTTP {resp.status} -> {resp.read()[:120]!r}")
    except Exception as e:
        print(f"[{label}] FAIL -> {type(e).__name__}: {str(e)[:160]}")


BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

via_openai("openai-default")  # 复现 App 现状（预期 FAIL）
via_openai("openai-UA-curl", default_headers={"User-Agent": "curl/8.5.0"})
via_openai("openai-UA-browser", default_headers={"User-Agent": BROWSER_UA})
via_httpclient("httpclient-noUA")  # 不带 UA、HTTP/1.1、无 x-stainless 头
via_httpclient("httpclient-UA-curl", ua="curl/8.5.0")
