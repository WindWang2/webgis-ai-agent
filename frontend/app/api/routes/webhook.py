"""
GitHub Webhook 路由
处理来自 GitHub 的 webhook 事件，用于 PR 审核流程
"""
import hashlib
import hmac
import logging
import re
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


class GitHubPullRequestEvent(BaseModel):
    """GitHub Pull Request 事件"""
    action: str  # opened, synchronize, closed, reopened
    number: int  # PR 编号
    pull_request: dict
    repository: dict
    sender: dict  # 触发者


class GitHubCheckRunEvent(BaseModel):
    """GitHub Check Run 事件（CI 结果）"""
    action: str  # completed, requested
    check_run: dict
    repository: dict
    sender: dict


def verify_github_signature(
    payload_body: bytes,
    signature_header: Optional[str]
) -> bool:
    """
    验证 GitHub webhook 签名
    
    Args:
        payload_body: 请求体字节
        signature_header: X-Hub-Signature-256 header 值
        
    Returns:
        签名是否有效
    """
    if not settings.GITHUB_WEBHOOK_SECRET:
        logger.warning("未配置 GITHUB_WEBHOOK_SECRET，跳过签名验证")
        return True
    
    if not signature_header:
        logger.warning("缺少 X-Hub-Signature-256 header")
        return False
    
    # 计算 HMAC-SHA256
    sha_name, signature = signature_header.split("=")
    if sha_name != "sha256":
        logger.warning(f"不支持的签名算法: {sha_name}")
        return False
    
    computed_sig = hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode(),
        payload_body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(computed_sig, signature)


