"""飞书通知服务"""
import logging
import json
import httpx
from app.core.config import settings
logger = logging.getLogger(__name__)
def send_feishu_notification(content: str, title: str = "PR 审核通知") -> bool:
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
                logger.info("飞书通知发送成功")
                return True
            else:
                logger.error(f"飞书通知发送失败: {res.status_code} - {res.text}")
                return False
    except Exception as e:
        logger.error(f"发送飞书通知异常: {str(e)}")
        return False
def send_pr_created(pr_number: int, pr_title: str, pr_url: str, author: str, reviewer: str = "") -> bool:
    """发送 PR 创建通知"""
    content = f"**PR #{pr_number}: {pr_title}**\\n\\n作者: {author}\\n审核人: {reviewer}\\n地址: [{pr_url}]({pr_url})"
    return send_feishu_notification(content, "📥 新 PR 待审核")
def send_pr_check_failed(pr_number: int, pr_title: str, errors: list) -> bool:
    """发送 PR 检查失败通知"""
    content = f"**PR #{pr_number}: {pr_title}** 自动检查未通过\\n\\n错误信息: {', '.join(errors)}"
    return send_feishu_notification(content, "❌ PR 检查失败")
def send_pr_timeout(pr_number: int, pr_title: str, hours: int) -> bool:
    """发送 PR 超时提醒"""
    content = f"**PR #{pr_number}: {pr_title}** 已等待 {hours} 小时未审核\\n请尽快处理！"
    return send_feishu_notification(content, "⏰ PR 审核超时提醒")
def send_pr_merged(pr_number: int, pr_title: str, merged_by: str) -> bool:
    """发送 PR 合并通知"""
    content = f"**PR #{pr_number}: {pr_title}** 已合并\\n合并人: {merged_by}"
    return send_feishu_notification(content, "✅ PR 已合并")
__all__ = [
    "send_feishu_notification",
    "send_pr_created",
    "send_pr_check_failed",
    "send_pr_timeout",
    "send_pr_merged"
]