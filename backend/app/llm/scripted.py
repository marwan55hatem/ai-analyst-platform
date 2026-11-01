"""Scripted analyst: a deterministic, rule-based stand-in for a real model (LLM_PROVIDER=fake).

It is a test model, not a real LLM, and says so in every answer. For a small set of question
patterns it plans real tool calls, then writes the answer only from the tool results it received
(numbers are copied, never computed), in the order Recommendation / Why (evidence) / Expected
impact (range) / Risks / Next step. Anything else gets one clarifying question.
"""

import json
import re
import time
from collections.abc import Callable, Iterator
from typing import Any

from app.llm.fake import FakeTurn, stream_turn
from app.llm.types import ChatMessage, LLMEvent, RateLimitWait, TextDelta, ToolSpec

SCRIPTED_BANNER = "**[Scripted test model - not a real LLM]**"
SUMMARY_MARKER = "SUMMARIZE_CONVERSATION"
INVENTED_NUMBER = "9,876,543.21"
# Markers that let end-to-end tests reach states a healthy scripted turn never shows.
RATE_LIMIT_MARKER = "[rate-limit-demo]"
SLOW_MARKER = "[slow-demo]"
TABLE_MARKER = "[table-demo]"
RATE_LIMIT_WAIT_S = 3
SLOW_WORD_S = 0.15
TABLE_DEMO = (
    f"{SCRIPTED_BANNER}\n\n"
    "| Region of the business | Product category name | Sales channel used | "
    "Customer group label | Payment method chosen | Delivery option taken |\n"
    "| --- | --- | --- | --- | --- | --- |\n"
    "| North and the coastal towns | Office furniture and supplies | Online shop front | "
    "Returning small businesses | Bank transfer on invoice | Standard ground delivery |\n"
    "| South and the inland towns | Stationery and paper goods | Stores and resellers | "
    "New household buyers | Card at the checkout | Collection from the store |\n\n"
    "```sql\n"
    "SELECT region_of_the_business, product_category_name, sales_channel_used, "
    "customer_group_label, payment_method_chosen, delivery_option_taken FROM data "
    "GROUP BY region_of_the_business, product_category_name, sales_channel_used\n"
    "```"
)
Step = list[tuple[str, dict[str, Any]]]
Results = dict[str, Any]

