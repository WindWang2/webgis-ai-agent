'use client';

import React, { useEffect, useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  X, Settings, Cpu, Network, Sparkles, Save, RefreshCw,
  Upload, Download, ShieldCheck, Globe, Code, Terminal,
  CheckCircle2, XCircle, Loader2
} from 'lucide-react';
import { API_BASE } from '@/lib/api/config';
import { useHudStore } from '@/lib/store/useHudStore';

export function SettingsPanel() {
  const {
    settingsOpen, setSettingsOpen,
    mcpConfig, setMcpConfig,
    llmConfig, setLlmConfig,
    availableSkills, setAvailableSkills
  } = useHudStore();

  const [activeTab, setActiveTab] = useState<'llm' | 'mcp' | 'skills'>('llm');
  const [isSaving, setIsSaving] = useState(false);
  const [saveFlash, setSaveFlash] = useState<string | null>(null);
  const [localLlm, setLocalLlm] = useState<Record<string, any>>({});
  const [localMcp, setLocalMcp] = useState("");

  useEffect(() => {
    if (settingsOpen) fetchConfig();
  }, [settingsOpen]);

  const fetchConfig = async () => {
    try {
      const [llmResp, mcpResp, skillsResp] = await Promise.all([
        fetch(`${API_BASE}/api/v1/config/llm`),
        fetch(`${API_BASE}/api/v1/config/mcp`),
        fetch(`${API_BASE}/api/v1/config/skills`)
      ]);

      if (llmResp.ok) {
        const data = await llmResp.json();
        setLlmConfig(data);
        setLocalLlm(data);
      }
      if (mcpResp.ok) {
        const data = await mcpResp.json();
        setMcpConfig(data.config_json);
        setLocalMcp(data.config_json);
      }
      if (skillsResp.ok) {
        const data = await skillsResp.json();
        setAvailableSkills(data.skills);
      }
    } catch (e) {
      console.error("Failed to fetch settings:", e);
    }
  };

  const handleSaveFlash = (tab: string) => {
    setSaveFlash(tab);
    setTimeout(() => setSaveFlash(null), 1500);
  };

  const saveLlm = async () => {
    setIsSaving(true);
    try {
      const resp = await fetch(`${API_BASE}/api/v1/config/llm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(localLlm)
      });
      if (resp.ok) {
        const data = await resp.json();
        setLlmConfig(data.config);
        handleSaveFlash('llm');
      }
    } finally {
      setIsSaving(false);
    }
  };

  const saveMcp = async () => {
    setIsSaving(true);
    try {
      const resp = await fetch(`${API_BASE}/api/v1/config/mcp`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config_json: localMcp })
      });
      if (resp.ok) {
        setMcpConfig(localMcp);
        handleSaveFlash('mcp');
      } else {
        const err = await resp.json();
        alert("MCP 配置保存失败: " + err.detail);
      }
    } finally {
      setIsSaving(false);
    }
  };

  const mcpValidation = useMemo(() => {
    if (!localMcp.trim()) return { valid: true, empty: true };
    try {
      JSON.parse(localMcp);
      return { valid: true, empty: false };
    } catch {
      return { valid: false, empty: false };
    }
  }, [localMcp]);

  if (!settingsOpen) return null;

  return (
    <AnimatePresence>
      <motion.div
        className="fixed inset-0 z-[100] flex items-center justify-center p-8 bg-black/60 backdrop-blur-md"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
      >
        <motion.div
          className="w-full max-w-4xl h-[700px] bg-[#0A0A0A]/90 border border-white/10 rounded-3xl overflow-hidden flex flex-col shadow-[0_0_60px_rgba(0,0,0,0.5)]"
          initial={{ scale: 0.9, y: 20 }}
          animate={{ scale: 1, y: 0 }}
          exit={{ scale: 0.9, y: 20 }}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-8 py-5 border-b border-white/[0.06] bg-white/[0.02]">
            <div className="flex items-center gap-3">
              <div className="p-2.5 bg-hud-cyan/[0.08] rounded-xl border border-hud-cyan/15">
                <Settings className="w-5 h-5 text-hud-cyan" />
              </div>
              <div>
                <h2 className="text-lg font-medium tracking-tight text-white/90">系统控制中心</h2>
                <p className="text-[10px] font-mono text-white/25 uppercase tracking-[0.2em]">Agent Command Center</p>
              </div>
            </div>
            <button
              onClick={() => setSettingsOpen(false)}
              className="p-2 hover:bg-white/5 rounded-lg transition-colors group"
            >
              <X className="w-4 h-4 text-white/25 group-hover:text-white/60" />
            </button>
          </div>

          <div className="flex-1 flex overflow-hidden">
            {/* Sidebar Navigation */}
            <div className="w-56 border-r border-white/[0.04] py-6 px-4 flex flex-col gap-1.5">
              <NavButton
                active={activeTab === 'llm'}
                onClick={() => setActiveTab('llm')}
                icon={<Cpu className="w-4 h-4" />}
                label="语言模型"
                badge={localLlm.model ? undefined : '!'}
                statusDot="green"
              />
              <NavButton
                active={activeTab === 'mcp'}
                onClick={() => setActiveTab('mcp')}
                icon={<Network className="w-4 h-4" />}
                label="MCP 连接器"
                statusDot={mcpValidation.valid ? 'green' : 'red'}
              />
              <NavButton
                active={activeTab === 'skills'}
                onClick={() => setActiveTab('skills')}
                icon={<Sparkles className="w-4 h-4" />}
                label="技能 Hub"
                count={availableSkills.length}
                statusDot="green"
              />

              <div className="mt-auto pt-4 border-t border-white/[0.04]">
                <div className="flex items-center gap-2 px-3 py-2.5 bg-emerald-500/[0.04] border border-emerald-500/10 rounded-xl">
                  <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.6)]" />
                  <span className="text-[10px] font-mono text-emerald-400/70 uppercase tracking-widest">系统在线</span>
                </div>
              </div>
            </div>

            {/* Content Area */}
            <div className="flex-1 overflow-y-auto p-8">
              <AnimatePresence mode="wait">
                {activeTab === 'llm' && (
                  <motion.div
                    key="llm"
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.2 }}
                    className="space-y-6"
                  >
                    <SectionTitle title="LLM 核心配置" subtitle="管理大模型的接入端点与运行参数" />

                    {/* Endpoint Section */}
                    <div className="space-y-4">
                      <div className="flex items-center gap-2 text-[10px] font-mono text-white/20 uppercase tracking-wider">
                        <Globe className="h-3 w-3" /> 接入端点
                      </div>
                      <div className="ml-1 pl-3 border-l border-white/[0.06] space-y-4">
                        <Field label="API Endpoint (Base URL)" value={localLlm.base_url} onChange={v => setLocalLlm({ ...localLlm, base_url: v })} placeholder="https://api.openai.com/v1" icon={<Globe className="w-4 h-4" />} />
                        <Field label="API Key" value={localLlm.api_key} onChange={v => setLocalLlm({ ...localLlm, api_key: v })} type="password" placeholder="sk-..." icon={<Terminal className="w-4 h-4" />} />
                      </div>
                    </div>

                    {/* Model Section */}
                    <div className="space-y-4">
                      <div className="flex items-center gap-2 text-[10px] font-mono text-white/20 uppercase tracking-wider">
                        <Cpu className="h-3 w-3" /> 模型参数
                      </div>
                      <div className="ml-1 pl-3 border-l border-white/[0.06] space-y-4">
                        <Field label="Model Name" value={localLlm.model} onChange={v => setLocalLlm({ ...localLlm, model: v })} placeholder="gpt-4o" icon={<Cpu className="w-4 h-4" />} />
                        {localLlm.model && (
                          <p className="text-[10px] text-white/15 font-mono -mt-2">
                            当前模型: {localLlm.model} {localLlm.model.includes('gpt-4') ? '(OpenAI GPT-4 系列)' : localLlm.model.includes('claude') ? '(Anthropic Claude 系列)' : ''}
                          </p>
                        )}
                        <div className="flex items-center h-[42px] px-4 bg-white/[0.02] border border-white/[0.06] rounded-xl">
                          <input
                            type="checkbox"
                            checked={localLlm.use_prompt_caching}
                            onChange={e => setLocalLlm({ ...localLlm, use_prompt_caching: e.target.checked })}
                            className="mr-3 accent-hud-cyan"
                          />
                          <span className="text-[12px] text-white/50">启用 Prompt Caching 加速</span>
                        </div>
                      </div>
                    </div>

                    {/* Save Row */}
                    <div className="flex items-center justify-end gap-3 pt-4 border-t border-white/[0.04]">
                      <button className="flex items-center gap-2 px-4 py-2 bg-white/[0.03] hover:bg-white/[0.06] text-white/40 text-[12px] font-medium rounded-xl transition-all border border-white/[0.06]">
                        <RefreshCw className="w-3.5 h-3.5" /> 连通性测试
                      </button>
                      <SaveButton
                        onClick={saveLlm}
                        isSaving={isSaving}
                        flash={saveFlash === 'llm'}
                      />
                    </div>
                  </motion.div>
                )}

                {activeTab === 'mcp' && (
                  <motion.div
                    key="mcp"
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.2 }}
                    className="h-full flex flex-col space-y-5"
                  >
                    <div className="flex items-center justify-between">
                      <SectionTitle title="MCP 连接器" subtitle="编辑 mcp_servers.json 配置文件" />
                      {/* JSON validation indicator */}
                      {!mcpValidation.empty && (
                        <div className={`flex items-center gap-1.5 text-[10px] font-mono ${mcpValidation.valid ? 'text-emerald-400/70' : 'text-red-400/80'}`}>
                          {mcpValidation.valid
                            ? <><CheckCircle2 className="w-3.5 h-3.5" /> JSON 有效</>
                            : <><XCircle className="w-3.5 h-3.5" /> JSON 格式错误</>
                          }
                        </div>
                      )}
                    </div>

                    <div className="flex-1 min-h-[300px] relative">
                      <textarea
                        value={localMcp}
                        onChange={e => setLocalMcp(e.target.value)}
                        spellCheck={false}
                        className={`
                          w-full h-full bg-black/30 rounded-xl p-5 text-[12px] font-mono text-white/60
                          focus:outline-none transition-all resize-none leading-relaxed
                          ${mcpValidation.valid || mcpValidation.empty
                            ? 'border border-white/[0.08] focus:border-hud-cyan/30'
                            : 'border border-red-500/30 focus:border-red-500/50'
                          }
                        `}
                      />
                      <div className="absolute top-3 right-4 text-[9px] font-mono text-white/15 uppercase">mcp_servers.json</div>
                    </div>

                    <div className="flex items-center justify-between pt-3 border-t border-white/[0.04]">
                      <p className="text-[10px] text-white/20">确保 JSON 结构符合 MCP 标准 (stdio/sse)</p>
                      <SaveButton
                        onClick={saveMcp}
                        isSaving={isSaving}
                        flash={saveFlash === 'mcp'}
                        disabled={!mcpValidation.valid || mcpValidation.empty}
                        label="部署配置"
                      />
                    </div>
                  </motion.div>
                )}

                {activeTab === 'skills' && (
                  <motion.div
                    key="skills"
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.2 }}
                    className="space-y-6"
                  >
                    <SectionTitle title="技能 Hub" subtitle="浏览已安装的技能插件与上传入口" />

                    <div className="grid grid-cols-2 gap-4">
                      {/* Local Skills */}
                      <div className="space-y-3">
                        <div className="flex items-center justify-between">
                          <span className="text-[10px] font-mono text-white/25 uppercase tracking-widest">已安装</span>
                          <span className="text-[9px] font-mono text-white/15 px-1.5 py-0.5 bg-white/[0.03] rounded-full">{availableSkills.length} 个技能</span>
                        </div>
                        <div className="space-y-1.5">
                          {availableSkills.length === 0 ? (
                            <div className="p-6 text-center rounded-xl border border-dashed border-white/[0.06]">
                              <Code className="h-6 w-6 text-white/[0.06] mx-auto mb-2" />
                              <p className="text-[11px] text-white/15">暂无已安装技能</p>
                            </div>
                          ) : availableSkills.map((skill: any, i: number) => (
                            <div key={i} className="flex items-center justify-between p-3 bg-white/[0.02] border border-white/[0.04] rounded-xl hover:bg-white/[0.04] hover:border-white/[0.08] transition-all group">
                              <div className="flex items-center gap-2.5">
                                <div className="p-1.5 bg-hud-cyan/[0.06] rounded-lg border border-hud-cyan/10">
                                  <Code className="w-3.5 h-3.5 text-hud-cyan/50" />
                                </div>
                                <div>
                                  <div className="text-[11px] text-white/70 font-medium">{skill.name}</div>
                                  <div className="text-[9px] text-white/20 font-mono">
                                    {(skill.size / 1024).toFixed(1)} KB{skill.modified ? ` · ${new Date(skill.modified).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })}` : ''}
                                  </div>
                                </div>
                              </div>
                              <button className="p-1 opacity-0 group-hover:opacity-100 hover:bg-white/[0.06] rounded-md text-white/15 hover:text-white/40 transition-all">
                                <Terminal className="w-3 h-3" />
                              </button>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Upload / Hub */}
                      <div className="space-y-3">
                        <span className="text-[10px] font-mono text-white/25 uppercase tracking-widest">扩展</span>

                        <div className="p-5 border border-dashed border-white/[0.08] rounded-xl flex flex-col items-center justify-center gap-3 hover:border-hud-cyan/20 hover:bg-hud-cyan/[0.02] transition-all cursor-pointer group">
                          <div className="p-3 bg-white/[0.03] rounded-xl group-hover:scale-105 transition-transform">
                            <Upload className="w-5 h-5 text-white/25 group-hover:text-hud-cyan/60" />
                          </div>
                          <div className="text-center">
                            <div className="text-[11px] text-white/50 font-medium">上传技能脚本</div>
                            <div className="text-[10px] text-white/15 mt-0.5">.py 或 .md 格式</div>
                          </div>
                        </div>

                        <div className="p-4 bg-hud-cyan/[0.04] border border-hud-cyan/10 rounded-xl flex items-center justify-between group cursor-pointer hover:bg-hud-cyan/[0.08] transition-all">
                          <div className="flex items-center gap-3">
                            <div className="p-2 bg-hud-cyan/[0.08] rounded-lg">
                              <Globe className="w-4 h-4 text-hud-cyan/60" />
                            </div>
                            <div>
                              <div className="text-[11px] text-white/70 font-medium">Skills Hub</div>
                              <div className="text-[10px] text-hud-cyan/40">在线浏览全球开发者共享算子</div>
                            </div>
                          </div>
                          <Download className="w-4 h-4 text-hud-cyan/40" />
                        </div>
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}

function NavButton({ active, onClick, icon, label, count, badge, statusDot }: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  count?: number;
  badge?: string;
  statusDot?: 'green' | 'red' | 'gray';
}) {
  const dotColor = statusDot === 'green'
    ? 'bg-emerald-400 shadow-[0_0_4px_rgba(52,211,153,0.6)]'
    : statusDot === 'red'
      ? 'bg-red-400 shadow-[0_0_4px_rgba(248,113,113,0.6)]'
      : 'bg-white/15';

  return (
    <button
      onClick={onClick}
      className={`
        flex items-center gap-2.5 px-3 py-2.5 rounded-xl transition-all duration-200 w-full text-left
        ${active
          ? 'bg-hud-cyan/[0.08] text-hud-cyan border border-hud-cyan/15'
          : 'text-white/35 hover:text-white/60 hover:bg-white/[0.03] border border-transparent'
        }
      `}
    >
      {icon}
      <span className="text-[12px] font-medium flex-1">{label}</span>
      {count !== undefined && count > 0 && (
        <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-mono ${active ? 'bg-hud-cyan/20 text-hud-cyan' : 'bg-white/[0.04] text-white/25'}`}>
          {count}
        </span>
      )}
      {badge && (
        <span className="text-[9px] px-1.5 py-0.5 rounded-full font-mono bg-amber-500/20 text-amber-400">{badge}</span>
      )}
      {statusDot && <div className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />}
    </button>
  );
}