@router.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
    x_github_event: Optional[str] = Header(None, alias="X-GitHub-Event")
):
    """
    GitHub Webhook 端点
    
    支持的事件：
    - pull_request: PR 创建、更新、合并
    - check_run: CI/CD 检查完成
    - pull_request_review: 审核评论
    """
    # 验证签名
    body = await request.body()
    if not verify_github_signature(body, x_hub_signature_256):
        logger.warning("GitHub webhook 签名验证失败")
        raise HTTPException(status_code=403, detail="Invalid signature")
    
    # 获取事件类型
    event_type = x_github_event or "unknown"
    logger.info(f"收到 GitHub webhook 事件: {event_type}")
    
    # 处理不同事件
    handlers = {
        "pull_request": _handle_pull_request,
        "check_run": _handle_check_run,
        "pull_request_review": _handle_pull_request_review,
    }
    
    handler = handlers.get(event_type)
    if handler:
        try:
            import json
            payload = json.loads(body)
            result = await handler(payload, event_type)
            return {"status": "ok", "event": event_type, "result": result}
        except Exception as e:
            logger.exception(f"处理 {event_type} 事件失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    else:
        logger.info(f"忽略未处理的事件类型: {event_type}")
        return {"status": "ignored", "event": event_type}


async def _handle_pull_request(payload: dict, event_type: str) -> dict:
    """
    处理 pull_request 事件
    """
    action = payload.get("action")
    pr_data = payload.get("pull_request", {})
    repo_data = payload.get("repository", {})
    
    pr_number = pr_data.get("number")
    repo_full_name = repo_data.get("full_name", "")
    
    logger.info(f"PR 事件: {action} - #{pr_number} @ {repo_full_name}")
    
    # 处理 PR 动作
    if action == "opened" or action == "reopened":
        # PR 创建或重新打开 - 触发检查
        return await _trigger_pr_checks(pr_data, repo_data, payload)
    
    elif action == "synchronize":
        # PR 有新提交 - 重新检查
        return await _trigger_pr_checks(pr_data, repo_data, payload)
    
    elif action == "closed":
        # PR 关闭（可能是合并）
        if pr_data.get("merged"):
            return await _handle_pr_merged(pr_data, repo_data)
        return {"action": "closed_not_merged"}
    
    return {"action": action, "handled": True}


async def _handle_check_run(payload: dict, event_type: str) -> dict:
    """
    处理 check_run 事件（CI 检查完成）
    """
    action = payload.get("action")
    check_data = payload.get("check_run", {})
    repo_data = payload.get("repository", {})
    
    # 目前我们不做额外处理，等待 PR Review
    logger.info(f"Check Run 事件: {action} - {check_data.get('name')}")
    
    return {"action": action, "check_name": check_data.get("name")}


async def _handle_pull_request_review(payload: dict, event_type: str) -> dict:
    """
    处理 pull_request_review 事件
    """
    action = payload.get("action")
    review_data = payload.get("review", {})
    pr_data = payload.get("pull_request", {})
    repo_data = payload.get("repository", {})
    
    logger.info(f"Review 事件: {action} - PR #{pr_data.get('number')}")
    
    # 可以在这里处理审核结果的记录
    return {"action": action}


async def _trigger_pr_checks(
    pr_data: dict,
    repo_data: dict,
    original_payload: dict
) -> dict:
    """
    触发 PR 检查流程
    """
    if not settings.ENABLE_PR_CHECK:
        logger.info("PR 检查已禁用")
        return {"skipped": "pr_check_disabled"}
    
    # 动态导入避免循环依赖
    from app.services.pr_check_flow import trigger_full_pr_check
    from app.services.pr_workflow.reviewer import assign_reviewer, parse_pr_file, detect_module
    from app.services.feishu_notifier import send_pr_created
    
    pr_number = pr_data.get("number")
    pr_title = pr_data.get("title", "")
    pr_url = pr_data.get("html_url", "")
    author = pr_data.get("user", {}).get("login", "")
    repo_full_name = repo_data.get("full_name", "")
    
    logger.info(f"触发 PR 检查: #{pr_number} - {repo_full_name}")
    
    try:
        # 1. 执行自动检查
        check_result = trigger_full_pr_check(pr_data, repo_data)
        
        # 2. 分配审核人
        if settings.PR_AUTO_ASSIGN_REVIEWER:
            files = parse_pr_file(pr_number) if pr_number else []
            module = detect_module(files) if files else "general"
            assignee_result = assign_reviewer(pr_number, module)
            reviewer = assignee_result.get("reviewer", "")
            
            # 发送飞书通知
            if settings.ENABLE_FEISHU_NOTIFY:
                send_pr_created(pr_number, pr_title, pr_url, author, reviewer)
        else:
            reviewer = ""
        
        return {
            "action": "checks_completed",
            "pr_number": pr_number,
            "check_passed": check_result.passed,
            "reviewer_assigned": bool(reviewer),
            "comment_posted": bool(check_result.comment_body)
        }
    except Exception as e:
        logger.exception(f"PR 检查流程异常: {e}")
        return {"action": "checks_failed", "error": str(e)}


async def _handle_pr_merged(pr_data: dict, repo_data: dict) -> dict:
    """
    处理 PR 合并事件
    """
    from app.services.pr_workflow.merge import on_pr_merged
    from app.services.feishu_notifier import send_pr_merged
    from app.services.pr_check_flow import post_pr_comment

    pr_number = pr_data.get("number")
    pr_title = pr_data.get("title", "")
    merged_by = pr_data.get("merged_by", {}).get("login", "未知")
    repo_full_name = repo_data.get("full_name", "")

    logger.info(f"PR 已合并: #{pr_number} - {repo_full_name}")

    # 1. 执行合并后操作
    merge_result = on_pr_merged(pr_number, repo_full_name, merged_by)

    # 2. 发送飞书通知
    if settings.PR_ENABLE_MERGE_NOTIFICATION and settings.ENABLE_FEISHU_NOTIFY:
        send_pr_merged(pr_number, pr_title, merged_by)

    # 3. 发布合并通知评论
    comment = f"✅ 此 PR 已由 {merged_by} 合并"
    post_pr_comment(pr_number, comment)

    return {
        "action": "merged",
        "pr_number": pr_number,
        "repo": repo_full_name,
        "merge_result": merge_result
    }


__all__ = ["router"]