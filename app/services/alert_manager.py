"""
Alert Manager - 告警通知模块
支持多种告警通道：Email、Webhook、飞书等
"""
import os
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    """告警数据类"""
    severity: str  # critical/warning/info
    title: str
    description: str
    labels: Optional[Dict[str, str]] = None
    annotations: Optional[Dict[str, str]] = None
    firing: bool = True  # True=触发, False=解决

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "firing": self.firing,
        }
        if self.label:
            result["labels"] = self.label
        if self.annotations:
            result["annotations"] = self.annotations
        return result


class AlertChannel(ABC):
    """告警通道抽象基类"""

    @abstractmethod
    def send(self, alert: Alert) -> bool:
        """发送告警，返回是否成功"""
        pass


class FeishuWebhookChannel(AlertChannel):
    """飞书 Webhook 告警通道"""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, alert: Alert) -> bool:
        """通过飞书 Webhook 发送告警"""
        if not self.webhook_url or self.webhook_url == "[SET_HERE]":
            logger.warning("飞书 Webhook 未配置，跳过告警")
            return False

        severity_icon = {
            "critical": "🔴",
            "warning": "🟡",
            "info": "🔵"
        }.get(alert.severity, "⚪")

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"{severity_icon} {alert.title}"
                    },
                    "template": {
                        "critical": "red",
                        "warning": "orange",
                        "info": "blue"
                    }.get(alert.severity, "grey")
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "plain_text",
                            "content": alert.description[:500]
                        }
                    },
                    {
                        "tag": "div",
                        "fields": [
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "plain_text",
                                    "content": f"**Severity:** {alert.severity.upper()}"
                                }
                            },
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "plain_text",
                                    "content": f"**Status:** {'触发' if alert.firing else '已解决'}"
                                }
                            }
                        ]
                    }
                ],
                "footer": {
                    "tag": "plain_text",
                    "content": f"WebGIS AI Agent · {alert.label or {}}"
                }
            }
        }

        try:
            import urllib.request
            req = urllib.request.Request(
                self.webhook_url,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"飞书告警发送失败: {e}")
            return False


class EmailChannel(AlertChannel):
    """邮件告警通道"""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        username: str,
        password: str,
        to_addrs: list[str],
        use_tls: bool = True
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.to_addrs = to_addrs
        self.use_tls = use_tls

    def send(self, alert: Alert) -> bool:
        """发送邮件告警"""
        # 构造邮件
        msg = MIMEMultipart()
        msg['From'] = self.username
        msg['To'] = ', '.join(self.to_addrs)
        
        severity = alert.severity.upper()
        subject_prefix = {
            "critical": "[CRITICAL]",
            "warning": "[WARNING]",
            "info": "[INFO]"
        }.get(severity.lower(), "")
        
        msg['Subject'] = f"{subject_prefix} WebGIS Alert: {alert.title}"

        body = f"""
{alert.title}
{'=' * len(alert.title)}

严重程度: {severity}
状态: {'触发中' if alert.firing else '已解决'}

描述:
{alert.description}

{"标签:" + json.dumps(alert.label) if alert.label else ""}
{"注解:" + json.dumps(alert.annotations) if alert.annotations else ""}

---
WebGIS AI Agent 告警系统
"""
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        try:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
            if self.use_tls:
                server.starttls()
            server.login(self.username, self.password)
            server.sendmail(self.username, self.to_addrs, msg.as_string())
            server.quit()
            logger.info(f"邮件告警发送成功: {alert.title}")
            return True
        except Exception as e:
            logger.error(f"邮件告警发送失败: {e}")
            return False


class MultiChannelManager:
    """多通道告警管理器"""

    def __init__(self):
        self.channels: list[AlertChannel] = []
        self.enabled = os.environ.get("ENABLE_ALERT", "true").lower() == "true"

        # 初始化飞书通道
        feishu_webhook = os.environ.get("FEISHU_WEBHOOK_URL", "")
        if feishu_webhook:
            self.channels.append(FeishuWebhookChannel(feishu_webhook))

        # 初始化邮件通道（如配置）
        smtp_host = os.environ.get("SMTP_HOST", "")
        if smtp_host:
            self.channel.extend([
                EmailChannel(
                    smtp_host=smtp_host,
                    smtp_port=int(os.environ.get("SMTP_PORT", "587")),
                    username=os.environ.get("SMTP_USERNAME", ""),
                    password=os.environ.get("SMTP_PASSWORD", ""),
                    to_addrs=os.environ.get("ALERT_EMAIL_TO", "").split(","),
                    use_tls=os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
                )
            ])

    def notify(self, alert: Alert) -> bool:
        """发送告警到所有已配置的通道"""
        if not self.enabled:
            logger.debug("告警功能未启用，跳过")
            return False

        logger.info(f"发送告警: [{alert.severity}] {alert.title}")
        
        success_count = 0
        for channel in self.channels:
            try:
                if channel.send(alert):
                    success_count += 1
            except Exception as e:
                logger.exception(f"通道 {channel.__class__.__name__} 发送失败: {e}")

        return success_count > 0

    def fire_critical(self, title: str, description: str, **kwargs) -> bool:
        """快捷方法：发送致命告警"""
        return self.notify(Alert(
            severity="critical",
            title=title,
            description=description,
            firing=True,
            **kwargs
        ))

    def fire_warning(self, title: str, description: str, **kwargs) -> bool:
        """快捷方法：发送警告告警"""
        return self.notify(Alert(
            severity="warning",
            title=title,
            description=description,
            firing=True,
            **kwargs
        ))


# 全局告警管理器
_alert_manager: Optional[MultiChannelManager] = None


def get_alert_manager() -> MultiChannelManager:
    """获取全局告警管理器实例（延迟初始化）"""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = MultiChannelManager()
    return _alert_manager


__all__ = [
    "Alert",
    "AlertChannel", 
    "FeishuWebhookChannel",
    "EmailChannel",
    "MultiChannelManager",
    "get_alert_manager"
]