function SectionTitle({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div>
      <h3 className="text-[15px] font-medium text-white/85">{title}</h3>
      <p className="text-[11px] text-white/30 mt-0.5">{subtitle}</p>
    </div>
  );
}

function Field({ label, icon, ...props }: any) {
  return (
    <div className="space-y-1.5">
      <label className="text-[10px] font-mono text-white/30 uppercase tracking-wider">{label}</label>
      <div className="relative group">
        <div className="absolute left-3.5 top-1/2 -translate-y-1/2 text-white/15 group-focus-within:text-hud-cyan/50 transition-colors">
          {icon}
        </div>
        <input
          autoComplete="off"
          className="w-full h-10 bg-white/[0.02] border border-white/[0.06] rounded-xl pl-10 pr-4 text-[12px] text-white/70 placeholder:text-white/10 focus:outline-none focus:border-hud-cyan/25 transition-all"
          {...props}
        />
      </div>
    </div>
  );
}

function SaveButton({ onClick, isSaving, flash, disabled, label = '保存设置' }: {
  onClick: () => void;
  isSaving: boolean;
  flash: boolean;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={isSaving || disabled}
      className={`
        flex items-center gap-2 px-6 py-2 text-[12px] font-medium rounded-xl transition-all duration-300
        ${flash
          ? 'bg-emerald-500/20 border border-emerald-500/40 text-emerald-400 shadow-[0_0_20px_rgba(52,211,153,0.15)]'
          : disabled
            ? 'bg-white/[0.02] border border-white/[0.06] text-white/15 cursor-not-allowed'
            : 'bg-hud-cyan/[0.08] hover:bg-hud-cyan/15 border border-hud-cyan/20 text-hud-cyan shadow-[0_0_15px_rgba(0,242,255,0.06)]'
        }
      `}
    >
      {flash ? <CheckCircle2 className="w-3.5 h-3.5" /> : isSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
      {flash ? '已保存' : label}
    </button>
  );
}
