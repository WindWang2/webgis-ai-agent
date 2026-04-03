/**
 * Mock Chat API - 用于本地开发和后端未就绪时
 */
import type { ChatMessage, ChatSession } from '../types/chat';

// 内存存储
let _sessions: Map<string, ChatSession> = new Map();

function _gen_id(): string {
  return Math.random().toString(36).substring(2, 15);
}

function _generate_response(message: string): string {
  const lower = message.toLowerCase();
  
  if (lower.includes('buffer') || lower.includes('缓冲')) {
    return `以下是缓冲区分析的Python代码示例：

\`\`\`python
from shapely.geometry import Point

# 创建点对象
point = Point(0, 0)

# 创建1000米的缓冲区（约0.009度）
buffer = point.buffer(0.009)

print(f"缓冲区面积: {buffer.area}")
\`\`\`

您可以在图层面板中上传数据并执行Buffer分析。`;
  }
  
  if (lower.includes('clip') || lower.includes('裁剪')) {
    return `裁剪分析的代码示例：

\`\`\`python
import geopandas as gpd

vector_layer = gpd.read_file("data.geojson")
clipped = vector_layer.clip(box_bounds)
clipped.to_file("clipped_output.geojson")
\`\`\``;
  }

  if (lower.includes('intersect') || lower.includes('相交')) {
    return `空间相交分析的代码：

\`\`\`python
import geopandas as gpd

layer1 = gpd.read_file("layer1.geojson")
layer2 = gpd.read_file("layer2.geojson")

result = layer1.overlay(layer2, how="intersect")
print(f"相交要素数量: {len(result)}")
\`\`\``;
  }

  if (lower.includes('统计') || lower.includes('面积') || lower.includes('周长')) {
    return `统计分析功能可以计算：

\`\`\`python
import geopandas as gpd

layer = gpd.read_file("my_layer.geojson")

layer["area"] = layer.geometry.area
layer["perimeter"] = layer.geometry.length

stats = {
    "总要素数": len(layer),
    "总面积": layer["area"].sum(),
    "平均面积": layer["area"].mean()
}
print(stats)
\`\`\``;
  }

  if (lower.includes('帮助') || lower.includes('能做什么')) {
    return `我可以帮您进行以下GIS操作：

1. **缓冲区分析 (Buffer)** - 创建指定距离的缓冲区
2. **裁剪分析 (Clip)** - 用一个图层裁剪另一个图层
3. **相交分析 (Intersect)** - 找出两个图层的交集
4. **融合分析 (Dissolve)** - 合并相同属性的要素
5. **联合分析 (Union)** - 合并两个图层
6. **统计分析** - 计算面积、周长、要素数量等

请告诉我您想执行的分析操作！`;
  }

  return `收到您的消息：「${message}」

我是WebGIS AI助手，可以帮您：
- 执行空间分析（Buffer、Clip、Intersect、Dissolve、Union）
- 管理图层（上传、查询、导出）
- 解答GIS相关问题

请问有什么可以帮您的？`;
}

// 模拟延迟
function _delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * 发送聊天消息
 */
export async function sendChatMessage(
  message: string,
  sessionId?: string
): Promise<{
  session_id: string;
  message: string;
  timestamp: number;
}> {
  await _delay(300 + Math.random() * 200);
  
  const now = Date.now();
  
  let session: ChatSession;
  if (sessionId && _sessions.has(sessionId)) {
    session = _sessions.get(sessionId)!;
  } else {
    session = {
      id: _gen_id(),
      title: message.slice(0, 30) + (message.length > 30 ? '...' : ''),
      messages: [],
      createdAt: now,
      updatedAt: now,
    };
    _sessions.set(session.id, session);
  }

  const userMsg: ChatMessage = {
    id: _gen_id(),
    role: 'user',
    content: message,
    timestamp: now,
  };
  session.messages.push(userMsg);

  const replyContent = _generate_response(message);
  const aiMsg: ChatMessage = {
    id: _gen_id(),
    role: 'assistant',
    content: replyContent,
    timestamp: now + 1,
  };
  session.messages.push(aiMsg);
  session.updatedAt = now + 1;

  return {
    session_id: session.id,
    message: replyContent,
    timestamp: aiMsg.timestamp,
  };
}

/**
 * 获取会话历史列表
 */
export async function getSessionList(): Promise<Array<{
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
}>> {
  await _delay(100);
  
  const sessions = Array.from(_sessions.values()).map(s => ({
    id: s.id,
    title: s.title,
    created_at: s.createdAt,
    updated_at: s.updatedAt,
    message_count: s.messages.length,
  }));
  
  sessions.sort((a, b) => b.updated_at - a.updated_at);
  
  return sessions;
}

/**
 * 获取会话详细内容
 */
export async function getSessionDetail(sessionId: string): Promise<{
  id: string;
  title: string;
  messages: ChatMessage[];
  created_at: number;
  updated_at: number;
}> {
  await _delay(100);
  
  const session = _sessions.get(sessionId);
  if (!session) {
    throw new Error('会话不存在');
  }
  
  return {
    id: session.id,
    title: session.title,
    messages: session.messages,
    created_at: session.createdAt,
    updated_at: session.updatedAt,
  };
}

/**
 * 删除会话
 */
export async function deleteSession(sessionId: string): Promise<void> {
  await _delay(100);
  _sessions.delete(sessionId);
}

/**
 * 清空会话消息
 */
export async function clearSessionMessages(sessionId: string): Promise<void> {
  await _delay(100);
  const session = _sessions.get(sessionId);
  if (session) {
    session.messages = [];
    session.updatedAt = Date.now();
  }
}