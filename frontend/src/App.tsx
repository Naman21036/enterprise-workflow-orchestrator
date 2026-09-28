import { useEffect, useState } from 'react';
import { Activity, AlertTriangle, Archive, ArrowLeftRight, FileClock, Gauge, Layers3, Menu, PanelLeftClose, Plus, Radio, Search, Settings2, Shield, Workflow, X, type LucideIcon } from 'lucide-react';
import { DashboardView, NewWorkflowView, WorkflowRunsView } from './components/WorkspaceViews';
import { CapabilityRegistryView } from './components/CapabilityRegistryView';
import { RecordingExplorerView } from './components/RecordingExplorerView';
import { HandoffView } from './components/HandoffView';
import { EvidenceView } from './components/EvidenceView';
import { SettingsView } from './components/SettingsView';
import { fetchHealth } from './services/api';

type Page = 'overview' | 'new' | 'runs' | 'capabilities' | 'recordings' | 'handoff' | 'evidence' | 'settings';
const groups: { label: string; items: { id: Page; label: string; icon: LucideIcon }[] }[] = [
  { label: 'WORKSPACE', items: [{ id: 'overview', label: 'Overview', icon: Gauge }, { id: 'new', label: 'New workflow', icon: Plus }, { id: 'runs', label: 'Workflow runs', icon: FileClock }] },
  { label: 'AUTOMATION', items: [{ id: 'capabilities', label: 'Capabilities', icon: Layers3 }, { id: 'recordings', label: 'Recordings', icon: Archive }] },
  { label: 'OPERATIONS', items: [{ id: 'handoff', label: 'Human intervention', icon: AlertTriangle }, { id: 'evidence', label: 'Evidence', icon: Shield }] },
  { label: 'CONFIGURATION', items: [{ id: 'settings', label: 'Settings', icon: Settings2 }] },
];
const titles: Record<Page, string> = { overview: 'Overview', new: 'New workflow', runs: 'Workflow runs', capabilities: 'Capability registry', recordings: 'Recording explorer', handoff: 'Human intervention', evidence: 'Evidence explorer', settings: 'System settings' };

