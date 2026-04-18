'use client';

import React, { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  X, Settings, Cpu, Network, Sparkles, Save, RefreshCw, 
  Upload, Download, ShieldCheck, Globe, Code, Terminal
} from 'lucide-react';
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
  const [localLlm, setLocalLlm] = useState<any>({});
  const [localMcp, setLocalMcp] = useState("");

  // Load backend config visibility
  useEffect(() => {
    if (settingsOpen) {
      fetchConfig();
    }
  }, [settingsOpen]);

  const fetchConfig = async () => {
    try {
      const [llmResp, mcpResp, skillsResp] = await Promise.all([
        fetch('http://localhost:8001/api/v1/config/llm'),
        fetch('http://localhost:8001/api/v1/config/mcp'),
        fetch('http://localhost:8001/api/v1/config/skills')
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

  const saveLlm = async () => {
    setIsSaving(true);
    try {
      const resp = await fetch('http://localhost:8001/api/v1/config/llm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(localLlm)
      });
      if (resp.ok) {
        const data = await resp.json();
        setLlmConfig(data.config);
      }
    } finally {
      setIsSaving(false);
    }
  };

  const saveMcp = async () => {
    setIsSaving(true);
    try {
      const resp = await fetch('http://localhost:8001/api/v1/config/mcp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config_json: localMcp })
      });
      if (resp.ok) {
        setMcpConfig(localMcp);
      } else {
        const err = await resp.json();
        alert("MCP 配置保存失败: " + err.detail);
      }
    } finally {
      setIsSaving(false);
    }
  };

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
          className="w-full max-w-4xl h-[700px] bg-[#0A0A0A]/80 border border-white/10 rounded-3xl overflow-hidden flex flex-col shadow-[0_0_50px_rgba(0,0,0,0.5)]"
          initial={{ scale: 0.9, y: 20 }}
          animate={{ scale: 1, y: 0 }}
          exit={{ scale: 0.9, y: 20 }}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-8 py-6 border-b border-white/5 bg-white/[0.02]">
            <div className="flex items-center gap-3">
              <div className="p-2.5 bg-hud-cyan/10 rounded-xl">
                <Settings className="w-5 h-5 text-hud-cyan" />
              </div>
              <div>
                <h2 className="text-xl font-medium tracking-tight text-white/90">系统控制中心</h2>
                <p className="text-[11px] font-mono text-white/30 uppercase tracking-[0.2em]">Agent Command Center (CNS-V3.1)</p>
              </div>
            </div>
            <button 
              onClick={() => setSettingsOpen(false)}
              className="p-2 hover:bg-white/5 rounded-full transition-colors group"
            >
              <X className="w-5 h-5 text-white/30 group-hover:text-white/60" />
            </button>
          </div>

          <div className="flex-1 flex overflow-hidden">
            {/* Sidebar Navigation */}
            <div className="w-64 border-r border-white/5 p-6 flex flex-col gap-2">
              <NavButton 
                active={activeTab === 'llm'} 
                onClick={() => setActiveTab('llm')} 
                icon={<Cpu className="w-4 h-4" />}
                label="语言模型 (LLM)"
              />
              <NavButton 
                active={activeTab === 'mcp'} 
                onClick={() => setActiveTab('mcp')} 
                icon={<Network className="w-4 h-4" />}
                label="MCP 连接器"
              />
              <NavButton 
                active={activeTab === 'skills'} 
                onClick={() => setActiveTab('skills')} 
                icon={<Sparkles className="w-4 h-4" />}
                label="技能 Hub"
              />
              
              <div className="mt-auto pt-6 border-t border-white/5">
                <div className="flex items-center gap-2 px-4 py-3 bg-hud-green/5 border border-hud-green/10 rounded-xl">
                  <ShieldCheck className="w-3.5 h-3.5 text-hud-green" />
                  <span className="text-[10px] font-mono text-hud-green/80 uppercase tracking-widest">系统状态: 在线</span>
                </div>
              </div>
            </div>

            {/* Content Area */}
            <div className="flex-1 overflow-y-auto p-8 bg-black/20">
              {activeTab === 'llm' && (
                <div className="space-y-8 animate-in fade-in slide-in-from-bottom-2 duration-300">
                  <SectionTitle title="LLM 核心配置" subtitle="管理大模型的接入端点与运行参数。更新后将保留当前对话记忆。" />
                  
                  <div className="grid gap-6">
                    <Field label="API Endpoint (Base URL)" value={localLlm.base_url} onChange={v => setLocalLlm({...localLlm, base_url: v})} placeholder="https://api.openai.com/v1" icon={<Globe className="w-4 h-4" />} />
                    <Field label="API Key" value={localLlm.api_key} onChange={v => setLocalLlm({...localLlm, api_key: v})} type="password" placeholder="sk-..." icon={<Terminal className="w-4 h-4" />} />
                    <div className="grid grid-cols-2 gap-6">
                      <Field label="Model Name" value={localLlm.model} onChange={v => setLocalLlm({...localLlm, model: v})} placeholder="gpt-4o" icon={<Cpu className="w-4 h-4" />} />
                      <div className="space-y-2">
                        <label className="text-[11px] font-mono text-white/40 uppercase tracking-wider ml-1">Prompt Caching</label>
                        <div className="flex items-center h-[42px] px-4 bg-white/[0.03] border border-white/10 rounded-xl">
                           <input 
                            type="checkbox" 
                            checked={localLlm.use_prompt_caching} 
                            onChange={e => setLocalLlm({...localLlm, use_prompt_caching: e.target.checked})}
                            className="mr-3 accent-hud-cyan"
                           />
                           <span className="text-sm text-white/60">启用首部缓存加速</span>
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center justify-end gap-4 pt-4">
                    <button className="flex items-center gap-2 px-6 py-2.5 bg-white/5 hover:bg-white/10 text-white/60 text-sm font-medium rounded-xl transition-all">
                      <RefreshCw className="w-4 h-4" /> 连通性测试
                    </button>
                    <button 
                      onClick={saveLlm}
                      disabled={isSaving}
                      className="flex items-center gap-2 px-8 py-2.5 bg-hud-cyan/10 hover:bg-hud-cyan/20 border border-hud-cyan/30 text-hud-cyan text-sm font-medium rounded-xl shadow-[0_0_20px_rgba(0,242,255,0.1)] transition-all"
                    >
                      {isSaving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} 保存设置
                    </button>
                  </div>
                </div>
              )}

              {activeTab === 'mcp' && (
                <div className="h-full flex flex-col space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
                  <SectionTitle title="MCP 连接器 (JSON)" subtitle="直接编辑 mcp_servers.json 配置文件。保存后 Agent 将自动重新识别新连接。" />
                  
                  <div className="flex-1 min-h-[300px] relative group">
                    <textarea 
                      value={localMcp}
                      onChange={e => setLocalMcp(e.target.value)}
                      spellCheck={false}
                      className="w-full h-full bg-black/40 border border-white/10 rounded-2xl p-6 text-sm font-mono text-white/70 focus:outline-none focus:border-hud-cyan/40 transition-all resize-none"
                    />
                    <div className="absolute top-4 right-4 text-[10px] font-mono text-white/20 uppercase">mcp_servers.json</div>
                  </div>

                  <div className="flex items-center justify-between pt-4">
                    <p className="text-[11px] text-white/30 italic">提示: 确保 JSON 结构符合 MCP 官方标准 (stdio/sse)。</p>
                    <button 
                      onClick={saveMcp}
                      disabled={isSaving}
                      className="flex items-center gap-2 px-8 py-2.5 bg-hud-cyan/10 hover:bg-hud-cyan/20 border border-hud-cyan/30 text-hud-cyan text-sm font-medium rounded-xl shadow-[0_0_20px_rgba(0,242,255,0.1)] transition-all"
                    >
                      {isSaving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} 部署配置
                    </button>
                  </div>
                </div>
              )}

              {activeTab === 'skills' && (
                <div className="space-y-8 animate-in fade-in slide-in-from-bottom-2 duration-300">
                  <SectionTitle title="技能 Hub & 实验室" subtitle="浏览、下载或上传技能插件。Agent 可以通过分析这些脚本获得新算子。" />
                  
                  <div className="grid grid-cols-2 gap-4">
                     {/* Local Skills */}
                     <div className="space-y-4">
                        <label className="text-[11px] font-mono text-white/40 uppercase tracking-widest">本地已安装 ({availableSkills.length})</label>
                        <div className="space-y-2">
                          {availableSkills.map((skill, i) => (
                            <div key={i} className="flex items-center justify-between p-4 bg-white/[0.03] border border-white/5 rounded-2xl hover:bg-white/[0.05] transition-all">
                              <div className="flex items-center gap-3">
                                <div className="p-2 bg-hud-cyan/5 rounded-lg">
                                  <Code className="w-4 h-4 text-hud-cyan/60" />
                                </div>
                                <div>
                                  <div className="text-sm text-white/80 font-medium">{skill.name}</div>
                                  <div className="text-[10px] text-white/30 font-mono">{(skill.size/1024).toFixed(1)} KB</div>
                                </div>
                              </div>
                              <button className="p-1.5 hover:bg-white/10 rounded-lg text-white/20">
                                <Terminal className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          ))}
                        </div>
                     </div>

                     {/* Upload / Hub browser */}
                     <div className="space-y-4">
                        <label className="text-[11px] font-mono text-white/40 uppercase tracking-widest">扩展与实验室</label>
                        
                        <div className="p-6 border-2 border-dashed border-white/10 rounded-3xl flex flex-col items-center justify-center gap-4 hover:border-hud-cyan/30 hover:bg-hud-cyan/5 transition-all cursor-pointer group">
                           <div className="p-4 bg-white/5 rounded-2xl group-hover:scale-110 transition-transform">
                             <Upload className="w-6 h-6 text-white/40 group-hover:text-hud-cyan" />
                           </div>
                           <div className="text-center">
                             <div className="text-sm text-white/60 font-medium">上传技能脚本</div>
                             <div className="text-[11px] text-white/20 mt-1">支持 .py 或 .md 格式插件包</div>
                           </div>
                        </div>

                        <div className="p-6 bg-hud-cyan/10 border border-hud-cyan/20 rounded-3xl flex items-center justify-between group cursor-pointer hover:bg-hud-cyan/15 transition-all">
                           <div className="flex items-center gap-4">
                              <div className="p-3 bg-hud-cyan/20 rounded-xl">
                                <Globe className="w-5 h-5 text-hud-cyan" />
                              </div>
                              <div>
                                <div className="text-sm text-white/80 font-medium">Skills Hub</div>
                                <div className="text-[11px] text-hud-cyan/60">在线浏览全球开发者共享的算子</div>
                              </div>
                           </div>
                           <Download className="w-5 h-5 text-hud-cyan/60" />
                        </div>
                     </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}

function NavButton({ active, onClick, icon, label }: { active: boolean, onClick: () => void, icon: React.ReactNode, label: string }) {
  return (
    <button 
      onClick={onClick}
      className={`flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-300 ${
        active 
          ? 'bg-hud-cyan/15 text-hud-cyan border border-hud-cyan/20' 
          : 'text-white/40 hover:text-white/70 hover:bg-white/5'
      }`}
    >
      {icon}
      <span className="text-sm font-medium">{label}</span>
      {active && <motion.div layoutId="nav-glow" className="ml-auto w-1 h-1 rounded-full bg-hud-cyan shadow-[0_0_8px_rgba(0,242,255,1)]" />}
    </button>
  );
}

function SectionTitle({ title, subtitle }: { title: string, subtitle: string }) {
  return (
    <div className="space-y-1">
      <h3 className="text-lg font-medium text-white/90">{title}</h3>
      <p className="text-xs text-white/40">{subtitle}</p>
    </div>
  );
}

function Field({ label, icon, ...props }: any) {
  return (
    <div className="space-y-2">
      <label className="text-[11px] font-mono text-white/40 uppercase tracking-wider ml-1">{label}</label>
      <div className="relative group">
        <div className="absolute left-4 top-1/2 -translate-y-1/2 text-white/20 group-focus-within:text-hud-cyan transition-colors">
          {icon}
        </div>
        <input 
          autoComplete="off"
          className="w-full h-11 bg-white/[0.03] border border-white/10 rounded-xl pl-11 pr-4 text-sm text-white/80 placeholder:text-white/10 focus:outline-none focus:border-hud-cyan/40 transition-all font-light"
          {...props} 
        />
      </div>
    </div>
  );
}
