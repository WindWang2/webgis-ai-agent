"""
GitHub Issue Webhook 路由
处理来自 GitHub 的 Issue 事件，用于 Issue 管理和分配流程
"""
import hashlib
import hmac
import logging
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel
from app.core.config import settings
logger = logging.getLogger(__name__)
router = APIRouter()


class GitHubIssueEvent(BaseModel):
    """GitHub Issue 事件"""
    action: str  # opened, closed, reopened, edited, labeled, etc.
    issue: dict
    repository: dict
    sender: dict  # 触发者
    label: Optional[dict] = None  # 当 action=labeled 时有值


def verify_github_signature(
    payload_body: bytes,
    signature_header: Optional[str]
) -> bool:
    """
    验证 GitHub webhook 签名
    
    Args:
        payload_body: 请求体字节
        signature_header: X- Hub-Signature-256 header 值
        
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


@router.post("/webhook/github/issues")
async def github_issue_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
    x_github_event: Optional[str] = Header(None, alias="X-GitHub-Event")
):
    """
    GitHub Issue Webhook 端点
    
    支持的事件：
    - issue: Issue 创建、更新、关闭等
    """
    # 验证签名
    body = await request.body()
    if not verify_github_signature(body, x_hub_signature_256):
        logger.warning("GitHub webhook 签名验证失败")
        raise HTTPException(status_code=403, detail="Invalid signature")
    
    # 获取事件类型
    event_type = x_github_event or "unknown"
    logger.info(f"收到 GitHub Issue webhook 事件: {event_type}")
    
    # 只处理 issue 事件
    if event_type != "issues":
        logger.info(f"忽略非 Issue 事件类型: {event_type}")
        return {"status": "ignored", "event": event_type}
    
    try:
        import json
        payload = json.loads(body)
        result = await _handle_issue_event(payload)
        return {"status": "ok", "event": event_type, "result": result}
    except Exception as e:
        logger.exception(f"处理 Issue 事件失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _handle_issue_event(payload: dict) -> dict:
    """
    处理 issue 事件
    
    Supported actions:
    - opened: Issue 创建
    - reopened: Issue 重新打开
    - closed: Issue 关闭
    - edited: Issue 编辑
    - labeled: 添加标签
    - unlabeled: 移除标签
    """
    action = payload.get("action")
    issue_data = payload.get("issue", {})
    repo_data = payload.get("repository", {})
    
    issue_number = issue_data.get("number")
    repo_full_name = repo_data.get("full_name", "")
    
    logger.info(f"Issue 事件: {action} - #{issue_number} @ {repo_full_name}")
    
    # Issue 创建或重新打开 - 触发自动分类和分配
    if action == "opened" or action == "reopened":
        return await _trigger_issue_processing(issue_data, repo_data)
    
    # Issue 关闭 - 发送通知
    elif action == "closed":
        return await _handle_issue_closed(issue_data, repo_data)
    
    # 其他事件暂不处理
    return {"action": action, "handled": True}


async def _trigger_issue_processing(
    issue_data: dict,
    repo_data: dict
) -> dict:
    """
    触发 Issue 处理流程：
    
    1. 自动分类（bug/feature/enhancement/documentation/question/refactor）
    2. 优先级判定（critical/high/medium/low）
    3. 分配给对应负责人（coder/researcher/academic）
    4. 添加标签并发布评论
    
    Args:
        issue_data: Issue 数据字典
        repo_data: 仓库数据字典
        
    Returns:
        处理结果字典
    """
    from app.services.issue_check_flow import trigger_full_issue_check
    
    if not settings.ENABLE_ISSUE_CHECK:
        logger.info("Issue 检查已禁用")
        return {"skipped": "issue_check_disabled"}
    
    issue_number = issue_data.get("number")
    logger.info(f"触发 Issue 处理: #{issue_number}")
    
    try:
        # 执行完整的 Issue 检查流程
        check_result = trigger_full_issue_check(issue_data, repo_data)
        
        logger.info(
            f"Issue #{issue_number} 处理完成: "
            f"category={check_result.category}, "
            f"priority={check_result.priority}, "
            f"role={check_result.assignee_role}"
        )
        
        return {
            "action": "processed",
            "issue_number": issue_number,
            "category": check_result.category,
            "priority": check_result.priority,
            "assignee_role": check_result.assignee_role,
            "labels_added": check_result.label_added,
        }
    
    except Exception as e:
        logger.exception(f"Issue 处理流程异常: {e}")
        return {"action": "processing_failed", "error": str(e)}


async def _handle_issue_closed(
    issue_data: dict,
    repo_data: dict
) -> dict:
    """
    处理 Issue 关闭事件
    """
    from app.services.feishu_notification import send_issue_closed
    
    issue_number = issue_data.get("number")
    issue_title = issue_data.get("title", "")
    closed_by = issue_data.get("user", {}).get("login", "")
    
    # 检查是否有关联的 PR（表示可能被合并）
    pull_request = issue_data.get("pull_request")
    resolution = ""
    
    if pull_request:
        resolution = "通过关联 PR 合并解决"
    
    # 发送飞书通知
    if settings.ENABLE_FEISHU_NOTIFY and settings.ENABLE_ISSUE_NOTIFY:
        send_issue_closed(issue_number, issue_title, closed_by, resolution)
    
    return {
        "action": "closed",
        "issue_number": issue_number,
        "closed_by": closed_by,
    }


__all__ = ["router"]