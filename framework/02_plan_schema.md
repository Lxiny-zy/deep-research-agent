# 02 — Plan.json Schema 与验证

## Schema 定义

```json
{
  "title": "string, ≤40 chars, required",
  "steps": [
    {
      "id": "string, kebab-case, unique, required",
      "name": "string, ≤15 chars (中文) / ≤20 chars (英文), required",
      "prompt": "string, full prompt text, required",
      "reset": "bool, optional, default true",
      "status": "string, one of pending/running/done/partial/failed/skipped, default pending"
    }
  ]
}
```

## 字段规则

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| title | string | 是 | 总标题，≤40 字符 |
| steps[].id | string | 是 | kebab-case，跨 plan 唯一 |
| steps[].name | string | 是 | UI 显示名，中文 ≤15 字，英文 ≤20 chars |
| steps[].prompt | string | 是 | 完整 prompt 文本，下一个 step 直接收到这串 |
| steps[].reset | bool | 否 | 默认 true；每个 step 独立 context |
| steps[].status | string | 否 | 默认 "pending" |

## Status 状态机

```
pending → running → done
                  → partial（有产出但不完整，记录缺口）
                  → skipped（明确不该执行）
                  → failed（基础设施错误，非研究局限）
```

- `partial` 是终态，worker 继续执行后续 step
- `failed` 由 worker 判定（认证/存储/调度错误），Agent 不伪装成 failed
- `done` 可由 step 自行声明，或由 worker 安全兜底

## 验证规则

Planner 输出的 plan.json 必须通过以下检查:

```python
import json

def validate_plan(plan_json_str):
    plan = json.loads(plan_json_str)

    # Top level
    assert isinstance(plan, dict), "plan must be dict"

    # Title
    assert 'title' in plan and isinstance(plan['title'], str) and len(plan['title']) <= 40

    # Steps
    steps = plan.get('steps')
    assert isinstance(steps, list) and len(steps) > 0, "steps must be non-empty list"

    # IDs
    ids = [s['id'] for s in steps]
    assert len(ids) == len(set(ids)), "duplicate ids"
    for sid in ids:
        assert isinstance(sid, str) and '-' in sid or sid.replace('-','').isalnum(), f"bad id format: {sid}"

    # Each step
    for s in steps:
        for k in ('id', 'name', 'prompt'):
            assert isinstance(s.get(k), str) and s[k].strip(), f"bad {k}"

        # Name length
        assert len(s['name']) <= 20, f"name too long: {s['name']}"

        # Status
        assert s.get('status', 'pending') == 'pending', "initial status must be pending"

        # Reset (optional)
        r = s.get('reset', True)
        assert isinstance(r, bool), "reset must be bool"

    # Step count sanity
    assert 3 <= len(steps) <= 30, f"step count {len(steps)} outside 3-30"

    return plan
```

## 执行器状态管理

```python
class PlanExecutor:
    def __init__(self, plan):
        self.plan = plan
        self.results = {}

    def get_next_pending(self):
        for s in self.plan['steps']:
            if s['status'] == 'pending':
                return s
        return None

    def mark_running(self, step_id):
        for s in self.plan['steps']:
            if s['id'] == step_id:
                s['status'] = 'running'

    def mark_done(self, step_id, output_paths=None):
        for s in self.plan['steps']:
            if s['id'] == step_id:
                s['status'] = 'done'
                if output_paths:
                    s['output_paths'] = output_paths

    def mark_partial(self, step_id, gap_note, output_paths=None):
        for s in self.plan['steps']:
            if s['id'] == step_id:
                s['status'] = 'partial'
                s['gap_note'] = gap_note
                if output_paths:
                    s['output_paths'] = output_paths

    def mark_failed(self, step_id, reason):
        for s in self.plan['steps']:
            if s['id'] == step_id:
                s['status'] = 'failed'
                s['fail_reason'] = reason

    def get_completed_outputs(self):
        """Collect all output paths from completed steps for the aggregator."""
        outputs = []
        for s in self.plan['steps']:
            if s['status'] in ('done', 'partial'):
                outputs.extend(s.get('output_paths', []))
        return outputs