export default function App() {
  const [page, setPage] = useState<Page>('overview');
  const [runId, setRunId] = useState('');
  const [health, setHealth] = useState<any>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [commandQuery, setCommandQuery] = useState('');
  const goToRun = (id: string, destination: Page = 'runs') => { setRunId(id); setPage(destination); setMenuOpen(false); };

  useEffect(() => { let mounted = true; const load = () => fetchHealth().then((data) => { if (mounted) setHealth(data); }).catch(() => { if (mounted) setHealth({ status: 'unavailable' }); }); load(); const timer = window.setInterval(load, 30000); return () => { mounted = false; window.clearInterval(timer); }; }, []);
  useEffect(() => { const key = (event: KeyboardEvent) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setCommandOpen(true); } if (event.key === 'Escape') setCommandOpen(false); }; window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key); }, []);
  const online = health?.status === 'healthy' && health?.database === 'healthy' && health?.target_application?.status === 'online' && health?.browser_automation?.status === 'installed';

  return <div className={`app-shell ${collapsed ? 'sidebar-collapsed' : ''}`}>
    <aside className={`sidebar ${menuOpen ? 'mobile-open' : ''}`}>
      <div className="brand-row"><div className="brand-mark"><Workflow size={19}/></div><div className="brand-copy"><strong>APEX</strong><span>AUTOMATION</span></div><button className="icon-button sidebar-close" aria-label="Close navigation" onClick={() => setMenuOpen(false)}><X size={17}/></button></div>
      <div className="workspace-switch"><span className="workspace-avatar">N</span><span className="workspace-meta"><b>Northstar workspace</b><small>Local environment</small></span></div>
      <button className="sidebar-search" onClick={() => { setCommandOpen(true); setCommandQuery(''); }}><Search size={15}/><span>Quick find</span><kbd>⌘ K</kbd></button>
      <nav className="primary-nav" aria-label="Primary navigation">{groups.map(group => <div className="nav-group" key={group.label}><div className="nav-label">{group.label}</div>{group.items.map(item => { const Icon = item.icon; return <button key={item.id} title={collapsed ? item.label : undefined} className={`nav-item ${page === item.id ? 'active' : ''}`} onClick={() => { setPage(item.id as Page); setMenuOpen(false); }}><Icon size={17}/><span>{item.label}</span></button>; })}</div>)}</nav>
      <div className="sidebar-bottom"><div className="safety-note"><span className="safety-icon"><Shield size={15}/></span><div><b>Safety controls active</b><small>Server-side policy enforced</small></div></div><div className="profile-row"><span className="profile-avatar">NG</span><span className="profile-name"><b>Naman Gupta</b><small>Operator</small></span></div></div>
    </aside>
    {menuOpen && <button className="mobile-scrim" aria-label="Close navigation" onClick={() => setMenuOpen(false)}/>}
    <div className="main-column">
      <header className="topbar"><div className="topbar-left"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMenuOpen(true)}><Menu size={18}/></button><button className="icon-button desktop-collapse" aria-label="Toggle sidebar" onClick={() => setCollapsed(!collapsed)}><PanelLeftClose size={17}/></button><div className="breadcrumbs"><span>Workspace</span><span className="crumb-slash">/</span><strong>{titles[page]}</strong></div></div><div className="topbar-right"><div className={`connection-pill ${online ? 'is-online' : health ? 'is-offline' : 'is-pending'}`}><span className="status-light"/>{online ? 'All systems operational' : health ? 'Service unavailable' : 'Checking services'}</div><span className="topbar-divider"/><div className="env-pill"><span className="env-dot"/>LOCAL</div></div></header>
      <main className="content-area" key={page}>
        {page === 'overview' && <DashboardView onNavigate={(p) => setPage(p as Page)} onSelectRun={goToRun}/>}
        {page === 'new' && <NewWorkflowView onRunCreated={(id) => goToRun(id)} onNavigateHandoff={(id) => goToRun(id, 'handoff')}/>}
        {page === 'runs' && <WorkflowRunsView initialRunId={runId} onNavigateHandoff={(id) => goToRun(id, 'handoff')}/>}
        {page === 'capabilities' && <CapabilityRegistryView/>}
        {page === 'recordings' && <RecordingExplorerView/>}
        {page === 'handoff' && <HandoffView runId={runId} onRunCompleted={() => goToRun(runId)}/>}
        {page === 'evidence' && <EvidenceView selectedRunId={runId}/>}
        {page === 'settings' && <SettingsView/>}
      </main>
      <footer className="app-footer"><span>APEX Automation <i>·</i> Computer-use orchestration</span><span><Radio size={12}/> {online ? 'API connected' : 'API status unknown'} <i>·</i> v{health?.version || '1.0.0'}</span></footer>
    </div>
    {commandOpen && <div className="modal-backdrop command-backdrop" onMouseDown={event => event.target === event.currentTarget && setCommandOpen(false)}><section className="command-modal" role="dialog" aria-modal="true" aria-label="Quick navigation"><label className="command-search"><Search size={16}/><input autoFocus placeholder="Jump to a workspace page…" value={commandQuery} onChange={event => setCommandQuery(event.target.value)}/><kbd>ESC</kbd></label><div className="command-list">{groups.flatMap(group => group.items).filter(item => item.label.toLowerCase().includes(commandQuery.toLowerCase())).map(item => { const Icon = item.icon; return <button key={item.id} onClick={() => { setPage(item.id as Page); setCommandOpen(false); }}><Icon size={16}/><span>{item.label}</span><ArrowLeftRight size={13}/></button>; })}{!groups.flatMap(group=>group.items).some(item=>item.label.toLowerCase().includes(commandQuery.toLowerCase()))&&<div className="command-empty">No workspace pages match “{commandQuery}”.</div>}</div></section></div>}
  </div>;
}
