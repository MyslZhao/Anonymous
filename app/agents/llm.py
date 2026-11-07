"""Agent 决策层：真实 DeepSeek 调用 + 解析校验 + 失败降级。

约束：任何模型输出都必须先被解析成 Proposal，才可能进入规则引擎。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.agents.mock import mock_proposal
from app.agents.prompts import SYSTEM_PROMPTS, build_user_message
from app.agents.roles import role_issue
from app.config import settings
from app.engine.state import SimState
from app.models import Proposal

_SEMAPHORE = asyncio.Semaphore(settings.max_concurrency)


class AgentError(RuntimeError):
    """Agent 未能在重试后给出合法建议。"""


def extract_json(text: str) -> dict[str, Any]:
    """从模型输出中提取 JSON 对象，容忍代码块包裹与前后噪音。"""
    if not text:
        raise AgentError("空响应")
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AgentError("响应中找不到 JSON 对象")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AgentError(f"JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise AgentError("JSON 顶层不是对象")
    return data


def parse_proposal(text: str, role: str) -> Proposal:
    data = extract_json(text)
    data["agent"] = role  # 角色以调用方为准，防止模型串角色
    return Proposal.model_validate(data)


ZONE_ACTIONS = {"send_medical_team", "dispatch_supply", "evacuate", "notify_students"}
ROAD_ACTIONS = {"close_road", "open_route"}


def shape_issue(state: SimState, proposal: Proposal) -> str | None:
    """契约层校验：区域/道路填反了要能立刻发现并让模型重来一次。

    这类错误很常见（模型把 close_road 的目标写成区域 id），
    与其让规则引擎静默驳回，不如带着反馈重试，提升 live 模式下的成功率。
    """
    if proposal.action in ROAD_ACTIONS and state.find_edge(proposal.target_area) is None:
        return (
            f"target_area 填了「{proposal.target_area}」，但 {proposal.action} 必须是道路名称，"
            "例如 2号道路、教学路、宿舍环线"
        )
    if proposal.action in ZONE_ACTIONS and proposal.target_area not in state.zones:
        return f"target_area 填了「{proposal.target_area}」，但 {proposal.action} 必须是区域 id，例如 dorm_a"
    return None


async def _chat(messages: list[dict], temperature: float = 0.2, max_tokens: int = 600) -> str:
    url = f"{settings.base_url}/chat/completions"
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=settings.timeout) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AgentError(f"响应结构异常：{body}") from exc


async def propose(
    role: str, state: SimState, focus: str | None = None, feedback: str | None = None
) -> tuple[Proposal, str]:
    """向指定角色索取一条结构化建议，返回 (proposal, 原始输出)。

    feedback 非空时表示“这是重新申请”：上一版建议被规则引擎驳回，附原因要求改一版。
    """
    if settings.mock_mode:
        return mock_proposal(role, state), "[mock] 离线规则生成"

    system = (
        f"{SYSTEM_PROMPTS[role]}\n角色 id 固定为 {role}。\n"
        "你是多 Agent 应急系统中的一个专业角色，输出会被程序严格校验，"
        "资源不足或道路不通的行动会被直接驳回。"
    )
    user_message = build_user_message(state, focus)
    if feedback:
        user_message += (
            f"\n\n【你的上一条建议被驳回】\n{feedback}\n"
            "请换一个当前确实可执行、资源不超过剩余库存的行动，重新输出一个 JSON 对象。"
        )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]

    async with _SEMAPHORE:
        last_error: Exception | None = None
        for _ in range(2):
            try:
                raw = await _chat(messages)
                proposal = parse_proposal(raw, role)
                issue = shape_issue(state, proposal) or role_issue(role, proposal.action)
                if issue:
                    raise AgentError(issue)
                return proposal, raw
            except (AgentError, httpx.HTTPError, ValueError) as exc:
                last_error = exc
                messages = messages[:2] + [
                    {
                        "role": "user",
                        "content": f"上一次输出不合法：{exc}。请仅输出合法 JSON 对象，不要任何其他文字。",
                    }
                ]
        raise AgentError(str(last_error))


async def propose_all(state: SimState, roles: list[str]) -> list[Proposal]:
    """并发获取全部角色建议；单个角色失败时降级为空建议而不拖垮整轮。"""
    results = await asyncio.gather(*(propose(role, state) for role in roles), return_exceptions=True)
    proposals: list[Proposal] = []
    for role, result in zip(roles, results):
        if isinstance(result, BaseException):
            proposals.append(
                Proposal(agent=role, action="idle", target_area="", reason=f"该角色本轮未给出有效建议（{result}）")
            )
        else:
            proposals.append(result[0])
    return proposals
