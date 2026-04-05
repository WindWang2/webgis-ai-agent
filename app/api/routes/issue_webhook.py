"""
GitHub Issue Webhook Router
Handles GitHub Issue events for automated management workflows.
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
    action: str
    issue: dict
    repository: dict
    sender: dict
    label: Optional[dict] = None

_STATUS_LABELS = {
    "opened": ("待处理", "处理中"),
    "reopened": ("已关闭", "重新打开"),
    "closed": ("处理中", "已关闭"),
    "assigned": ("待分配", "已分配"),
    "unassigned": ("已分配", "待分配"),
}

def _get_status_labels(action: str) -> tuple[str, str]:
    pair = _STATUS_LABELS.get(action, ("-", "-"))
    return pair[0], pair[1]

def verify_signature(payload_body: bytes, sig_header: Optional[str]) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not settings.GITHUB_WEBHOOK_SECRET:
        logger.error("No GITHUB_WEBHOOK_SECRET configured, rejecting request")
        return False
    if not sig_header:
        return False
    _, sig = sig_header.split("=", 1)
    expected = hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode(),
        payload_body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig)

@router.post("/webhook/github/issues")
async def handle_github_issue_event(
    request: Request,
    x_hub_sig: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
    x_gh_event: Optional[str] = Header(None, alias="X-GitHub-Event"),
):
    """
    GitHub Issues Webhook endpoint.
    Handles: opened, reopened, closed, assigned, etc.
    """
    body = await request.body()
    if not verify_signature(body, x_hub_sig):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    action = payload.get("action", "")
    issue_data = payload.get("issue", {})
    repo_data = payload.get("repository", {})
    issue_number = issue_data.get("number", 0)

    logger.info(f"Issue event: {action} #{issue_number}")

    # Sync state locally
    await _sync_state_to_tracker(action, issue_number, issue_data)

    # Route by action
    if action in ("opened", "reopened"):
        return await _trigger_processing(issue_data, repo_data)
    elif action == "closed":
        return await _handle_closed(issue_data)
    elif action == "assigned":
        return await _handle_assigned(issue_data)
    
    return {"action": action, "status": "ok"}

async def _sync_state_to_tracker(action: str, issue_num: int, issue_dat: dict):
    """Sync Issue status change to local DB, notify via Feishu."""
    from app.services.issue_state_sync import sync_issue_state
    from app.services.issue_tracker_db import get_tracker

    try:
        tr = get_tracker()
        pl = {"action": action, "issue": issue_dat}
        changed = sync_issue_state(issue_num, action, pl, tr)
        
        if changed and settings.ENABLE_FEISHU_NOTIFY and settings.ENABLE_ISSUE_NOTIFY:
            from app.services.feishu_notification import send_feishu_notification
            old_lbl, new_lbl = _get_status_label(action)
            msg = (
                f"**🔄 Issue #{issue_num} 状态变更**\n\n"
                f"动作: {action}\n{old_lbl} → {new_lbl}"
            )
            send_feishu_notification(msg, f"🔄 Issue #{issue_num}")
    except Exception as e:
        logger.warning(f"Sync failed: {e}")

async def _trigger_processing(issue_dt: dict, repo_dt: dict) -> dict:
    """Trigger auto classification, assignment, labeling."""
    from app.services.issue_check_flow import trigger_full_issue_check
    
    if not settings.ENABLE_ISSUE_CHECK:
        return {"skipped": True}
    
    num = issue_dt.get("number", 0)
    try:
        res = trigger_full_issue_check(issue_dt, repo_dt)
        return {
            "action": "processed",
            "number": num,
            "category": res.category,
            "priority": res.priority,
            "role": res.assignee_role,
            "labels": res.label_added,
        }
    except Exception as e:
        logger.exception(f"Processing error: {e}")
        return {"error": str(e)}

async def _handle_closed(issue_dt: dict) -> dict:
    """Handle Issue closed event."""
    from app.services.feishu_notification import send_issue_closed
    
    num = issue_dt.get("number", 0)
    title = issue_dt.get("title", "")
    by = issue_dt.get("user", {}).get("login", "")
    resol = "via merged PR" if issue_dt.get("pull_request") else ""
    
    if settings.ENABLE_FEISHU_NOTIFY and settings.ENABLE_ISSUE_NOTIFY:
        send_issue_closed(num, title, by, resol)
    
    return {"action": "closed", "number": num}

async def _handle_assigned(issue_dt: dict) -> dict:
    """Handle Issue assigned event."""
    from app.services.feishu_notification import send_issue_assigned
    
    num = issue_dt.get("number", 0)
    title = issue_dt.get("title", "")
    assigns = issue_dt.get("assignees", [])
    who = assigns[0].get("login", "") if assigns else ""
    
    if settings.ENABLE_FEISHU_NOTIFY and settings.ENABLE_ISSUE_NOTIFY:
        send_issue_assigned(num, title, who)
    
    return {"action": "assigned", "number": num, "to": who}

# Internal endpoints for cron
@router.post("/internal/check-issue-timeouts")
async def check_timeouts():
    """Manually trigger timeout check (call from cron/Celery)."""
    from app.services.celery_issue_tasks import check_all_issues_timeouts
    return check_all_issues_timeouts()

@router.get("/stats/weekly-issue")
async def get_stats():
    """Get current week's Issue statistics."""
    from app.services.issue_stats import generate_weekly_issue_stats
    cont, tit = generate_weekly_issue_stats()
    return {"title": tit, "content": cont}

__all__ = ["router"]