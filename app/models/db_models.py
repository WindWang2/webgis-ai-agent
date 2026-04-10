"""数据库模型"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, Float, Boolean, JSON, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class UserRole(Base):
    __tablename__ = "user_roles"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id"))
    role = Column(String(50))


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    name = Column(String(100))
    email = Column(String(200))
    role = Column(String(50), default="user")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String, primary_key=True)
    title = Column(String(200), default="新对话")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String, ForeignKey("conversations.id"))
    role = Column(String(20), nullable=False)  # user / assistant / tool
    content = Column(Text, default="")
    tool_calls = Column(JSON, nullable=True)  # FC tool calls
    tool_call_id = Column(String, nullable=True)
    tool_result = Column(JSON, nullable=True)  # tool execution result
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    conversation = relationship("Conversation", back_populates="messages")


class Layer(Base):
    __tablename__ = "layers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    type = Column(String(50), nullable=False)  # geojson / raster / vector
    source = Column(String(100))  # osm / sentinel / upload
    data_path = Column(Text)  # 文件路径或 GeoJSON
    style = Column(JSON, nullable=True)  # MapLibre 样式
    visible = Column(Boolean, default=True)
    opacity = Column(Float, default=1.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200))
    type = Column(String(50))
    status = Column(String(50), default="pending")
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
