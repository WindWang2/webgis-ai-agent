/**
 * citation 共享渲染件测试（ADR-0145）。
 *
 * 覆盖三层契约：
 * 1. 解析：注入块剥离 / 字段提取 / 无引用文本零改动（零回归原则）；
 * 2. 预处理：只有「已知编号」的 [n] 变锚点链接；
 * 3. 渲染：MiniMd（chat 气泡真实渲染器）与 StoryMarkdown 的角标交互 +
 *    引用来源列表；无引用时两个渲染器行为与从前一致。
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MiniMd from './mini-md';
import StoryMarkdown from './story-markdown';
import { citeTextPreprocess, splitCitationBlocks } from './citation';

const INJECTED = [
  '请基于以下知识库片段回答：FAISS 是什么 [1][2]',
  '',
  '[1] 来源：《向量检索入门》 分块 chk_aaa111（L2 0.4210）',
  'FAISS 是 Facebook AI Research 开源的向量相似度检索库，',
  '支持 L2 与内积距离。',
  '',
  '[2] 来源：《RAG 架构》 分块 chk_bbb222（L2 0.7350）',
  'RAG 将检索与生成结合。',
].join('\n');

describe('splitCitationBlocks — 解析', () => {
  it('无定义块的文本逐字节原样返回（存量消息零回归）', () => {
    const text = '普通回答，带 [1] 与 [2026] 字面量。\n\n第二段。';
    expect(splitCitationBlocks(text)).toEqual({ body: text, sources: [] });
  });

  it('剥离定义块并提取编号/标题/分块/分数/摘录', () => {
    const { body, sources } = splitCitationBlocks(INJECTED);
    expect(sources).toHaveLength(2);
    expect(sources[0]).toMatchObject({
      n: 1,
      title: '向量检索入门',
      chunkId: 'chk_aaa111',
      scoreLabel: '0.4210',
    });
    expect(sources[0].excerpt).toContain('Facebook AI Research');
    expect(sources[1]).toMatchObject({ n: 2, chunkId: 'chk_bbb222' });
    expect(body).toContain('请基于以下知识库片段回答：FAISS 是什么');
    expect(body).not.toContain('来源：《向量检索入门》');
    expect(body).not.toContain('Facebook AI Research');
  });
});

describe('citeTextPreprocess — 预处理', () => {
  it('已知编号转锚点链接，未知编号保留原样', () => {
    const { body, sources } = splitCitationBlocks(INJECTED);
    const prepared = citeTextPreprocess(body, sources);
    expect(prepared).toContain('[[1]](#cite-1)');
    expect(prepared).toContain('[[2]](#cite-2)');
    expect(prepared).not.toContain('[[3]]');
  });

  it('无引用时正文不变', () => {
    const body = '答案见 [1] 与 [2]';
    expect(citeTextPreprocess(body, [])).toBe(body);
  });
});

describe('MiniMd 渲染 citation', () => {
  it('注入消息渲染出角标按钮 + 引用来源列表', () => {
    render(<MiniMd text={INJECTED} />);
    const sup1 = screen.getByRole('button', { name: '引用来源 1：向量检索入门' });
    expect(sup1).toBeInTheDocument();
    expect(screen.getByText('引用来源')).toBeInTheDocument();
    expect(screen.getByText(/《向量检索入门》 · 分块 chk_aaa111/)).toBeInTheDocument();
  });

  it('点击角标打开来源卡（含摘录与跳转入口），Escape 关闭', () => {
    render(<MiniMd text={INJECTED} />);
    fireEvent.click(screen.getByRole('button', { name: '引用来源 1：向量检索入门' }));
    const card = screen.getByRole('dialog', { name: /引用来源 1：向量检索入门/ });
    expect(card).toBeInTheDocument();
    expect(card).toHaveTextContent('FAISS 是 Facebook AI Research');
    expect(screen.getByRole('button', { name: '在知识库面板打开' })).toBeInTheDocument();
    fireEvent.keyDown(card, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: /引用来源 1/ })).not.toBeInTheDocument();
  });

  it('键盘路径（a11y）：聚焦角标按 Enter 打开卡片，可读屏可达', async () => {
    const user = userEvent.setup();
    render(<MiniMd text={INJECTED} />);
    const sup = screen.getByRole('button', { name: '引用来源 1：向量检索入门' });
    sup.focus();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('dialog', { name: /引用来源 1/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '关闭引用卡片' })).toBeInTheDocument();
  });

  it('普通消息不出现引用来源区，也不产生任何角标按钮（零回归）', () => {
    render(<MiniMd text={'普通回答 [1]。\n\n- 列表项\n'} />);
    expect(screen.queryByText('引用来源')).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('StoryMarkdown 渲染 citation', () => {
  it('同一注入格式在 story 渲染器中同样产出角标 + 来源列表', () => {
    render(<StoryMarkdown text={INJECTED} />);
    expect(screen.getByRole('button', { name: '引用来源 2：RAG 架构' })).toBeInTheDocument();
    expect(screen.getByText('引用来源')).toBeInTheDocument();
  });

  it('消毒行为不回退：javascript: 链接仍被拒绝', () => {
    render(<StoryMarkdown text={'[x](javascript:alert(1)) [n](#cite-9)'} />);
    for (const link of screen.queryAllByRole('link')) {
      expect(link.getAttribute('href')).not.toContain('javascript:');
    }
  });
});