_PRICE = re.compile(
    r"(raise|increase|lift|cut|lower|reduce|drop)\s+(?:the\s+|my\s+|our\s+)?([\w&' -]+?)\s+"
    r"prices?\s+(?:by\s+)?(\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
_CODE = re.compile(r"run python:\s*```(?:python)?\n(.*?)```", re.IGNORECASE | re.DOTALL)
_ARABIC = re.compile(r"[\u0600-\u06FF]")


def _money(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:,.2f}"
    return "n/a"


def _count(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:,.0f}"
    return "n/a"


def _pct(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value * 100:.1f}%"
    return "n/a"


def _sections(recommendation: str, why: list[str], impact: str, risks: str, next_step: str) -> str:
    evidence = "\n".join(f"- {line}" for line in why if line) or "- No tool returned evidence."
    return (
        f"{SCRIPTED_BANNER}\n\n"
        f"**Recommendation** - {recommendation}\n\n"
        f"**Why (evidence)**\n{evidence}\n\n"
        f"**Expected impact (range)** - {impact}\n\n"
        f"**Risks** - {risks}\n\n"
        f"**Next step** - {next_step}"
    )


class ScriptedAnalyst:
    """Deterministic analyst for walkthroughs and tests; it never calls a network."""

    sleep = staticmethod(time.sleep)

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Profiling role refinement: keep the rule-based roles."""
        return {"columns": {}}

    def chat(
        self, messages: list[ChatMessage], tools: list[ToolSpec], *, model: str
    ) -> Iterator[LLMEvent]:
        """Plan the next tool calls or write the final answer for the latest question."""
        if messages and messages[0].content.startswith(SUMMARY_MARKER):
            yield from stream_turn(
                FakeTurn(text=self._summary(messages)), messages, model, call_prefix="sum"
            )
            return
        question, done, results = _turn_state(messages)
        if RATE_LIMIT_MARKER in question and done == 0:
            yield RateLimitWait(RATE_LIMIT_WAIT_S)
            self.sleep(RATE_LIMIT_WAIT_S)
        plan = self._plan(question)
        available = {t.name for t in tools}
        step = plan[done] if done < len(plan) and available else None
        if step is not None:
            calls = step(results) if callable(step) else step
            if calls and all(name in available for name, _ in calls):
                turn = FakeTurn(tool_calls=list(calls))
                yield from stream_turn(turn, messages, model, call_prefix=f"scripted_{done}")
                return
        text = self._answer(question, results)
        for event in stream_turn(FakeTurn(text=text), messages, model, call_prefix="answer"):
            if SLOW_MARKER in question and isinstance(event, TextDelta):
                self.sleep(SLOW_WORD_S)
            yield event

    # -- planning -------------------------------------------------------------------------------

    def _plan(self, question: str) -> list[Step | Callable[[Results], Step]]:
        q = question.lower()
        code = _CODE.search(question)
        if code:
            return [[("run_python", {"code": code.group(1)})]]
        if "[verifier-demo]" in q:
            return [[("get_insights", {"type": "kpi_summary", "limit": 1})]]
        if _PRICE.search(question) or "what if" in q or "ماذا لو" in question:
            return [[("what_if", {"scenarios": [self._price_scenario(question)]})]]
        if "chart" in q or "رسم" in question:
            return [[("get_dataset_overview", {})], lambda r: self._chart(r, "bar" in q)]
        if (
            any(k in q for k in ("what should", "grow profit", "recommend"))
            or "ماذا يجب" in question
        ):
            return [
                [
                    ("get_insights", {"limit": 5}),
                    ("explain_drivers", {}),
                    (
                        "what_if",
                        {
                            "scenarios": [
                                {"lever": "price", "change_pct": 5, "scope": "all"},
                                {"lever": "cost", "change_pct": -5, "scope": "all"},
                            ]
                        },
                    ),
                ]
            ]
        if (
            any(k in q for k in ("forecast", "predict", "next month", "next quarter"))
            or "توقع" in question
        ):
            return [[("get_forecast", {"metric": "revenue", "horizon": 6})]]
        if any(k in q for k in ("anomal", "unusual", "spike", "outlier")):
            return [[("get_anomalies", {})]]
        if "segment" in q or "شرائح" in question:
            return [[("get_segments", {"limit": 5})]]
        if "churn" in q or "تسرب" in question:
            return [[("get_churn_risk", {"top_n": 5})]]
        if any(
            k in q
            for k in (
                "top product",
                "best product",
                "best-selling",
                "show the data",
                "list products",
            )
        ):
            return [[("get_dataset_overview", {})], self._top_products_sql]
        if "email" in q or "phone" in q:
            return [[("get_dataset_overview", {})], self._personal_data_sql]
        if (
            any(k in q for k in ("net revenue", "revenue", "sales", "turnover"))
            or "إيراد" in question
        ):
            return [[("get_insights", {"type": "kpi_summary", "limit": 1})]]
        return []

    @staticmethod
    def _price_scenario(question: str) -> dict[str, Any]:
        match = _PRICE.search(question)
        if not match:
            return {"lever": "price", "change_pct": 5, "scope": "all"}
        verb, target, pct = match.groups()
        change = float(pct) * (-1 if verb.lower() in ("cut", "lower", "reduce", "drop") else 1)
        target = target.strip()
        if target.lower() in ("all", "our", "my", "the", "all our", "all my"):
            return {"lever": "price", "change_pct": change, "scope": "all"}
        return {"lever": "price", "change_pct": change, "scope": "category", "target": target}

    @staticmethod
    def _columns(results: Results) -> dict[str, str]:
        overview = results.get("get_dataset_overview") or {}
        roles: dict[str, str] = {}
        for column in overview.get("columns", []):
            roles.setdefault(column.get("role"), column.get("name"))
        return roles

    @staticmethod
    def _amount(roles: dict[str, str]) -> str:
        if roles.get("revenue"):
            return f'SUM("{roles["revenue"]}")'
        if roles.get("price") and roles.get("quantity"):
            return f'SUM("{roles["price"]}" * "{roles["quantity"]}")'
        return "COUNT(*)"

    def _chart(self, results: Results, ranked: bool) -> Step:
        roles = self._columns(results)
        amount = self._amount(roles)
        item = roles.get("product") or roles.get("category")
        if ranked and item:
            sql = (
                f'SELECT "{item}" AS item, ROUND({amount}, 2) AS amount FROM data '
                "GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
            )
            return [("make_chart", {"sql": sql, "title": "Top items"})]
        if not roles.get("date"):
            return []
        day = f'CAST("{roles["date"]}" AS DATE)'
        sql = (
            f"SELECT CAST(date_trunc('month', {day}) AS DATE) AS month, "
            f"ROUND({amount}, 2) AS amount FROM data GROUP BY 1 ORDER BY 1"
        )
        return [("make_chart", {"sql": sql, "title": "Revenue by month"})]

    def _top_products_sql(self, results: Results) -> Step:
        roles = self._columns(results)
        product = roles.get("product") or roles.get("category")
        if not product:
            return []
        amount = self._amount(roles)
        sql = (
            f'SELECT "{product}" AS item, ROUND({amount}, 2) AS amount FROM data '
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
        )
        return [("run_sql", {"sql": sql})]

    def _personal_data_sql(self, results: Results) -> Step:
        roles = self._columns(results)
        column = roles.get("email") or roles.get("phone") or roles.get("customer_id")
        if not column:
            return []
        return [("run_sql", {"sql": f'SELECT "{column}" FROM data LIMIT 5'})]

    # -- answers --------------------------------------------------------------------------------

    def _answer(self, question: str, results: Results) -> str:
        q = question.lower()
        if TABLE_MARKER in q:
            return TABLE_DEMO
        if not results:
            return (
                f"{SCRIPTED_BANNER}\n\nI can answer questions about net revenue, forecasts, "
                "anomalies, customer segments, churn risk, price what-ifs and what to do next. "
                "Which of these do you mean?"
            )
        errors = [f"{name}: {r.get('message')}" for name, r in results.items() if r.get("error")]
        if "run_python" in results:
            return self._python_answer(results["run_python"])
        if errors and len(errors) == len(results):
            return _sections(
                "The data cannot answer this right now.",
                errors,
                "No estimate: the tools returned errors.",
                "The answer depends on the missing results.",
                "Fix the reported problem and ask again.",
            )
        if "[verifier-demo]" in q:
            return self._verifier_demo(results)
        if _ARABIC.search(question) and set(results) == {"get_insights"}:
            return self._arabic_revenue_answer(results)
        for name, writer in (
            (
                "what_if",
                self._what_if_answer if "get_insights" not in results else self._plan_answer,
            ),
            ("get_forecast", self._forecast_answer),
            ("get_anomalies", self._findings_answer),
            ("get_segments", self._findings_answer),
            ("get_churn_risk", self._churn_answer),
            ("run_sql", self._sql_answer),
            ("make_chart", self._chart_answer),
            ("get_insights", self._revenue_answer),
        ):
            if name in results:
                return writer(results)
        return self._findings_answer(results)

    def _revenue_answer(self, results: Results) -> str:
        insights = results["get_insights"].get("insights", [])
        if not insights:
            return _sections(
                "The data cannot answer this yet.",
                ["No KPI insight is stored."],
                "Not available.",
                "None.",
                "Run the analysis, then ask again.",
            )
        top = insights[0]
        data = top.get("data", {})
        why = [
            f"{top.get('title')} (confidence {top.get('confidence')}, "
            f"{_count(top.get('sample_size'))} rows).",
            f"Gross sales {_money(data.get('gross_revenue'))}, returns "
            f"{_money(data.get('returns_total'))}, net revenue {_money(data.get('net_revenue'))}.",
            f"{_count(data.get('orders'))} orders, average order value "
            f"{_money(data.get('average_order_value'))}.",
        ]
        return _sections(
            f"Use net revenue of {_money(data.get('net_revenue'))} as the baseline for planning.",
            why,
            "This is a measured total, not an estimate, so there is no range.",
            "Totals depend on the data's date range and on returns being recorded.",
            "Ask for the trend or the forecast to see where it is heading.",
        )

    def _arabic_revenue_answer(self, results: Results) -> str:
        insights = results["get_insights"].get("insights", [])
        if not insights:
            return f"{SCRIPTED_BANNER}\n\nلا تكفي البيانات للإجابة: لا توجد نتائج مؤشرات محفوظة."
        data = insights[0].get("data", {})
        net = _money(data.get("net_revenue"))
        return (
            f"{SCRIPTED_BANNER}\n\n"
            f"**التوصية** - استخدم صافي الإيراد البالغ {net} أساسًا للتخطيط.\n\n"
            "**السبب (الأدلة)**\n"
            f"- إجمالي المبيعات {_money(data.get('gross_revenue'))}، "
            f"والمرتجعات {_money(data.get('returns_total'))}.\n"
            f"- عدد الطلبات {_count(data.get('orders'))}، "
            f"ومتوسط قيمة الطلب {_money(data.get('average_order_value'))}.\n\n"
            "**الأثر المتوقع (النطاق)** - هذا إجمالي مقاس وليس تقديرًا، فلا يوجد نطاق.\n\n"
            "**المخاطر** - تعتمد الأرقام على فترة البيانات وعلى تسجيل المرتجعات.\n\n"
            "**الخطوة التالية** - اطلب الاتجاه أو التوقع لمعرفة المسار."
        )

    def _chart_answer(self, results: Results) -> str:
        content = results["make_chart"]
        if content.get("error"):
            return _sections(
                "The data cannot answer this.",
                [str(content.get("message"))],
                "Not available.",
                "None.",
                "Ask for a different view.",
            )
        return _sections(
            f'Use the chart "{content.get("title")}" below as the starting point.',
            [f"{_count(content.get('points'))} points, as a {content.get('chart_type')} chart."],
            "Observed totals, not an estimate.",
            "Totals include every period in the data.",
            "Ask about the highest or lowest point.",
        )

    def _forecast_answer(self, results: Results) -> str:
        forecasts = results["get_forecast"].get("forecasts", [])
        if not forecasts:
            return _sections(
                "The data cannot answer this.",
                ["No forecast was produced."],
                "Not available.",
                "Too little history.",
                "Upload more history.",
            )
        total = forecasts[0]
        lines = []
        for i, period in enumerate((total.get("periods") or [])[:3]):
            lines.append(
                f"{period}: {_money(total['point'][i])} (80% range {_money(total['lo80'][i])} to "
                f"{_money(total['hi80'][i])})"
            )
        beat = "did not beat" if total.get("no_better_than_baseline") else "beat"
        return _sections(
            f"Plan around the forecast from the {total.get('model')} model, using its range, "
            "not just the point.",
            [
                str(total.get("summary") or total.get("title")),
                *lines,
                f"The model {beat} the seasonal-naive baseline in backtests "
                f"(confidence {total.get('confidence')}).",
            ],
            "; ".join(lines) or "Not available.",
            "Forecasts ignore promotions, holidays and external events.",
            "Compare actuals with the 80% range each month.",
        )

    def _findings_answer(self, results: Results) -> str:
        name = next(iter(results))
        content = results[name]
        insights = content.get("insights", [])
        why = [f"{i.get('title')}: {i.get('summary')}" for i in insights[:3]]
        if content.get("note"):
            why.append(str(content["note"]))
        topic = {"get_anomalies": "the unusual periods", "get_segments": "the customer segments"}
        return _sections(
            f"Review {topic.get(name, 'these findings')} below and act on the largest first.",
            why,
            "The tools report observed effects, not forecasts, so no range is given.",
            "Findings describe the past; check them against what you know happened.",
            "Drill into the top item with a SQL breakdown.",
        )

    def _churn_answer(self, results: Results) -> str:
        content = results["get_churn_risk"]
        why = [f"{i.get('title')}: {i.get('summary')}" for i in content.get("insights", [])[:2]]
        for item in (content.get("customers") or {}).get("items", [])[:3]:
            why.append(
                f"Customer {item.get('customer_key')}: churn probability "
                f"{_pct(item.get('probability'))}, risk band {item.get('risk_band')}."
            )
        return _sections(
            "Contact the highest-risk customers first with a retention offer.",
            why,
            "Revenue at stake is listed per customer in the evidence; results depend on the offer.",
            "Churn here means no purchase in the window; probabilities are bands, not certainties.",
            "Run a small retention test on the top-risk group and measure repeat purchases.",
        )

    def _what_if_answer(self, results: Results) -> str:
        scenarios = results["what_if"].get("scenarios", [])
        if not scenarios:
            return _sections(
                "The data cannot answer this.", ["No scenario result."], "n/a", "n/a", "n/a"
            )
        s = scenarios[0]
        revenue = s.get("revenue_change") or {}
        profit = s.get("profit_change") or {}
        if s.get("status") != "ok" or not revenue:
            return _sections(
                "The data cannot answer this reliably.",
                [
                    str(s.get("message") or s.get("summary")),
                    *[str(w) for w in s.get("warnings", [])[:2]],
                ],
                "No range: there is not enough evidence.",
                "Acting on an unreliable elasticity could cost revenue.",
                "Run a small price test to measure the response directly.",
            )
        target = s.get("target") or "all products"
        impact = (
            f"yearly revenue change {_money(revenue.get('expected'))} (range "
            f"{_money(revenue.get('low'))} to {_money(revenue.get('high'))})"
        )
        if profit:
            impact += (
                f"; yearly profit change {_money(profit.get('expected'))} (range "
                f"{_money(profit.get('low'))} to {_money(profit.get('high'))})"
            )
        verdict = (
            "go ahead with a limited test"
            if (revenue.get("low") or 0) > 0
            else "test it before rolling it out"
        )
        return _sections(
            f"For {target}, {verdict}.",
            [
                str(s.get("summary")),
                f"Confidence {s.get('confidence')}, "
                f"based on {_count(s.get('sample_size'))} periods.",
            ],
            impact,
            "; ".join(str(w) for w in (s.get("warnings") or [])[:2])
            or "The elasticity is observational: past prices may have followed demand.",
            "Run the change on part of the range for a few weeks and compare.",
        )

    def _plan_answer(self, results: Results) -> str:
        insights = results.get("get_insights", {}).get("insights", [])
        drivers = (results.get("explain_drivers") or {}).get("importance") or []
        scenarios = [
            s
            for s in (results.get("what_if") or {}).get("scenarios", [])
            if s.get("status") == "ok"
        ]
        why = [f"{i.get('title')}" for i in insights[:3]]
        if drivers:
            names = [
                str(d.get("feature") or d.get("name")) for d in drivers[:3] if isinstance(d, dict)
            ]
            why.append("Revenue goes together most with: " + ", ".join(names) + " (association).")
        best = None
        for s in scenarios:
            profit = (s.get("profit_change") or s.get("revenue_change") or {}).get("expected")
            if isinstance(profit, (int, float)) and (best is None or profit > best[0]):
                best = (profit, s)
        if best is None:
            return _sections(
                "Start with the top insights below; no lever scenario had enough evidence.",
                why,
                "No range available.",
                "Insights are observational.",
                "Ask a what-if question for one lever.",
            )
        s = best[1]
        change = s.get("profit_change") or s.get("revenue_change") or {}
        why.append(str(s.get("summary")))
        return _sections(
            f"Prioritise the {s.get('lever')} lever ({s.get('change_pct')}% on {s.get('scope')}), "
            "the scenario with the best expected profit change.",
            why,
            f"expected {_money(change.get('expected'))} per year "
            f"(range {_money(change.get('low'))} to {_money(change.get('high'))})",
            "Scenarios assume past price responses hold; drivers are associations, not causes.",
            "Test the change on a limited scope and measure it against the range.",
        )

    def _sql_answer(self, results: Results) -> str:
        content = results["run_sql"]
        rows = content.get("rows", [])
        lines = [f"{str(r[0])[:80]}: {_money(r[1]) if len(r) > 1 else ''}" for r in rows[:5]]
        return _sections(
            "Focus on the items at the top of this list.",
            [f"Query returned {_count(content.get('row_count'))} rows.", *lines],
            "Observed totals, not an estimate.",
            "Totals include every period in the data.",
            "Ask for a trend per item to see which are growing.",
        )

    def _python_answer(self, content: dict[str, Any]) -> str:
        if content.get("error"):
            return _sections(
                "The code could not run.",
                [f"{content.get('error')}: {content.get('message')}"],
                "No result.",
                "The sandbox refused or stopped the code; nothing outside the sandbox was touched.",
                "Rewrite the calculation with pandas on `df` only.",
            )
        result = json.dumps(content.get("result"), default=str)[:500]
        return _sections(
            "Use the computed result below.",
            [f"Sandbox result: {result}", *[str(n) for n in content.get("notes", [])]],
            "Computed directly from the data.",
            "Sampled data gives sample values (see notes).",
            "Check the code in the evidence panel.",
        )

    def _verifier_demo(self, results: Results) -> str:
        data = ((results.get("get_insights") or {}).get("insights") or [{}])[0].get("data", {})
        return _sections(
            "This answer deliberately contains an invented number to show the verifier.",
            [
                f"Net revenue {_money(data.get('net_revenue'))} (from the tool).",
                f"Invented figure with no source: {INVENTED_NUMBER}.",
            ],
            "None - demonstration only.",
            "The invented figure must be flagged.",
            "Check the caveat under this answer.",
        )

    @staticmethod
    def _summary(messages: list[ChatMessage]) -> str:
        questions = [m.content.strip()[:120] for m in messages[1:] if m.role == "user"]
        return "Earlier questions: " + "; ".join(questions[-10:]) if questions else ""


def _turn_state(messages: list[ChatMessage]) -> tuple[str, int, Results]:
    """Return the latest question, how many tool rounds followed it, and results by tool name."""
    last_user = max((i for i, m in enumerate(messages) if m.role == "user"), default=-1)
    question = messages[last_user].content if last_user >= 0 else ""
    names: dict[str, str] = {}
    rounds = 0
    results: Results = {}
    for message in messages[last_user + 1 :]:
        if message.role == "assistant" and message.tool_calls:
            rounds += 1
            names.update({c.id: c.name for c in message.tool_calls})
        elif message.role == "tool" and message.tool_call_id:
            parsed = _tool_json(message.content)
            if parsed is not None:
                results[names.get(message.tool_call_id, "unknown")] = parsed
    return question, rounds, results


def _tool_json(wrapped: str) -> dict[str, Any] | None:
    start = wrapped.find("\n")
    end = wrapped.rfind("\n<<<END_TOOL_OUTPUT>>>")
    if start < 0 or end < 0:
        return None
    try:
        parsed = json.loads(wrapped[start + 1 : end])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
