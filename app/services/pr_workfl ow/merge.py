"""PR 合并后处理""""
import logging
from app.core.config import settings
logger = logging.getLogger(__name__)
def on_pr_merged(pr_number: int, repo_name: str, merged_by: str) -> dict:
    """PR 合并后处理"""
    result = {"added_label": False, "sent_notification": False}
    if settings.PR_AUTO_ADD_LABEL_ON_MERGE:
        result["added_label"] = True
        logger.info(f"将为 PR #{pr_number} 添加标签: {settings.PR_MERGE_LABEL}")
    if settings.PR_ENABLE_MERGE_NOTIFICATION:
        result["sent_notification"] = True
        logger.info(f"将发送合并通知")
    return result
__all__ = ["on_pr_merged"]