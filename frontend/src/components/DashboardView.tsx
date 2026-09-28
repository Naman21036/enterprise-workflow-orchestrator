import React, { useState } from 'react';
import { Play, CheckCircle2, AlertTriangle, XCircle, Search, Layers, RefreshCw, Eye, ArrowRight, ShieldCheck, Clock } from 'lucide-react';
import { runWorkflowGoal, ExecutionResult } from '../services/api';

interface DashboardViewProps {
  onSelectRun: (runId: string) => void;
  onNavigateHandoff: (runId: string) => void;
}

export const DashboardView: React.FC<DashboardViewProps> = ({ onSelectRun, onNavigateHandoff }) => {
  const [goal, setGoal] = useState('Find member 1002 and retrieve their savings balance.');
  const [memberId, setMemberId] = useState('1002');
  const [targetApp, setTargetApp] = useState('APEX Federal');
  const [loading, setLoading] = useState(false);
  const [activeRun, setActiveRun] = useState<ExecutionResult | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleRunWorkflow = async () => {
    if (!goal.trim()) return;
    setLoading(true);
    setErrorMsg(null);
    setActiveRun(null);

    try {
      const res = await runWorkflowGoal({
        goal: goal.trim(),
        target_app: targetApp,
        input_parameters: memberId ? { member_id: memberId } : {}
      });
      setActiveRun(res);
      if (res.status === 'BLOCKED') {
        onNavigateHandoff(res.run_id);
      }
    } catch (err: any) {
      setErrorMsg(err.message || 'Workflow execution failed');
    } finally {
      setLoading(false);
    }
  };

  const handleExampleClick = (exGoal: string, exId: string) => {
    setGoal(exGoal);
    setMemberId(exId);
  };

  return (
    <div className="space-y-6">
      {/* Top Section: Goal Input Form */}
      <div className="bg-[#1C2541] border border-slate-700/60 rounded-xl p-6 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-xl font-bold text-slate-100 flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-[#00F2FE]"></span>
              Automate Banking Workflows
            </h2>
            <p className="text-xs text-slate-400 mt-1">
              Use natural language to run existing capabilities or discover new workflows on APEX Federal.
            </p>
          </div>
          <span className="text-xs font-mono text-[#00F2FE] bg-[#00F2FE]/10 px-3 py-1 rounded-md border border-[#00F2FE]/30">
            ENV: DEV-LOCAL
          </span>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-4">
            <div>
              <label className="block text-xs font-semibold uppercase text-slate-400 mb-1.5">
                What would you like to do?
              </label>
              <textarea
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                rows={3}
                className="w-full bg-[#0B132B] border border-slate-700 rounded-lg p-3 text-sm text-slate-100 focus:border-[#00F2FE] focus:outline-none transition"
                placeholder="e.g. Find member 1002 and retrieve their savings balance."
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold uppercase text-slate-400 mb-1.5">
                  Target Application
                </label>
                <select
                  value={targetApp}
                  onChange={(e) => setTargetApp(e.target.value)}
                  className="w-full bg-[#0B132B] border border-slate-700 rounded-lg p-2.5 text-sm text-slate-100 focus:border-[#00F2FE] focus:outline-none"
                >
                  <option value="APEX Federal">APEX Federal · Local synthetic banking UI</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-semibold uppercase text-slate-400 mb-1.5">
                  Member ID Parameter (optional)
                </label>
                <input
                  type="text"
                  value={memberId}
                  onChange={(e) => setMemberId(e.target.value)}
                  className="w-full bg-[#0B132B] border border-slate-700 rounded-lg p-2.5 text-sm text-slate-100 focus:border-[#00F2FE] focus:outline-none font-mono"
                  placeholder="1002"
                />
              </div>
            </div>

            <button
              onClick={handleRunWorkflow}
              disabled={loading}
              className="w-full bg-gradient-to-r from-[#00C6FF] to-[#0072FF] text-white font-semibold py-3 px-6 rounded-lg shadow-lg hover:opacity-95 transition flex items-center justify-center gap-2 disabled:opacity-50"
            >
              {loading ? (
                <>
                  <RefreshCw className="w-5 h-5 animate-spin" />
                  Orchestrating Workflow...
                </>
              ) : (
                <>
                  <Play className="w-5 h-5 fill-current" />
                  Run Workflow
                </>
              )}
            </button>
          </div>

          {/* Quick Examples Column */}
          <div className="bg-[#151E38] border border-slate-700/50 rounded-lg p-4 space-y-3">
            <span className="text-xs font-semibold uppercase text-slate-400 tracking-wider">
              Quick Demonstration Scenarios
            </span>

            <div
              onClick={() => handleExampleClick('Find member 1002 and retrieve their savings balance.', '1002')}
              className="p-3 bg-[#0B132B] border border-slate-700/60 rounded-lg hover:border-[#00F2FE]/50 cursor-pointer transition"
            >
              <div className="text-xs font-semibold text-[#00F2FE]">1. Deterministic Replay (Found)</div>
              <div className="text-xs text-slate-300 mt-1">Find member 1002 & extract savings balance ($7,250.00).</div>
            </div>

            <div
              onClick={() => handleExampleClick('Find member 99999 and retrieve their savings balance.', '99999')}
              className="p-3 bg-[#0B132B] border border-slate-700/60 rounded-lg hover:border-amber-400/50 cursor-pointer transition"
            >
              <div className="text-xs font-semibold text-amber-400">2. Business Outcome (Not Found)</div>
              <div className="text-xs text-slate-300 mt-1">Search member 99999 &rarr; returns MEMBER_NOT_FOUND payload.</div>
            </div>

            <div
              onClick={() => handleExampleClick('Open new checking account for member 1003.', '1003')}
              className="p-3 bg-[#0B132B] border border-slate-700/60 rounded-lg hover:border-cyan-400/50 cursor-pointer transition"
            >
              <div className="text-xs font-semibold text-cyan-400">3. New workflow discovery</div>
              <div className="text-xs text-slate-300 mt-1">Discovers new workflow UI capability automatically.</div>
            </div>
          </div>
        </div>
      </div>

      {/* Error Alert */}
      {errorMsg && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 flex items-center gap-3 text-red-400 text-sm">
          <XCircle className="w-5 h-5 flex-shrink-0" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Workflow Execution Results Display (Matches Screens 2, 4, 5, 6 in Visual Spec) */}
      {activeRun && (
        <div className="bg-[#1C2541] border border-slate-700/60 rounded-xl p-6 space-y-6 shadow-2xl">
          {/* Header Banner */}
          <div className="flex flex-wrap items-center justify-between border-b border-slate-700/60 pb-4 gap-4">
            <div>
              <div className="flex items-center gap-3">
                <span className={`px-3 py-1 rounded-md text-xs font-bold uppercase tracking-wide ${
                  activeRun.status === 'SUCCESS' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40' :
                  activeRun.status === 'BUSINESS_OUTCOME' ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40' :
                  activeRun.status === 'BLOCKED' ? 'bg-red-500/20 text-red-400 border border-red-500/40' :
                  'bg-blue-500/20 text-blue-400'
                }`}>
                  {activeRun.status}
                </span>
                <span className="text-xs font-mono text-slate-400">Run ID: {activeRun.run_id}</span>
              </div>
              <h3 className="text-base font-semibold text-slate-100 mt-2">
                Workflow: {activeRun.goal}
              </h3>
            </div>

            <div className="flex items-center gap-6 text-xs text-slate-300 font-mono">
              <div>
                <span className="text-slate-500 block text-[10px] uppercase">Execution Mode</span>
                <span className="text-cyan-400 font-semibold">{activeRun.execution_mode}</span>
              </div>
              <div>
                <span className="text-slate-500 block text-[10px] uppercase">Capability</span>
                <span>{activeRun.capability_id || 'member_savings_lookup'} v1.0.0</span>
              </div>
              <div>
                <span className="text-slate-500 block text-[10px] uppercase">Duration</span>
                <span>{activeRun.duration_seconds ? `${activeRun.duration_seconds.toFixed(1)}s` : '1.2s'}</span>
              </div>
            </div>
          </div>

          {/* Main Execution View Split Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Timeline Steps */}
            <div className="space-y-4">
              <h4 className="text-sm font-semibold uppercase text-slate-400 tracking-wider flex items-center gap-2">
                <Layers className="w-4 h-4 text-[#00F2FE]" />
                Execution Step Timeline
              </h4>

              <div className="space-y-3">
                <div className="flex items-start gap-3 p-3 bg-[#0B132B] border border-slate-700/60 rounded-lg">
                  <div className="w-6 h-6 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center font-bold text-xs">1</div>
                  <div>
                    <div className="text-xs font-semibold text-slate-200">Capability Lookup</div>
                    <div className="text-xs text-slate-400">
                      {activeRun.execution_mode === 'Deterministic Replay' ?
                        `Found ${activeRun.capability_id || 'a compatible'} capability and routed to deterministic replay.` :
                        `No compatible capability found. Discovery recording ${activeRun.recording_id || 'created'} was compiled into ${activeRun.capability_id || 'a capability'}.`}
                    </div>
                  </div>
                </div>

                <div className="flex items-start gap-3 p-3 bg-[#0B132B] border border-slate-700/60 rounded-lg">
                  <div className="w-6 h-6 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center font-bold text-xs">2</div>
                  <div>
                    <div className="text-xs font-semibold text-slate-200">Replay Execution</div>
                    <div className="text-xs text-slate-400">{activeRun.execution_mode === 'Deterministic Replay' ? 'Stored steps replayed in a fresh browser context.' : 'The newly compiled capability was verified in a fresh browser context.'}</div>
                  </div>
                </div>

                <div className="flex items-start gap-3 p-3 bg-[#0B132B] border border-slate-700/60 rounded-lg">
                  <div className="w-6 h-6 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center font-bold text-xs">3</div>
                  <div>
                    <div className="text-xs font-semibold text-slate-200">Checkpoint Verification & Output Extraction</div>
                    <div className="text-xs text-slate-400">Verifying page DOM checkpoints & extracting structured shape data.</div>
                  </div>
                </div>
              </div>
            </div>

            {/* Extracted Outputs or Business Outcome Display */}
            <div>
              {activeRun.status === 'BUSINESS_OUTCOME' ? (
                <div className="bg-amber-500/10 border border-amber-500/40 rounded-xl p-5 space-y-3">
                  <div className="flex items-center gap-2 text-amber-400 font-bold text-base">
                    <AlertTriangle className="w-5 h-5" />
                    Business Outcome: Member Not Found
                  </div>
                  <p className="text-xs text-slate-300">
                    The core banking system returned a non-error business outcome state. The requested member ID does not exist.
                  </p>
                  <div className="bg-[#0B132B] p-3 rounded-lg border border-amber-500/30 text-xs font-mono text-slate-300 space-y-1">
                    <div>Input Member ID: {memberId}</div>
                    <div>Error Code: MEMBER_NOT_FOUND</div>
                  </div>
                </div>
              ) : activeRun.status === 'BLOCKED' ? (
                <div className="bg-red-500/10 border border-red-500/40 rounded-xl p-5 space-y-4">
                  <div className="flex items-center gap-2 text-red-400 font-bold text-base">
                    <AlertTriangle className="w-5 h-5" />
                    Blocked: Human Intervention Required
                  </div>
                  <p className="text-xs text-slate-300">
                    Workflow hit an unexpected confirmation dialog blocking execution. Active browser session preserved.
                  </p>
                  <button
                    onClick={() => onNavigateHandoff(activeRun.run_id)}
                    className="w-full bg-red-600 hover:bg-red-500 text-white font-bold py-2.5 px-4 rounded-lg text-xs transition"
                  >
                    Open Human Intervention Console →
                  </button>
                </div>
              ) : (
                <div className="bg-[#151E38] border border-slate-700/60 rounded-xl p-5 space-y-4">
                  <h4 className="text-sm font-semibold uppercase text-slate-400 tracking-wider flex items-center justify-between">
                    <span>Extracted Outputs</span>
                    <span className="text-xs font-normal text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/30">Verified</span>
                  </h4>

                  <div className="space-y-2.5 font-mono text-xs">
                    <div className="flex justify-between items-center p-2.5 bg-[#0B132B] rounded border border-slate-700">
                      <span className="text-slate-400">savings_balance</span>
                      <span className="text-[#00F2FE] font-bold text-sm">{activeRun.outputs?.savings_balance || 'Not returned'}</span>
                    </div>

                    <div className="flex justify-between items-center p-2.5 bg-[#0B132B] rounded border border-slate-700">
                      <span className="text-slate-400">member_id</span>
                      <span className="text-slate-200">{activeRun.outputs?.member_id || 'Not returned'}</span>
                    </div>

                    <div className="flex justify-between items-center p-2.5 bg-[#0B132B] rounded border border-slate-700">
                      <span className="text-slate-400">member_name</span>
                      <span className="text-slate-200">{activeRun.outputs?.member_name || 'Not returned'}</span>
                    </div>
                  </div>

                  <div className="pt-2 flex gap-3">
                    <button
                      onClick={() => onSelectRun(activeRun.run_id)}
                      className="flex-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs py-2 px-3 rounded border border-slate-700 transition flex items-center justify-center gap-1.5"
                    >
                      <Eye className="w-3.5 h-3.5 text-[#00F2FE]" />
                      View Evidence
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
