"""
飞书通知服务
支持 PR 审核和 Issue 管理的通知功能
"""
import logging
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def send_feishu_notification(
    content: str,
    title: str = "WebGIS AI 通知"
) -> bool:
    """
    发送飞书通知到群聊
    
    Args:
        content: 消息内容
        title: 消息标题
        
    Returns:
        发送是否成功
    """
    if not settings.ENABLE_FEISHU_NOTIFY:
        logger.info("飞书通知已禁用，跳过发送")
        return False
    
    webhook = settings.FEISHU_WEBHOOK_URL
    if not webhook:
        logger.warning("未配置 FEISHU_WEBHOOK_URL，无法发送通知")
        return False
    
    # 构建飞书卡片消息
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title
                }
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": content
                }
            ]
        }
    }
    
    try:
        with httpx.Client(timeout=10) as client:
            res = client.post(
                webhook,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            if res.status_code == 200:
                logger.info(f"飞书通知发送成功: {title}")
                return True
            else:
                logger.error(f"飞书通知发送失败: {res.status_code} - {res.text}")
                return False
    except Exception as e:
        logger.error(f"发送飞书通知异常: {str(e)}")
        return False


# ============ PR 相关通知函数 ============

def send_pr_created(
    pr_number: int,
    pr_title: str,
    pr_url: str,
    author: str,
    reviewer: str = ""
) -> bool:
    """发送 PR 创建通知"""
    content = (
        f"**📥 PR #{pr_number}: {pr_title}**\n\n"
        f"👤 作者: {author}\n"
        f"👨‍🔍 审核人: {reviewer or '待分配'}\n"
        f"🔗 地址: [{pr_url}]({pr_url})"
    )
    return send_feishu_notification(content, "📥 新 PR 待审核")


def send_pr_check_failed(pr_number: int, pr_title: str, errors: list) -> bool:
    """发送 PR 检查失败通知"""
    content = (
        f"**❌ PR #{pr_number}: {pr_title}**\n\n"
        f"自动检查未通过 ⚠️\n\n"
        f"错误信息: {', '.join(errors)}"
    )
    return send_feishu_notification(content, "❌ PR 检查失败")


def send_pr_timeout(pr_number: int, pr_title: str, hours: int) -> bool:
    """发送 PR 超时提醒"""
    content = (
        f"**⏰ PR #{pr_number}: {pr_title}**\n\n"
        f"已等待 {hours} 小时未审核\n"
        f"请尽快处理！"
    )
    return send_feishu_notification(content, "⏰ PR 审核超时提醒")


def send_pr_merged(pr_number: int, pr_title: str, merged_by: str) -> bool:
    """发送 PR 合并通知"""
    content = (
        f"**✅ PR #{pr_number}: {pr_title}**\n\n"
        f"已合并 ✓\n"
        f"👤 合并人: {merged_by}"
    )
    return send_feishu_notification(content, "✅ PR 已合并")


# ============ Issue 相关通知函数 ============

def send_issue_created(
    issue_number: int,
    issue_title: str,
    author: str,
    category: str = "",
    priority: str = "",
    assignee_role: str = "",
) -> bool:
    """
    发送 Issue 创建通知
    
    Args:
        issue_number: Issue 编号
        issue_title: Issue 标题
        author: 作者
        category: 分类
        priority: 优先级
        assignee_role: 分配角色
        
    Returns:
        发送是否成功
    """
    category_icon = {
        "bug": "🐛",
        "feature": "✨",
        "enhancement": "🚀",
        "documentation": "📝",
        "question": "❓",
        "refactor": "🔧",
    }.get(category, "📋")
    
    priority_emoji = {
        "critical": "🔴",
        "high": "🟠",
        "medium": "🟡",
        "low": "🟢",
    }.get(priority, "⚪")
    
    content = (
        f"**📥 Issue #{issue_number}: {issue_title}**\n\n"
        f"👤 作者: {author}\n"
        f"📂 分类: {category_icon} {category or '未分类'}\n"
        f"⭐ 优先级: {priority_emoji} {priority or '普通'}"
    )
    
    if assignee_role:
        content += f"\n👨‍💼 负责角色: {assignee_role}"
    
    return send_feishu_notification(content, "📥 新 Issue 待处理")


def send_issue_assigned(
    issue_number: int,
    issue_title: str,
    assignee: str,
    reason: str = "",
) -> bool:
    """
    发送 Issue 分配通知
    
    Args:
        issue_number: Issue 编号
        issue_title: Issue 标题
        assignee: 被分配人
        reason: 分配原因（如分类依据）
        
    Return:
        发送是否成功
    """
    content = (
        f"**📌 Issue #{issue_number}: {issue_title}**\n\n"
        f"👨‍💼 已分配给: @{assignee}\n"
    )
    
    if reason:
        content += f"📋 原因: {reason}\n"
    
    return send_feishu_notification(content, "📌 Issue 已分配")


def send_issue_closed(
    issue_number: int,
    issue_title: str,
    closed_by: str,
    resolution: str = "",
) -> bool:
    """
    发送 Issue 关闭通知
    
    Args:
        issue_number: Issue 编号
        issue_title: Issue 标题
        closed_by: 关闭人
        resolution: 解决方案（可选）
        
    Return:
        发送是否成功
    """
    content = (
        f"**✅ Issue #{issue_number}: {issue_title}**\n\n"
        f"已关闭 ✓\n"
        f"👤 关闭人: {closed_by}"
    )
    
    if resolution:
        content += f"\n💡 解决方案: {resolution}"
    
    return send_feishu_notification(content, "✅ Issue 已关闭")


# ============ 任务版相关通知函数 ============

def send_task_updated(
    task_id: str,
    task_title: str,
    old_status: str,
    new_status: str,
    updated_by: str = "",
) -> bool:
    """
    发送任务状态更新通知
    
    Args:
        task_id: 任务 ID
        task_title: 任务标题
        old_status: 原状态
        new_status: 新状态
        updated_by: 更新人
        
    Return:
        发送是否成功
    """
    content = (
        f"**🔄 任务状态更新**\n\n"
        f"📝 任务: {task_title}\n"
        f"📌 {old_status} → {new_status}"
    )
    
    if updated_by:
        content += f"\n👤 更新人: {updated_by}"
    
    return send_feishu_notification(content, "🔄 任务状态更新")


__all__ = [
    "send_feishu_notification",
    "send_pr_created",
    "send_pr_check_failed",
    "send_pr_timeout",
    "send_pr_merged",
    "send_issue_created",
    "send_issue_assigned",
    "send_issue_closed",
    "send_task_updated",
]